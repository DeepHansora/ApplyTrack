# ApplyTrack — Security Plan
## Phase 0: Security Requirements and Controls

---

## 1. Threat Model

The following threats have been identified and assessed for the ApplyTrack application. Each is addressed by one or more controls described in later sections.

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **IDOR (Insecure Direct Object Reference)** — attacker guesses a UUID and accesses another user's record | Medium | High | Ownership check on every read/write/delete; 404 on mismatch |
| **Session hijacking** — attacker steals a session cookie via network or XSS | Medium | High | HttpOnly, Secure, SameSite cookie flags; short session expiry |
| **CSRF** — malicious page tricks authenticated user into submitting a form | Medium | High | Synchronizer CSRF token on all state-changing POST routes |
| **SQL injection** — attacker injects SQL via form input | Low | Critical | SQLAlchemy ORM parameterized queries; no raw SQL with user input |
| **XSS (Cross-Site Scripting)** — attacker injects malicious script into rendered pages | Medium | High | Jinja2 autoescaping; treat AI output as untrusted; CSP header |
| **Credential stuffing** — attacker uses leaked passwords to log in | High | High | Argon2id hashing (slow by design); future: rate limiting, account lockout |
| **Insecure AI output** — Gemini returns malicious or structurally invalid content | Low | Medium | Pydantic validation of all AI output; Jinja2 escaping on render |
| **Secrets exposure** — API keys or DB credentials committed to Git | Low | Critical | `.env` in `.gitignore`; `.env.example` with no real values; env vars in production |
| **Data privacy breach** — resume text or personal data exposed via logs | Medium | High | No logging of resume text, passwords, or API keys |
| **Account enumeration** — attacker discovers whether an email exists | High | Low | Generic error messages on login and register failure |
| **Privilege escalation** — user accesses admin routes | Low | High | No admin routes in initial release; `is_active` check on every request |

---

## 2. Authentication

### 2.1 Password Security

**Algorithm: Argon2id** (preferred) with bcrypt as the acceptable fallback.

**Why Argon2id?**
Argon2id is the winner of the Password Hashing Competition (2015) and is the current recommended standard for password hashing by OWASP and NIST. It is a memory-hard algorithm, meaning it requires a large and configurable amount of RAM to compute. This makes brute-force attacks extremely expensive — an attacker with specialized GPU hardware gains very little advantage because GPU cores have limited per-core memory.

**Why not MD5, SHA-1, or plain SHA-256?**
These are general-purpose cryptographic hash functions designed for speed. They can compute billions of hashes per second on modern hardware, making brute-force attacks against a stolen hash database practical within hours or days. They must never be used for password storage.

**Why not bcrypt alone?**
bcrypt is acceptable and battle-tested but is limited to 72-byte passwords and uses a CPU-only design. Argon2id is superior for new applications. If the deployment environment does not support Argon2id, bcrypt with a work factor of 12 or higher is the fallback.

**Implementation:**
- Use `passlib[argon2]` library.
- Configuration: `time_cost=2, memory_cost=65536 (64 MB), parallelism=1` (OWASP minimum; adjust based on server benchmarks).
- Hash is stored in `users.password_hash` as a string in the `$argon2id$...` format.
- Verification: `passlib.context.CryptContext.verify(plain_password, stored_hash)`.
- Never log, return in an API response, or store the plaintext password at any point.

**Password requirements (minimum):**
- Minimum 8 characters.
- Maximum 128 characters (to prevent DoS via extremely long inputs).
- No arbitrary character restrictions (allow special characters).
- These are enforced by Pydantic schema validation before the hash is computed.

---

### 2.2 Session Management

**Approach:** Database-backed server sessions with cryptographic token hashing. 
Raw session tokens are **never** stored in plaintext inside the database. If read-replicas, backups, or database dumps are exposed, attackers cannot extract reusable session credentials.

#### 1. Token Generation
- When a user logs in, the backend generates a cryptographically random 32-byte (256-bit) session token using `secrets.token_urlsafe(32)`.
- The raw token is transmitted to the browser in a strictly secured cookie.
- The server computes the `SHA-256` hexadecimal digest of the raw token (`token_hash = hashlib.sha256(raw_token.encode()).hexdigest()`).
- The `token_hash` is persisted in the `user_sessions` PostgreSQL table alongside `user_id`, `created_at`, `expires_at = now() + INTERVAL '24 hours'`, `last_activity_at = now()`, and `is_revoked = FALSE`.

#### 2. Verification on Request & Non-Reusability
- On every incoming request to protected routes, the auth dependency extracts the cookie's raw token.
- It computes `SHA-256(raw_token)` and executes an indexed lookup:
  ```sql
  SELECT s.*, u.* 
  FROM user_sessions s
  JOIN users u ON s.user_id = u.id
  WHERE s.token_hash = :hash
    AND s.is_revoked = FALSE
    AND s.expires_at > now()
    AND s.last_activity_at > now() - INTERVAL '2 hours'
    AND u.is_active = TRUE;
  ```
- **Sliding Idle Expiration Window:** If the session is valid, the dependency updates `last_activity_at = now()`. This gives the user another 2 hours of inactivity headroom without extending `expires_at`.
- **Absolute Lifetime Cap:** Under no circumstances can a session exceed its initial `expires_at` (24 hours from creation). Even if the user remains continuously active, the session terminates after 24 hours.
- **Strict Non-Reusability:** If a session fails any validation check (expired idle window, passed absolute lifetime, revoked, or non-existent), the server executes an immediate invalidation:
  ```sql
  UPDATE user_sessions SET is_revoked = TRUE WHERE token_hash = :hash;
  ```
  The browser cookie is deleted via `Set-Cookie: session=; Max-Age=0; Path=/`, and the request redirects to `/login`. Expired tokens can never be revived or reused.

#### 3. Expiration & Rolling Timeout
- **Sliding Idle Timeout (2 Hours):** 2 hours of inactivity (measured from `last_activity_at`) invalidates the session. Every successful authenticated request updates `last_activity_at = now()`.
- **Absolute Lifetime Cap (24 Hours):** The session has an absolute expiration set at creation: `expires_at = created_at + 24 hours` (`Max-Age = 86400`). Continuous activity will not extend the session beyond 24 hours. Once 24 hours elapse, the user must re-authenticate.
- **Background Cleanup:** A lightweight maintenance job purges records where `expires_at < now() - INTERVAL '7 days'`.

#### 4. Revocation & Logout Behavior
- **Single Logout (`POST /logout`):** The current session's `is_revoked` flag is set to `TRUE`, and the client-side cookie is cleared (`Max-Age=0`).
- **Global Logout / Password Reset / Deactivation:** All active sessions for `user_id` are revoked in a single query:
  ```sql
  UPDATE user_sessions SET is_revoked = TRUE WHERE user_id = :user_id;
  ```
- **Session Rotation on Privilege Change:** A new session token is always minted upon successful credential authentication to neutralize session fixation risks.

#### 5. Cookie Flags

| Flag | Value | Purpose |
|---|---|---|
| `HttpOnly` | True | JavaScript cannot read document.cookie; prevents XSS token extraction |
| `Secure` | True (production) / False (localhost dev) | Cookie sent exclusively over TLS/HTTPS encrypted connections |
| `SameSite` | `Lax` | Protects state-changing endpoints from cross-site request forgery |
| `Path` | `/` | Cookie applies to all application routes |
| `Max-Age` | 86400 (24 hours) | Sets browser cookie expiry |

---

### 2.3 CSRF Protection

**Pattern: Synchronizer Token Pattern**

1. When a user loads a form page (GET), the server generates a unique, random CSRF token (cryptographically random, at least 32 bytes).
2. The token is stored in the user's server-side session.
3. The token is embedded in every form as a hidden input field: `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`.
4. When the form is submitted (POST), the server reads the token from the request body and compares it to the token stored in the session.
5. If the tokens do not match, the server returns a **403 Forbidden** response and does not process the request.

**Coverage:** Every state-changing route (all POST routes) requires CSRF validation. GET routes do not because GET requests must be idempotent (safe) and do not change state.

**Implementation:** A FastAPI dependency `verify_csrf_token(request: Request)` is added to every POST route. A Jinja2 global helper `{{ csrf_token() }}` injects the token into templates.

**Why this works:** An attacker's malicious page cannot read the CSRF token from the legitimate application's page (same-origin policy). The attacker's form submission will not include the correct token and will be rejected.

---

## 3. Authorization and Ownership

### 3.1 IDOR Prevention

**What is IDOR?**
Insecure Direct Object Reference (IDOR) is a vulnerability where an application uses a user-supplied identifier (like a URL path parameter `/applications/{id}`) to fetch a record without verifying that the requesting user is authorized to access it. An attacker who knows or guesses a valid UUID can access another user's private data.

**How UUIDs help (but are not sufficient):**
UUID v4 primary keys are randomly generated and non-sequential. An attacker cannot increment an integer (`/applications/1`, `/applications/2`) to discover records. However, UUIDs can still be leaked through browser history, logs, or screenshots. UUIDs reduce the attack surface but do not eliminate IDOR — ownership checks are always required.

**Prevention:**
Every service method that reads, updates, or deletes a record must verify ownership before returning or modifying data. The check is in the **service layer**, not in the router, because the service layer is the security boundary that all code paths must pass through.

---

### 3.2 Ownership Check Pattern

The following pseudo-code illustrates the required pattern for every service method that accesses a user-owned record:

```python
# Pattern: Fetch → Verify → Proceed or Raise
# Applied in every service method that reads, updates, or deletes a record.

def get_application(db: Session, application_id: UUID, current_user: User) -> Application:
    # 1. Fetch the record by its primary key
    application = application_repository.get_by_id(db, application_id)

    # 2. Handle not found
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    # 3. Verify ownership — compare record's user_id to current user's id
    if application.user_id != current_user.id:
        # Return 404, not 403, to avoid confirming the record exists
        raise HTTPException(status_code=404, detail="Application not found")

    # 4. Proceed — ownership confirmed
    return application
```

**Why 404 and not 403?**
Returning 403 ("Forbidden") confirms to the attacker that the record exists but they cannot access it. Returning 404 ("Not Found") reveals nothing about whether the record exists for another user. This is a standard defensive pattern.

**For records without a direct user_id (interviews, status history):**

```python
def get_interview(db: Session, interview_id: UUID, current_user: User) -> Interview:
    # Join through applications to get the owning user_id
    interview = interview_repository.get_by_id_with_application(db, interview_id)

    if interview is None:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Verify ownership through the parent application
    if interview.application.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Interview not found")

    return interview
```

---

### 3.3 Cross-User Isolation

Every database query that returns a list must include a `user_id` filter. Never query a table by other criteria alone and return results.

**Correct:**
```python
# Always scope to the current user
applications = db.query(Application).filter(
    Application.user_id == current_user.id,
    Application.status == status_filter
).all()
```

**Wrong (never do this):**
```python
# Missing user_id filter — returns all users' matching applications
applications = db.query(Application).filter(
    Application.status == status_filter
).all()
```

This rule applies to every list query in every repository. Authorization tests must verify that User A's queries never return User B's records, even with valid filters.

---

## 4. Input Validation

### Primary Validation Layer: Pydantic v2

All incoming data (form submissions, query parameters) is validated by a Pydantic schema before reaching the service layer. Invalid input is rejected at the boundary with a 422 response or a re-rendered form with field-level errors.

### Field Length Limits

| Field Type | Maximum Length | Notes |
|---|---|---|
| Full name | 255 characters | VARCHAR(255) in DB |
| Email address | 320 characters | RFC 5321 maximum |
| Password (input) | 128 characters | Before hashing; to prevent DoS |
| Company name | 255 characters | |
| Job title | 255 characters | |
| Resume title | 255 characters | |
| Resume version | 100 characters | |
| URL fields | 2048 characters | |
| Industry, location, currency | 100 characters | |
| Notes, job description, resume text | 50,000 characters | Pydantic MaxLen; balance usability vs. abuse |
| Gemini input (resume text) | 10,000 characters | AI input limit enforced before API call |
| Gemini input (job description) | 5,000 characters | AI input limit enforced before API call |
| Reminder title | 255 characters | |
| Notification title | 255 characters | |
| Round name | 255 characters | |
| AI error message | 1,000 characters | Truncated safe summary |

### URL Validation

All URL fields (`job_url`, `external_url`, `website`, `meeting_url`) are validated with Pydantic's `AnyHttpUrl` or `HttpUrl` type, which requires a valid scheme (`http://` or `https://`) and a parseable URL structure.

### Enum Validation

All enumerated fields (`status`, `employment_type`, `interview_type`, `analysis_type`) are validated using Python `Enum` types in Pydantic schemas. Any value not in the allowed set is rejected.

### Date/Time Validation

All date/time fields accept ISO 8601 format strings from forms. The service layer converts them to UTC-aware datetime objects. Future-only fields (e.g., `due_at` for reminders) are validated to be in the future at creation time (warning, not error, to allow backdating if needed).

---

## 5. Database Security

### Parameterized Queries via SQLAlchemy ORM

All database interactions use the SQLAlchemy ORM. ORM queries are parameterized by design — user input is never interpolated directly into SQL strings. This prevents SQL injection.

**Never use:**
```python
# DANGEROUS — SQL injection vulnerability
db.execute(f"SELECT * FROM applications WHERE job_title = '{user_input}'")
```

**Always use:**
```python
# SAFE — parameterized via ORM
db.query(Application).filter(Application.job_title == user_input).all()
```

If raw SQL is ever necessary (e.g., complex analytics queries), use SQLAlchemy's `text()` with bound parameters:
```python
db.execute(text("SELECT * FROM applications WHERE job_title = :title"), {"title": user_input})
```

### Least-Privilege Database User

The application connects to PostgreSQL as a dedicated user (e.g., `applytrack_app`) that has the minimum required permissions:
- `SELECT`, `INSERT`, `UPDATE`, `DELETE` on application tables.
- `USAGE` on sequences.
- No `CREATE`, `DROP`, `ALTER`, or superuser privileges.

Alembic migrations run as a separate privileged user (not the application user).

### Connection Security

- PostgreSQL connection uses SSL in production (`sslmode=require` in the connection string).
- Database credentials are stored in environment variables only; never hardcoded.

---

## 6. AI Security

### 6.1 Treating Gemini Output as Untrusted

The response from Google Gemini is external data and must be treated with the same skepticism as user input. Even well-intentioned AI models can produce unexpected, malformed, or potentially harmful output.

**Controls:**
1. **Pydantic validation:** Every Gemini response is parsed against a strict Pydantic schema (`ResumeAnalysisResult` or `JobMatchResult`). If the response does not conform to the schema, the analysis is marked `failed` and an error message is shown. The raw response is not saved or displayed.
2. **Jinja2 autoescaping:** All AI output displayed in templates passes through Jinja2's autoescaping, which converts `<`, `>`, `"`, `'`, and `&` to HTML entities. AI-generated text cannot inject HTML or JavaScript into pages.
3. **No `Markup()` wrapping:** Never wrap AI output in Jinja2's `Markup()` class, which bypasses autoescaping.
4. **Response size limit:** Reject Gemini responses that exceed a defined maximum size (e.g., 50 KB) to prevent memory exhaustion.

### 6.2 Consent Requirements

Before the first AI analysis, the user must explicitly acknowledge that:
- Their resume text and job description will be transmitted to Google's Gemini API.
- Google's data handling policies apply to transmitted content.
- The analysis result is not a prediction of hiring outcomes.

Consent is recorded as `users.ai_consent = TRUE` and `users.ai_consent_given_at = now()`. Every AI route checks for consent before proceeding. If consent is not given, the user is redirected to the consent page.

### 6.3 Data Minimization

- Send only the necessary text to Gemini: resume text and/or job description.
- Never include the user's name, email, password hash, session token, or unrelated personal data in Gemini prompts.
- Resume text is stored in `resumes.resume_text` only when the user explicitly pastes it. It can be deleted independently of the resume record.
- Gemini API calls include only structured prompts with the minimum necessary text.

### 6.4 Input Size Limits

Before calling the Gemini API, validate input sizes in the service layer:
- Resume text: maximum 10,000 characters.
- Job description: maximum 5,000 characters.

If limits are exceeded, return a validation error to the user without calling the API. These limits protect against API cost overruns and are enforced independently of Pydantic field-level limits.

---

## 7. Secret Management

### Local Development

1. Copy `.env.example` to `.env`.
2. Fill in real values in `.env` (database URL, Gemini API key, session secret).
3. `.env` is listed in `.gitignore` and must never be committed to version control.

### Environment Variables Required

| Variable | Description | Example Value (from .env.example) |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:password@localhost:5432/applytrack` |
| `SECRET_KEY` | Session signing secret (minimum 32 random bytes) | `your-secret-key-here` |
| `GEMINI_API_KEY` | Google Gemini API key | `your-gemini-api-key-here` |
| `GEMINI_MODEL` | Gemini model identifier | `gemini-3.8-flash` |
| `ENVIRONMENT` | `development` or `production` | `development` |
| `DEBUG` | Enable debug mode | `False` |

### Production Deployment

- All secrets are set as environment variables in the deployment platform (Railway / Render).
- Secrets are never baked into Docker images or committed to source control.
- The `.env.example` file contains only placeholder values and explanatory comments.

### Secret Rotation

- If the `GEMINI_API_KEY` or `SECRET_KEY` is ever exposed, rotate it immediately via the respective platform (Google Cloud Console / deployment platform).
- After rotating `SECRET_KEY`, all existing sessions are invalidated (users must log in again).

---

## 8. Logging Security

Logging is essential for debugging and incident response, but log files must never contain sensitive data.

| Category | Log? | Why |
|---|---|---|
| Successful login (user ID, timestamp) | Yes | Audit trail |
| Failed login attempt (email, timestamp) | Yes | Detect credential stuffing |
| Session created / destroyed | Yes | Audit trail |
| Application created / status changed | Yes | Activity log |
| Gemini API call initiated (user ID, analysis type, model) | Yes | Usage and cost tracking |
| Gemini API error (error type, status code) | Yes | Debugging |
| Authorization failure (user ID, resource ID, action) | Yes | Security monitoring |
| Unhandled exceptions (stack trace) | Yes | Debugging |
| **Plaintext passwords** | **Never** | Obvious security risk |
| **Password hashes** | **Never** | Hash is equivalent to password |
| **Session tokens / cookie values** | **Never** | Equivalent to credentials |
| **Full resume text** | **Never** | Private personal data |
| **Full job descriptions** | **Never** | May contain confidential info |
| **Gemini API keys** | **Never** | Would expose the key |
| **Full Gemini prompt text** | **Never** | Contains private resume content |
| **Full Gemini raw response** | **Never** | May contain echoed sensitive data |
| Email addresses in bulk | Avoid | Log user ID instead where possible |

**Implementation:** Use Python's standard `logging` module with structured log formatting. Configure log levels: `DEBUG` in development, `INFO` in production. Use a log sanitizer or a custom formatter to redact known sensitive fields before they reach the log handler.

---

## 9. Output Security

### Jinja2 Autoescaping

Jinja2's autoescaping is enabled globally for all `.html` templates. This means every `{{ variable }}` expression is automatically HTML-escaped before rendering. Characters that have special meaning in HTML (`<`, `>`, `&`, `"`, `'`) are replaced with their HTML entity equivalents, preventing stored or reflected XSS.

**Configuration in FastAPI:**
```python
from jinja2 import Environment, FileSystemLoader, select_autoescape

env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(["html", "xml"])  # Autoescaping enabled
)
```

### Never Bypass Autoescaping for Untrusted Content

The `Markup()` function and the `| safe` Jinja2 filter disable autoescaping. These must only be used for content that is known to be safe (e.g., static HTML written by the developer, not data from the database or from Gemini).

**Wrong (XSS vulnerability if AI returns script tags):**
```html
{{ analysis.result.summary | safe }}
```

**Correct:**
```html
{{ analysis.result.summary }}
```

### Trusted vs. Untrusted Content

| Content Source | Trust Level | Rendering |
|---|---|---|
| Static HTML in template files | Trusted | Use `| safe` only where necessary |
| User-entered text (from DB) | Untrusted | Always autoescaped `{{ }}` |
| Gemini AI output (from DB) | Untrusted | Always autoescaped `{{ }}` |
| Status labels (from Python Enum) | Trusted | Can use `| safe` for badge HTML |
| Pagination / sort links (from request params) | Untrusted | Always escape; validate before use |

### Content Security Policy (CSP) Header

Add a `Content-Security-Policy` HTTP response header to restrict what resources the browser can load. For Phase 1 (no inline JavaScript, no external scripts):

```
Content-Security-Policy: default-src 'self'; script-src 'none'; style-src 'self';
```

Adjust as custom fonts (Clash Display, Satoshi) are loaded from a CDN or self-hosted.

---

## 10. Security Checklist by Phase

| Phase | Security Tasks |
|---|---|
| **Phase 0** | Define threat model; agree on password hashing algorithm; define CSRF approach; define ownership check pattern; document all sensitive fields |
| **Phase 1** | Configure Jinja2 autoescaping; add CSP header; create `.env.example`; add `.env` to `.gitignore`; set up `Secure`, `HttpOnly`, `SameSite` cookie flags |
| **Phase 2** | Implement Argon2id hashing; implement CSRF token generation and validation; implement `get_current_user` dependency; implement session rotation on login; test generic error messages on login failure; test unauthenticated redirect |
| **Phase 3** | Implement ownership checks in company and application services; test cross-user isolation for companies and applications; test that `company_id` belongs to the current user |
| **Phase 4** | Ensure status history transaction cannot be partially committed; test rollback scenarios |
| **Phase 5** | Implement ownership check for interview routes (via application JOIN); validate resume ownership when associating with application; test cross-user resume/interview access |
| **Phase 6** | Implement consent gate before AI routes; validate Gemini output with Pydantic; set input size limits; test malformed response handling; never log resume text or API key; test AI output escaping in templates |
| **Phase 7** | Verify all analytics queries are scoped to `current_user.id`; no aggregate queries that leak cross-user data |
| **Phase 8** | Enable `Secure` cookie flag in production config; add rate limiting (future); add Content-Security-Policy header; run OWASP ZAP or similar baseline scan; review all TODO security comments; set database SSL; document security decisions in README |

---

## 11. Future Security Considerations

The following controls are out of scope for the initial release but should be planned for future phases:

| Control | Description | Priority |
|---|---|---|
| **Rate limiting** | Limit login attempts per IP per minute to prevent credential stuffing (e.g., 5 attempts per minute, 20 per hour) | High |
| **Account lockout** | Lock account after N consecutive failed login attempts; require email verification or time-based unlock | High |
| **Two-factor authentication (2FA)** | TOTP-based 2FA (Google Authenticator compatible) for accounts handling sensitive career data | Medium |
| **Structured audit log** | Append-only audit log table recording all authentication, authorization, and data-change events with timestamps and IP addresses | Medium |
| **Email verification** | Verify email address at registration before granting full access | Medium |
| **Password reset** | Secure email-based password reset flow using time-limited, single-use tokens | High (pre-launch) |
| **Session concurrent access control** | Allow only one active session per user; invalidate old sessions on new login | Low |
| **Dependency vulnerability scanning** | Run `pip-audit` or `safety` as part of CI pipeline on every push | High |
| **Secrets scanning** | Add `gitleaks` or GitHub secret scanning to repository to prevent accidental key commits | High |
