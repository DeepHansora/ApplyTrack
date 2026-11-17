# ApplyTrack — Development Plan
## Phase 0: Planning Complete

---

## 1. Open Decisions — Proposed Defaults

The following eight open decisions were identified in the original documentation. Each is resolved with a recommended default, rationale, and impact on schema and code.

---

### Decision 1: Account Deletion, Retention, and Compliance Limitations

**Question:** Is account deletion supported in the first release, and what happens to related records?

**Recommended default:**
Implement a two-tiered account deactivation and deletion model, with clearly documented technical and operational limitations:

1. **Soft-Deactivation (Immediate Access Cutoff):**
   - Sets `users.is_active = FALSE`.
   - Revokes all sessions in `user_sessions` immediately (`is_revoked = TRUE`).
   - The user cannot log in, and all private records are hidden from standard application queries.
2. **30-Day Grace Period Behavior:**
   - Deactivated accounts retain their database records for an intentional **30-day grace period**.
   - During these 30 days, users who accidentally deactivated their accounts or changed their minds can contact support to reactivate their job hunt history.
   - While in grace-period deactivation, the data is dormant, inaccessible via web sessions, and locked.
3. **Permanent Deletion (Self-Service or Scheduled Purge):**
   - A dedicated self-service route (`POST /profile/delete`) with two-step confirmation (the user must explicitly type their account email address to confirm).
   - Alternatively, when the 30-day grace period lapses, a scheduled batch job flags the account for hard-purge.
   - Upon execution, `DELETE FROM users WHERE id = ?` cascades across all tables (`companies`, `applications`, `interviews`, `resumes`, `reminders`, `notifications`, `ai_analyses`), permanently removing all live database rows.
4. **Backup Retention & Compliance Limitations (Important Disclosure):**
   - **No software implementation alone guarantees legal GDPR compliance.** Application-level cascading deletes only remove data from the *live active database*.
   - Managed PostgreSQL database services (Railway, Render, AWS RDS) maintain automated Point-In-Time Recovery (PITR) snapshots and write-ahead log (WAL) archives for disaster recovery, with retention periods strictly dependent on the chosen provider and plan tier (e.g. 7 days on basic tiers, up to 14–30 days on professional plans).
   - Deleting a row from the live database does not rewrite past immutable backup snapshots. If a disaster recovery restore is ever performed, an audit log of purged account IDs must be re-executed against the restored database to prevent zombie data restoration.

**Rationale:**
Accidental account deletion is catastrophic for a job-search tool — a user might lose months of records if deactivation were immediately irreversible without warning. A 30-day grace period prevents accidental loss. Providing an explicit, self-service hard-delete option satisfies user privacy rights on the live system, while acknowledging the operational realities of encrypted disaster recovery backups.

**Impact on schema / code:**
- `users.is_active` tracks soft-deactivation.
- `user_sessions` allows immediate revocation of all active sessions on deactivation.
- The `get_current_user` dependency checks `is_active = TRUE` on every request.
- Hard-delete: executes `DELETE FROM users WHERE id = ?`, with PostgreSQL `ON DELETE CASCADE` propagating across all owned child tables.
- Route: `POST /profile/delete` with two-step CSRF-protected form confirmation.
- Phase 8 task: document backup retention policy and restore checklist in `README.md`.

---

### Decision 2: Application Deletion

**Question:** Are applications archived by default or hard-deleted with explicit confirmation?

**Recommended default:**
Soft-archive by default using the `is_archived` flag. Applications that have any `application_status_history` entries should never be silently hard-deleted — they represent real job search activity. Provide a hard-delete option only for `Saved` status applications with zero history entries (i.e., the application was created but never formally submitted) with explicit UI confirmation. For all other applications, archive is the only option in the initial release.

**Rationale:**
An application that has been progressed through status transitions is a meaningful historical record. Users will want to review past rejections and withdrawn applications for analytics, reflection, and future reference. Accidental deletion of an interview-stage or offer-stage application would be irreversible and frustrating. The exception for `Saved` applications is pragmatic — these are draft records with no history, so deleting them is low-risk.

**Impact on schema / code:**
- `applications.is_archived` already in schema.
- Service layer: `application_service.delete()` checks status == `Saved` AND no `application_status_history` rows; raises a business logic error otherwise.
- UI: Show "Archive" button always; show "Delete" button only on `Saved` applications with no history.

---

### Decision 3: Flexible Application Lifecycle & Status Transition Rules

**Question:** What are the exact allowed status transitions, and how can users correct accidental status changes without artificial friction?

**Recommended default:**
Implement a flexible state machine that accommodates actual job-search realities without forcing users through unnecessary intermediate stages:
- `Saved` → `Applied`, `Interview`, `Offer`, `Withdrawn` (supports retroactively recording jobs already in interview or offer stages)
- `Applied` → `Assessment`, `Interview`, `Offer`, `Rejected`, `Withdrawn` (supports direct invitations to interview or immediate offers)
- `Assessment` → `Interview`, `Offer`, `Rejected`, `Withdrawn`
- `Interview` → `Offer`, `Rejected`, `Withdrawn`
- `Offer` → `Withdrawn`, `Rejected` (candidate declined offer, or offer was rescinded)
- `Rejected` → _(terminal state for standard forward flow)_
- `Withdrawn` → _(terminal state for standard forward flow)_

**Corrections & Correction Notes:**
Any status entry made by mistake (such as accidentally selecting `Rejected` when an `Interview` invite was received) is resolved using the dedicated "Correct Status" action (`POST /applications/{id}/status/correct`):
- Requires a mandatory correction note explaining the circumstance.
- Sets `is_correction = TRUE` on `application_status_history`.
- Bypasses directional state machine gates while preserving full historical transparency.

**Rationale:**
Job hunts do not follow a rigid linear conveyor belt. Companies frequently bypass written assessments and schedule interviews immediately, or present offers based on referrals. Forcing users to click through artificial intermediate states (e.g. forcing an `Applied` job through a fake `Assessment` step just to reach `Interview`) pollutes metrics and degrades usability. The correction route with mandatory explanation ensures data integrity when human mistakes occur.

**Impact on schema / code:**
- `application_status_history.is_correction BOOLEAN NOT NULL DEFAULT FALSE`.
- `application_status_history.notes TEXT NOT NULL` for corrections (mandatory check in service layer).
- `lifecycle_service.py`: validates against `ALLOWED_TRANSITIONS` mapping for standard progressions, while routing corrections through audit-logged correction handling.
- Timeline template visually annotates correction entries with a distinct badge.

---

### Decision 4: Resume Text Retention

**Question:** Is resume text retained after AI analysis, and can it be deleted independently?

**Recommended default:**
`resume_text` on the `resumes` table is nullable. It is populated only when the user explicitly pastes text. The user can delete the text independently (set to `NULL`) without deleting the resume record via the "Clear Pasted Text" action. AI analysis results in `ai_analyses.result` can be deleted independently via the "Delete Analysis" action. The two are stored separately, so clearing resume text does not delete analysis results, and deleting an analysis does not clear resume text.

**Rationale:**
Users may paste resume text for a one-time analysis and then prefer not to retain it in the application (privacy preference). Independently deletable text gives users control over their data. Retaining the analysis result separately means the structured output remains useful even after the source text is cleared.

**Impact on schema / code:**
- `resumes.resume_text TEXT NULL` — already defined in schema.
- Service: `resume_service.clear_text(resume_id, current_user)` sets `resume_text = NULL`.
- Service: `ai_analysis_service.delete(analysis_id, current_user)` deletes the `ai_analyses` row.
- UI: "Clear Pasted Text" button on resume detail page; "Delete Analysis" button on analysis detail page.

---

### Decision 5: Gemini Model

**Question:** Which Gemini model/version and API configuration should be used?

**Recommended default:**
Use `gemini-3.8-flash` as the proposed current default model (retiring legacy `gemini-1.5-flash`). Configure the model name via the `GEMINI_MODEL` environment variable so it can be changed without a code deployment. Store the `model_name` value in every `ai_analyses` record so historical results are auditable and comparable if the model changes. Set a request timeout of 30 seconds. Review Google's current API terms and data handling settings before go-live.

**Rationale:**
`gemini-3.8-flash` represents the active, modern tier for rapid and cost-effective text analysis tasks of the scale this application generates (resume text + job description). The environment variable allows switching between models seamlessly without code changes. Storing `model_name` per analysis is essential for understanding why results may differ between analyses if the model is changed.

**Impact on schema / code:**
- `ai_analyses.model_name VARCHAR(100)` already in schema.
- `app/config.py`: add `GEMINI_MODEL: str = "gemini-3.8-flash"`.
- `app/integrations/gemini.py`: read `settings.GEMINI_MODEL`; set `timeout=30`.
- `ai_service.py`: pass `settings.GEMINI_MODEL` to the client; store in `ai_analyses.model_name`.

---

### Decision 6: Match Score Methodology, Rubric Isolation, and Fairness

**Question:** How are match scores calculated and explained, and how do we prevent double-counting skills and keywords?

**Recommended default:**
Use a transparent, rubric-based **Compatibility Score** (0–100) with four isolated categories:

| Category | Maximum Points | Exact Scope & Calculation Criteria | Fairness & Deduplication Rule |
|---|---|---|---|
| **Skills match** | 40 | Discrete hard and soft technical competencies (e.g. Python, SQL, Docker, FastAPI). | Evaluates verifiable capabilities. **Non-Double-Counting:** Any competency credited here is strictly excluded from Keyword Alignment. |
| **Experience relevance** | 30 | Years in role, career seniority level, and domain depth (e.g. backend web development vs embedded systems). | Assesses scope of responsibility and practical experience. If a job does not mandate minimum years of experience, full base credit is awarded. |
| **Education fit** | 10 | Academic degrees, relevant certifications, or formal coursework. | **Fairness Rule:** If a job description marks education as optional or leaves it unstated, candidates receive full credit (10/10). No zero-floor penalties are applied for absent graduation years. |
| **Keyword alignment** | 20 | Contextual industry phrasing, tooling ecosystem terminology, and workflow methodologies (e.g. "RESTful architecture", "TDD", "microservices principles", "Agile sprint cycles"). | **Strict Boundary:** Focuses exclusively on domain vocabulary and working methodology language that does not qualify as a standalone skill. |

Label the total as **"Compatibility Score"** — never "probability of hire", "hire likelihood", "match probability", or any phrase implying predictive recruitment outcome. Display the breakdown panel showing each category's score alongside a one-sentence rationale.

**Rationale:**
Opaque percentage scores deceive users into believing AI models can predict hiring decisions. Clear rubric breakdowns offer constructive feedback: users can see whether their gap is missing terminology or genuine skill misalignment. Rigorous deduplication prevents inflating or deflating scores based on duplicate keyword hits.

**Impact on schema / code:**
- `ai_analyses.result` JSONB stores `compatibility_score` integer and `score_breakdown` object.
- Pydantic `JobMatchResult.score_breakdown` validates each category is within range, and asserts `compatibility_score == sum(breakdown)`.
- Gemini prompt includes explicit negative constraints instructing the model to separate technical tools from methodology language and to give neutral credit to unstated educational criteria.

---

### Decision 7: Notification Generation

**Question:** Are notifications generated on page load or by a background process?

**Recommended default:**
Generate notifications synchronously at the moment of the creating action. When a reminder is created, also `INSERT` a notification record in the same database transaction. On authenticated page load, query and display unread notification counts in the navigation header. No background jobs, cron processes, or Celery in the initial release. 

**Database-Enforced Deduplication:**
Rather than relying solely on service-layer checks, the database enforces deduplication via a partial unique constraint:
```sql
CREATE UNIQUE INDEX uq_notifications_reminder_unread 
ON notifications (source_reminder_id) 
WHERE read_at IS NULL;
```
If a reminder is edited or submitted concurrently, database-level deduplication prevents duplicate unread alerts from polluting the user's dashboard.

**Rationale:**
Background job infrastructure (Celery, Redis, cron) adds significant operational complexity. For the initial release, synchronous notification creation during the action that triggers it is simpler, testable, and sufficient for in-app-only notifications. The user gets immediate feedback because the notification appears as soon as they create the reminder. Deduplication prevents the notification table from accumulating duplicate records if a reminder is edited or the creation action is retried.

**Impact on schema / code:**
- `notifications.source_reminder_id UUID NULL FK → reminders.id` — already in schema.
- `notification_service.create_for_reminder(db, reminder, user)`: checks for existing unread notification for `source_reminder_id` before inserting.
- `reminder_service.create(...)`: calls `notification_service.create_for_reminder(...)` within the same `db` transaction.
- `base.html` / nav partial: queries `SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL` on each authenticated page load.

---

### Decision 8: Final Deployment Target & Managed PostgreSQL Recommendation

**Question:** What is the final deployment target, budget, and production database hosting?

**Recommended default:**
Target **Railway** or **Render** as the primary PaaS deployment platform, with managed PostgreSQL:

1. **Platform Recommendation:**
   - **Primary Recommendation:** **Railway** (or alternatively Render). Both provide native Dockerfile deployment, automated SSL certificates, intuitive environment variable secret injection, and tightly integrated managed PostgreSQL services.
   - **Managed Database Service:** Utilize the platform's managed PostgreSQL addon (PostgreSQL 16+). This provides automated connection string provisioning (`DATABASE_URL`), TLS encryption in transit (`sslmode=require`), connection pooling, automated point-in-time snapshots, and zero infrastructure maintenance overhead.
2. **Containerization Strategy:**
   - Package the application via a multi-stage `Dockerfile` based on `python:3.12-slim`.
   - Local development leverages `docker-compose.yml` spinning up a local PostgreSQL container alongside the FastAPI live-reload server.
3. **CI/CD Pipeline:**
   - GitHub Actions workflow executes tests (`pytest`) and linting on every push to `main` and active feature branches.
   - Successful builds automatically trigger deployment on Railway/Render.
4. **Configuration & Secrets:**
   - Zero hardcoded credentials; `app/config.py` loads `DATABASE_URL`, `SECRET_KEY`, and `GEMINI_API_KEY` from runtime environment variables.
   - Full setup and migration instructions documented in `README.md`.

---

## 2. Technology Rationale (Beginner-Friendly)

### Why FastAPI instead of Django or Flask?

**FastAPI** is a modern Python web framework designed from the ground up to work with Python type hints. It is fast (one of the fastest Python frameworks, comparable to Node.js), and it integrates Pydantic for automatic data validation and OpenAPI documentation generation out of the box.

**Django** is a "batteries-included" full-stack framework with an ORM, an admin panel, and a large ecosystem. It is excellent for content-heavy applications but carries "magic" — hidden behavior and conventions that can be difficult to understand when learning. Its ORM and validation system are separate from FastAPI's ecosystem.

**Flask** is a minimal micro-framework that gives you maximum control. However, that means you must add your own validation library, ORM adapter, and authentication system. For a project that benefits from Pydantic's integration with the validation layer, Flask requires more boilerplate.

**Why FastAPI for ApplyTrack?** The project benefits from FastAPI's dependency injection system (clean authentication dependencies), Pydantic-native validation (the same library used for AI output validation), async readiness (future-proof), and clear separation of concerns that naturally maps to the router/service/repository architecture.

---

### Why PostgreSQL instead of SQLite or MySQL?

**PostgreSQL** is a mature, production-grade relational database. For ApplyTrack specifically, two PostgreSQL features are essential: **JSONB** (binary JSON storage, used for `ai_analyses.result` — allows indexing and querying within the JSON structure) and **robust UUID support** (`gen_random_uuid()` built in, UUID as a first-class column type).

**SQLite** stores the entire database in a single file, which makes it perfect for development and testing but unsuitable for production because it uses file-level locking (only one write at a time), has limited concurrency, and lacks server-side features like JSONB. We use SQLite in tests for speed.

**MySQL** is a solid production database but historically lacked some PostgreSQL features we need: JSON column support is less powerful than JSONB, UUID handling requires workarounds, and certain constraint types are less expressive. PostgreSQL is the preferred choice for new Python web applications.

---

### Why SQLAlchemy 2.x and Alembic?

**SQLAlchemy** is Python's most widely used Object-Relational Mapper (ORM). It maps Python classes to database tables, so instead of writing SQL strings, you define Python models and query them using Python syntax. SQLAlchemy handles translating your Python queries to parameterized SQL, managing database connections, and handling transactions.

**Version 2.x** introduces a cleaner, more explicit query API (the `select()` construct) compared to version 1.x's implicit session queries. It better supports async patterns and is more aligned with modern Python type hints.

**Alembic** is SQLAlchemy's official database migration tool. When you change a SQLAlchemy model (add a column, rename a table, add an index), Alembic generates a migration script that describes the change. Running `alembic upgrade head` applies all pending migrations to the database in order. This means the database schema is version-controlled alongside the application code — the team can always reproduce the exact database schema for any point in history.

---

### Why Pydantic v2?

**Pydantic** validates data using Python type hints. You declare a class with typed fields, and Pydantic automatically validates that incoming data matches those types, enforcing required fields, length limits, and custom validators. Invalid data raises a detailed `ValidationError`.

**Version 2** (released 2023) rewrote Pydantic's core in Rust, making it 5–50x faster than v1 for typical workloads. The API is largely compatible but cleaner. It is FastAPI's native validation layer — when you declare a Pydantic model as a request body in a FastAPI route, FastAPI automatically validates the incoming request against it and returns a 422 error for invalid input.

For ApplyTrack, Pydantic plays two roles: (1) validating user input from forms and (2) validating Gemini API output — the same library guards both the entry and exit points of the application.

---

### Why server-rendered Jinja2 instead of React or HTMX?

**React** (and other SPA frameworks) require a JavaScript build pipeline (Node.js, npm/pnpm, bundler), a separate API layer, and client-side state management. They are powerful for highly interactive applications but add significant complexity to build, test, and deploy. They are also significantly more code to write and understand.

**HTMX** adds dynamic HTML fragment replacement via custom HTML attributes. It is lightweight and pairs well with server-rendered pages. It is excluded from the initial release because it adds JavaScript complexity and debugging surface area. It may be introduced in a later phase with a demonstrated justification.

**Jinja2 with standard HTML forms** is the simplest possible architecture: the server renders complete HTML pages, the browser submits standard forms, and the server processes them and redirects. No JavaScript build step, no client-side state, no API version compatibility concerns. The application works with JavaScript disabled. This simplicity means the developer can focus on backend engineering quality — the stated primary goal — rather than frontend framework mechanics. The design system is rich and visually refined through CSS alone.

---

### Why Argon2id for passwords?

**Argon2id** is the winner of the Password Hashing Competition (2015) and is the current recommended algorithm by OWASP and NIST for storing passwords. It is "memory-hard": it requires a large, configurable amount of RAM to compute one hash. This is intentional — it makes brute-force attacks expensive even on specialized hardware.

**Bcrypt** (the previous standard) is CPU-hard but not memory-hard. It is still acceptable and widely supported but newer than Argon2id in security recommendations. If the deployment environment does not support Argon2id, bcrypt with work factor ≥ 12 is the fallback.

**MD5 and SHA-1** are general-purpose cryptographic hash functions designed for speed — specifically for hashing large files quickly. They can compute billions of hashes per second on modern GPUs. If a password database is stolen and the passwords are hashed with MD5 or SHA-1, the attacker can crack most passwords within hours or days using a rented GPU cluster. They must never be used for passwords.

**SHA-256** (even with a salt) is also not suitable for passwords for the same reason — it is designed to be fast. Only algorithms specifically designed to be slow and memory-hard (Argon2id, bcrypt, scrypt) are appropriate for password storage.

---

### Why UUIDs instead of integer IDs?

**Integer IDs** (`1, 2, 3, ...`) are sequential and predictable. An attacker who discovers one valid record ID can enumerate others by incrementing: if `/applications/42` works, try `/applications/43`, `/applications/44`, etc. Even with authorization checks, this reveals whether records exist for other users (information leakage).

**UUID v4** is a 128-bit randomly generated identifier. It is effectively non-guessable: the probability of randomly guessing a valid UUID from the 2^122 possible values is negligible. This means an attacker cannot enumerate records by guessing IDs. URLs like `/applications/f47ac10b-58cc-4372-a567-0e02b2c3d479` reveal nothing.

**UUIDs do not replace authorization checks.** A leaked UUID (e.g., via browser history) can still be used to attempt access. Every service method must still verify that the requesting user owns the record. UUIDs are a defense-in-depth measure, not the primary authorization mechanism.

**Trade-off:** UUIDs are larger (16 bytes vs 4 bytes for a 32-bit integer), slightly slower to index in PostgreSQL, and harder to read/debug. For this application, the security benefit is worth the performance cost. PostgreSQL handles UUID indexes efficiently with B-tree indexes.

---

## 3. All Phases with Tasks and Exit Criteria

---

### Phase 0 — Requirements and Design Freeze (Current Phase)

**Goal:** Complete all planning artifacts before writing application code.

**Task List:**
- [ ] Write `docs/REQUIREMENTS.md` — full functional requirements, acceptance criteria, identified gaps.
- [ ] Write `docs/DATABASE_DESIGN.md` — full schema, ER diagram, state machine, ownership rules, deletion policy.
- [ ] Write `docs/ROUTE_MAP.md` — all routes, methods, templates, CSRF requirements.
- [ ] Write `docs/SECURITY_PLAN.md` — threat model, authentication, authorization, input validation, logging.
- [ ] Write `docs/DEVELOPMENT_PLAN.md` — open decisions resolved, tech rationale, all phases, Git workflow, dev setup, interview guide.
- [ ] Resolve all 8 open decisions with proposed defaults and document their schema/code impact.
- [ ] Define the formal status state machine.
- [ ] Define the JSONB schema for `ai_analyses.result`.
- [ ] Review all identified gaps and confirm resolution approach.
- [ ] Confirm technology stack and project structure.

**Exit Criteria:**
- All five planning documents are written and reviewed.
- No unresolved decision blocks foundation work.
- Schema is validated against all functional requirements.
- State machine is formally defined.
- Project structure is agreed upon.

---

### Phase 1 — Foundation and Shared Design System

**Goal:** Working application skeleton with shared design language, database connection, and health check.

**Task List:**
- [ ] Initialize Git repository; create `.gitignore` (include `.env`, `__pycache__`, `.pytest_cache`, `*.pyc`).
- [ ] Create Python 3.12 virtual environment; install dependencies; write `requirements.txt`.
- [ ] Create `app/config.py` with Pydantic `BaseSettings` reading from `.env`.
- [ ] Create `.env.example` with all required variables and placeholder values.
- [ ] Set up `app/database.py` with SQLAlchemy engine and session factory.
- [ ] Create `app/main.py` with FastAPI application factory.
- [ ] Add `GET /health` endpoint returning JSON `{"status": "ok", "database": "connected"}`.
- [ ] Initialize Alembic (`alembic init migrations`); configure `env.py` to use app models.
- [ ] Create all SQLAlchemy model files in `app/models/`.
- [ ] Write initial Alembic migration creating all 10 tables.
- [ ] Run `alembic upgrade head`; verify tables are created correctly.
- [ ] Create `app/dependencies.py` with database session dependency.
- [ ] Design and implement CSS token system in `static/css/tokens.css` (all color, typography, spacing tokens as CSS custom properties).
- [ ] Create `static/css/base.css` (reset, body, typography, global link styles).
- [ ] Create `static/css/layout.css` (page wrapper, main content area, asymmetric grid classes).
- [ ] Create `static/css/components.css` (button styles, form field styles, table styles, status badge styles).
- [ ] Create `templates/base.html` with shared navigation, footer, flash message partial, and CSS links.
- [ ] Create `templates/partials/nav.html`, `footer.html`, `flash.html`.
- [ ] Build the landing page template (`templates/landing.html`) and `GET /` route.
- [ ] Import and self-host Clash Display and Satoshi fonts (or configure CDN fallback).
- [ ] Verify responsive layout at mobile and desktop widths.
- [ ] Run the application with `uvicorn app.main:app --reload`; confirm all pages render.

**Exit Criteria:**
- Application starts and connects to PostgreSQL without errors.
- `alembic upgrade head` runs successfully on a clean database.
- `GET /health` returns 200 with database connected.
- Landing page renders with correct fonts, colors, and layout.
- Shared design system is visually consistent with the design specification.

**Acceptance Criteria:**
- CSS tokens match the exact color values in the design system specification.
- Base template navigation is present on all pages.
- Font stack falls back gracefully if Clash Display / Satoshi are unavailable.

---

### Phase 2 — Authentication

**Goal:** Secure user registration, login, logout, and authentication dependency.

**Task List:**
- [ ] Install `passlib[argon2]` and `python-multipart`.
- [ ] Create `app/schemas/user.py` with `UserCreate`, `UserLogin`, `UserOut` Pydantic schemas (password length 8–128 chars, email validation).
- [ ] Create `app/models/user.py` SQLAlchemy model matching the schema.
- [ ] Create `app/repositories/user_repository.py` with `get_by_email`, `create`, `get_by_id` methods.
- [ ] Create `app/services/auth_service.py` with `register`, `authenticate`, `hash_password`, `verify_password` methods.
- [ ] Implement CSRF token generation and validation utilities.
- [ ] Create `app/dependencies.py` `get_current_user` dependency (reads session, fetches user, checks `is_active`).
- [ ] Create `app/routers/auth.py` with `GET /register`, `POST /register`, `GET /login`, `POST /login`, `POST /logout` routes.
- [ ] Create `templates/auth/register.html` and `templates/auth/login.html`.
- [ ] Configure session middleware (`starlette.middleware.sessions.SessionMiddleware`) with secret key from config.
- [ ] Implement PRG: on successful login/register, redirect to `/dashboard`; on failure, re-render form with error.
- [ ] Protect `/dashboard` with `get_current_user` dependency; redirect unauthenticated requests to `/login`.
- [ ] Create stub `GET /dashboard` route and template for testing.
- [ ] Write tests: successful registration, duplicate email, invalid password, successful login, wrong password, logged-out session, unauthenticated redirect.

**Exit Criteria:**
- Users can register, log in, and log out.
- Passwords are stored as Argon2id hashes (verify by inspecting the `password_hash` column).
- Unauthenticated access to `/dashboard` redirects to `/login`.
- Cross-user access is not possible (each session is isolated).

**Acceptance Criteria:**
- `POST /register` with duplicate email shows an error, does not create a second record.
- `POST /login` with wrong password shows a generic error that does not reveal whether the email exists.
- Session cookie has `HttpOnly` and `SameSite=Lax` attributes (verify in browser DevTools).
- CSRF token mismatch on form submission returns 403.

---

### Phase 3 — Companies and Applications

**Goal:** Full CRUD for companies and applications with search, filters, sorting, pagination, and ownership enforcement.

**Task List:**
- [ ] Create `app/models/company.py` and `app/models/application.py` SQLAlchemy models.
- [ ] Create Pydantic schemas for company and application create/edit/response.
- [ ] Create `company_repository.py` with `create`, `get_by_id`, `list_by_user`, `update`, `archive`, `delete` methods.
- [ ] Create `application_repository.py` with same CRUD plus `search` (by title/company name), `filter` (by status, date, type), `sort`, and paginated `list` methods.
- [ ] Create `company_service.py` with ownership checks, name normalization, duplicate detection, archiving logic, and hard-delete condition check.
- [ ] Create `application_service.py` with ownership checks, company-ownership cross-check, archiving, and hard-delete condition check.
- [ ] Create `app/routers/companies.py` with all 9 company routes.
- [ ] Create `app/routers/applications.py` with all 9 application routes.
- [ ] Create all company and application templates.
- [ ] Implement search form with query parameters, filter dropdowns, sort dropdowns.
- [ ] Implement pagination (page number + items-per-page; show prev/next and page count in template).
- [ ] Add `templates/partials/pagination.html` and `templates/partials/status_badge.html`.
- [ ] Write tests: create company, duplicate company name (same user), duplicate name different case, company belonging to another user is inaccessible, create application, application with wrong company_id rejected, search and filter results, pagination correctness, cross-user isolation for both entities.

**Exit Criteria:**
- A user can manage companies and applications end-to-end through the browser.
- Search, filter, sort, and pagination all work correctly.
- Company uniqueness is case-insensitive per user.
- Cross-user access to companies and applications returns 404.

**Acceptance Criteria:**
- Creating a company with the same name (different case) fails with a validation error.
- Attempting to access another user's company or application via URL returns 404.
- Applications cannot be created with a company that belongs to another user.
- Pagination shows correct page counts and navigates correctly.

---

### Phase 4 — Lifecycle and History

**Goal:** Status transitions, correction workflow, and audit timeline.

**Task List:**
- [ ] Create `app/models/application_status_history.py` SQLAlchemy model.
- [ ] Create Pydantic schemas for status update and correction.
- [ ] Create `application_status_history_repository.py` with `create` and `get_by_application_id` methods.
- [ ] Create `app/services/lifecycle_service.py` with: state machine validation (`ALLOWED_TRANSITIONS` dict), `transition_status()` method (updates application status and inserts history in one transaction), `correct_status()` method (bypass validation, require note, set `is_correction=True`).
- [ ] Create `app/routers/lifecycle.py` (or add to applications router) with `GET /applications/{id}/status`, `POST /applications/{id}/status`, `GET /applications/{id}/status/correct`, `POST /applications/{id}/status/correct`.
- [ ] Create `templates/applications/status_form.html` and `templates/applications/correction_form.html`.
- [ ] Add status timeline section to `templates/applications/detail.html` (chronological list of history entries, correction entries visually distinct).
- [ ] Write tests: every allowed transition succeeds; every disallowed transition raises an error; correction bypasses state machine; note is required for correction; transaction rollback if history insert fails; timeline renders in correct order.

**Exit Criteria:**
- Every status update is auditable via the history timeline.
- Status update and history insert are transactionally consistent (both succeed or both fail).
- Terminal states (`Rejected`, `Withdrawn`) block forward transitions via the normal route.

**Acceptance Criteria:**
- Attempting a disallowed transition via the form shows an error; the status is not changed.
- Forcing a database error mid-transaction causes both the status update and history insert to roll back.
- Correction entries are visually distinct in the timeline (e.g., labeled "Correction").

---

### Phase 5 — Interviews, Resumes and Reminders

**Goal:** Interview round management, external resume references, in-app reminders, and notification creation.

**Task List:**
- [ ] Create `app/models/interview.py`, `app/models/resume.py`, `app/models/application_resume.py`, `app/models/reminder.py`, `app/models/notification.py` SQLAlchemy models.
- [ ] Create Pydantic schemas for all five entities.
- [ ] Create repositories for all five entities.
- [ ] Create `interview_service.py` with CRUD and ownership verification through application JOIN.
- [ ] Create `resume_service.py` with CRUD, `clear_text()`, and hard-delete condition check.
- [ ] Create `reminder_service.py` with CRUD, `complete()`, `reopen()`, and notification creation call.
- [ ] Create `notification_service.py` with `create_for_reminder()` (includes deduplication check) and `mark_read()`.
- [ ] Create routers for all five entities.
- [ ] Create all templates for interviews, resumes, reminders, and notifications.
- [ ] Add unread notification count to nav bar partial (queried on each authenticated page load).
- [ ] Add upcoming interviews and reminders sections to dashboard template.
- [ ] Write application-resume association service and routes.
- [ ] Write tests: create/edit/delete interview (own and cross-user); create/edit/deactivate resume; clear resume text; associate resume with application (own and cross-user); create/complete/reopen reminder; notification created on reminder creation; deduplication prevents duplicate notification; notification marked read.

**Exit Criteria:**
- Interview, resume-reference, and reminder workflows work through the browser.
- Unread notification count is visible in the nav bar.
- Dashboard shows upcoming interviews and reminders.

**Acceptance Criteria:**
- Accessing another user's interview via `/applications/{id}/interviews/{interview_id}` returns 404.
- Creating two reminders for the same application does not create duplicate unread notifications for the same `source_reminder_id`.
- Clearing resume text sets `resume_text = NULL`; the resume record remains.

---

### Phase 6 — Gemini AI

**Goal:** Consent flow, resume analysis, job match, Pydantic-validated results, error handling.

**Task List:**
- [ ] Install `google-generativeai` Python SDK.
- [ ] Create `app/integrations/gemini.py` with a `GeminiClient` wrapper class: `__init__(api_key, model_name, timeout)`, `analyze_resume(resume_text: str) -> dict`, `match_job(resume_text: str, job_description: str) -> dict`.
- [ ] Configure retry logic and timeout handling in the client wrapper.
- [ ] Create Pydantic models `ResumeAnalysisResult` and `JobMatchResult` in `app/schemas/ai_analysis.py`.
- [ ] Create `app/models/ai_analysis.py` SQLAlchemy model.
- [ ] Create `ai_analysis_repository.py` with `create`, `get_by_id`, `list_by_user`, `delete` methods.
- [ ] Create `app/services/ai_service.py` with: consent check, input size validation, Gemini API call, Pydantic response validation, `ai_analyses` record creation (`pending` → `completed` or `failed`).
- [ ] Create `app/routers/ai.py` with all AI routes (consent, resume-analysis, job-match, analyses list, detail, delete).
- [ ] Create all AI templates with structured result display panels and score breakdown.
- [ ] Add "What does this score mean?" explanation to job match result template.
- [ ] Write tests: resume analysis success (mock Gemini), job match success, malformed JSON response → analysis marked failed, Gemini timeout → analysis marked failed, rate limit error → graceful error message, input too long → validation error before API call, consent gate: user without consent is redirected, delete analysis removes record.

**Exit Criteria:**
- Users can run resume analysis and job-match and view structured results.
- Gemini provider failure does not break core application features.
- Failed analyses are stored with a safe error description.

**Acceptance Criteria:**
- A Gemini response that does not match the Pydantic schema is marked `failed`; no raw API error is shown to the user.
- Resume text longer than 10,000 characters is rejected before the API is called.
- Compatibility Score breakdown sums to the total score.
- Score label clearly reads "Compatibility Score" with no "hire likelihood" language.

---

### Phase 7 — Dashboard and Analytics

**Goal:** Complete dashboard with metrics, activity feed, and analytics page with trends.

**Task List:**
- [ ] Create `app/services/analytics_service.py` with methods: `get_application_counts_by_status(user_id)`, `get_interview_counts(user_id)`, `get_offer_count(user_id)`, `get_recent_activity(user_id, limit=10)`, `get_upcoming_interviews(user_id, days=7)`, `get_upcoming_reminders(user_id)`, `get_overdue_reminders(user_id)`, `get_applications_per_week(user_id, weeks=12)`, `get_conversion_rate(user_id, from_status, to_status)`.
- [ ] Update `app/routers/dashboard.py` with full dashboard data aggregation.
- [ ] Create `app/routers/analytics.py` with the analytics page.
- [ ] Update `templates/dashboard/index.html` with large metric cards, activity list, interview panel, reminder panel.
- [ ] Create `templates/analytics/index.html` with trend visualization (CSS-based bar charts or simple tables if JS charts are not used), conversion funnel, metric definitions.
- [ ] Verify all queries are scoped to `current_user.id` with explicit tests.
- [ ] Check query efficiency: use `COUNT()` aggregates rather than loading full record sets into Python.
- [ ] Write tests: application counts match inserted records; counts do not include another user's records; activity feed is scoped; conversion rate is calculated correctly.

**Exit Criteria:**
- Dashboard values match the underlying records exactly.
- All metrics are scoped to the logged-in user.
- No query returns cross-user aggregate data.

**Acceptance Criteria:**
- Creating an application for User B does not change User A's dashboard counts.
- Metric definitions are documented in the template (tooltip or explanatory text).
- Dashboard loads without N+1 query problems.

---

### Phase 8 — Quality, Deployment and Handover

**Goal:** Complete test suite, production configuration, Docker, deployment, documentation.

**Task List:**
- [ ] Complete test coverage: add missing unit tests (status machine edge cases, analytics calculations, name normalization).
- [ ] Add integration tests for all service + repository interactions.
- [ ] Add route tests for all 50+ routes.
- [ ] Add authorization test suite: for each entity type, verify User A cannot read/update/delete User B's records.
- [ ] Add data integrity tests: FK constraint violations, unique constraint violations, transaction rollbacks.
- [ ] Add AI integration tests with full mock suite (success, malformed, timeout, rate limit, oversized input).
- [ ] Add structured logging using Python `logging` module; configure log levels by environment.
- [ ] Add centralized error handlers in `app/main.py` for 404, 403, 422, 500.
- [ ] Configure production settings in `app/config.py` (`Secure` cookie, `DEBUG=False`, `ENVIRONMENT=production`).
- [ ] Write `Dockerfile` (multi-stage build: install dependencies, copy app, run with uvicorn).
- [ ] Write `docker-compose.yml` for local development (app + PostgreSQL containers).
- [ ] Write `.dockerignore`.
- [ ] Deploy to Railway or Render; configure environment variables; run `alembic upgrade head` on production DB.
- [ ] Configure GitHub Actions CI: `pytest` on push; linting with `ruff`; optionally `pip-audit` for dependency vulnerabilities.
- [ ] Add two-step account hard-delete flow (`POST /profile/delete` with email confirmation).
- [ ] Write `README.md` with: project description, local setup steps, environment variable documentation, Alembic usage, running tests, Docker usage, deployment guide.
- [ ] Run final security review: check all ownership assertions, CSRF coverage, cookie flags in production.
- [ ] Prepare interview walkthrough: be able to explain every system component from memory.

**Exit Criteria:**
- A fresh environment can be set up from documentation alone (another developer can follow README.md from scratch).
- All critical workflows pass their tests.
- Deployment is live and accessible.
- Developer can explain every part of the system in a technical interview.

**Acceptance Criteria:**
- `pytest` passes with zero failures on a clean database.
- `docker-compose up` starts the application and connects to PostgreSQL.
- Production deployment URL is accessible with HTTPS.
- All environment variables are documented in `.env.example` with descriptions.

---

## 4. Git Workflow

### Branch Strategy

- `main` branch: stable, deployable code only. Protected; no direct pushes.
- Feature branches: `feature/{phase}-{short-description}`, e.g., `feature/phase2-auth`, `feature/phase3-companies`.
- One feature branch per phase (or per significant feature within a phase).
- Merge to `main` via pull request after tests pass.

### Commit Message Format

Use conventional commit style for clarity:

```
feat(auth): implement Argon2id password hashing and session management

- Add passlib[argon2] dependency
- Create auth_service with hash_password and verify_password
- Configure SessionMiddleware with HttpOnly and SameSite=Lax
- Add CSRF token generation and validation dependency

Tests: test_auth_service.py covers hash, verify, wrong password
```

Format: `type(scope): short imperative summary` followed by a blank line and a body.

Types: `feat` (new feature), `fix` (bug fix), `test` (tests only), `refactor` (no behavior change), `docs` (documentation), `chore` (tooling).

### One Commit per Stable Milestone

Each Git commit should represent a working, testable state. Do not commit broken code to `main`. Use small, frequent commits on feature branches; squash to a single meaningful commit when merging to `main`.

### Critical Rules

- **Never commit `.env`.** It is in `.gitignore`. Verify before every push with `git status`.
- **Never commit API keys, passwords, or secrets in any file.** Use `.env.example` with placeholder values.
- **Never force-push to `main`.**

---

## 5. Local Development Setup Checklist

Follow these steps on a fresh machine to get ApplyTrack running locally.

**Prerequisites: Python 3.12+ and PostgreSQL 15+ must be installed.**

```
Step 1: Clone the repository
  git clone https://github.com/your-username/applytrack.git
  cd applytrack

Step 2: Create and activate a Python virtual environment
  python -m venv venv
  # Windows PowerShell:
  .\venv\Scripts\Activate.ps1
  # macOS/Linux:
  source venv/bin/activate

Step 3: Install Python dependencies
  pip install -r requirements.txt

Step 4: Create a PostgreSQL database
  psql -U postgres
  CREATE DATABASE applytrack;
  CREATE USER applytrack_app WITH PASSWORD 'your_local_password';
  GRANT ALL PRIVILEGES ON DATABASE applytrack TO applytrack_app;
  \q

Step 5: Set up environment variables
  cp .env.example .env
  # Edit .env and fill in:
  #   DATABASE_URL=postgresql://applytrack_app:your_local_password@localhost:5432/applytrack
  #   SECRET_KEY=generate-a-random-32-byte-string-here
  #   GEMINI_API_KEY=your-google-gemini-api-key
  #   GEMINI_MODEL=gemini-3.8-flash
  #   ENVIRONMENT=development
  #   DEBUG=True

Step 6: Run database migrations
  alembic upgrade head
  # Expected output: "Running upgrade -> <revision>, Create all tables"

Step 7: Start the development server
  uvicorn app.main:app --reload --port 8000
  # Expected output: "Uvicorn running on http://127.0.0.1:8000"
  # Open http://localhost:8000 in your browser

Step 8: Run the test suite
  pytest
  # Or with coverage:
  pytest --cov=app --cov-report=term-missing

Step 9: (Optional) Run with Docker Compose
  docker-compose up --build
  # App available at http://localhost:8000
```

**Troubleshooting:**
- If `alembic upgrade head` fails: check `DATABASE_URL` in `.env` and verify PostgreSQL is running.
- If Gemini tests fail: check `GEMINI_API_KEY` or run with mocked Gemini (see `tests/conftest.py`).
- If fonts do not load: verify font files are in `app/static/` or CDN URLs are correct.

---

## 6. Interview Preparation Guide

The following maps each core interview question to the phase where the feature is implemented and identifies what to study.

| Interview Question | Relevant Phase | What to Study |
|---|---|---|
| Why FastAPI? What are path operations, dependencies, and Pydantic schemas? | Phase 1–2 | FastAPI docs: routes, `Depends()`, request body validation, response models |
| Why PostgreSQL? Explain primary keys, foreign keys, indexes, joins, and normalization. | Phase 0 (design) + Phase 1 (migration) | `DATABASE_DESIGN.md`, PostgreSQL docs on indexes and constraints |
| What is SQLAlchemy ORM? What is a session? How do transactions work? | Phase 1 (models) + Phase 4 (transactions) | SQLAlchemy 2.x docs: `Session`, `begin()`, `commit()`, `rollback()` |
| Why separate routers, services, and repositories? | All phases | Layered architecture: router handles HTTP; service handles business logic; repository handles DB |
| Authentication vs. authorization; hashing vs. encryption; cookie/session security. | Phase 2 | `SECURITY_PLAN.md` sections 2 and 3; understand why Argon2id is memory-hard |
| How does the application prevent IDOR? | Phase 3–5 | `SECURITY_PLAN.md` section 3; explain the ownership check pattern in `get_application()` |
| How are status updates and history kept consistent? | Phase 4 | Explain database transactions: both the UPDATE and INSERT happen in one `db.commit()` |
| How do search, filtering, sorting, and pagination work? | Phase 3 | SQLAlchemy: `filter()`, `order_by()`, `offset()`, `limit()`; explain query parameters |
| How is Gemini called, how is output validated, and what happens when the provider fails? | Phase 6 | `app/integrations/gemini.py`; Pydantic validation of AI output; `analysis.status = 'failed'` path |
| How are tests structured and how is the application configured for deployment? | Phase 8 | `tests/conftest.py`; explain fixtures, test database, mock Gemini; `Dockerfile`; env vars |
| What is CSRF and how is it prevented? | Phase 2 | `SECURITY_PLAN.md` section 2.3; explain synchronizer token pattern |
| What is a migration and why is Alembic needed? | Phase 1 | Alembic docs; explain that schema changes need to be version-controlled |
| What is an N+1 query problem and how is it avoided? | Phase 7 (analytics) | SQLAlchemy eager loading with `joinedload()`; use aggregate queries instead of Python loops |
| What is the difference between soft-delete and hard-delete? | Phase 0 (design) | `is_archived` flag vs. `DELETE` SQL; explain audit trail implications |
| Why are UUID primary keys used instead of integers? | Phase 0 (design) | `SECURITY_PLAN.md` section 3.1; explain non-guessability and that UUIDs don't replace auth |
