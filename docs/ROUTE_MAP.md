# ApplyTrack — Route and Page Inventory
## Phase 0: Finalized Route Map

> Server-rendered application. All GET routes render a Jinja2 template.
> All state-changing routes (POST) redirect on success (PRG pattern).
> Auth Required = YES means an unauthenticated request redirects to `/login`.

---

## Route Table

### Public Routes

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/` | No | Landing page | Template: `landing.html` | No | Public marketing/intro page |
| GET | `/login` | No | Login form | Template: `auth/login.html` | No | Redirects to `/dashboard` if already logged in |
| GET | `/register` | No | Registration form | Template: `auth/register.html` | No | Redirects to `/dashboard` if already logged in |

---

### Authentication Routes

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| POST | `/register` | No | Submit registration | Redirect: `/dashboard` | Yes | Validates unique email; hashes password with Argon2id; creates session |
| POST | `/login` | No | Submit credentials | Redirect: `/dashboard` | Yes | Verifies password hash; creates session cookie |
| POST | `/logout` | Yes | Invalidate session | Redirect: `/login` | Yes | Server-side session destruction; clears cookie |

---

### Dashboard

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/dashboard` | Yes | Main dashboard | Template: `dashboard/index.html` | No | Shows metrics, upcoming interviews, reminders, recent activity |

---

### Profile / Settings

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/profile` | Yes | View and edit profile | Template: `profile/index.html` | No | Shows current name, email, AI consent status |
| POST | `/profile` | Yes | Update profile (name, email) | Redirect: `/profile` | Yes | Validates unique email if changed; PRG on success |
| POST | `/profile/deactivate` | Yes | Deactivate account | Redirect: `/login` | Yes | Sets `is_active = FALSE`; destroys session |
| POST | `/profile/delete` | Yes | Hard-delete account | Redirect: `/` | Yes | Two-step confirmation required; cascades to all owned records; Phase 8 |
| POST | `/profile/ai-consent` | Yes | Grant or revoke AI consent | Redirect: `/profile` | Yes | Toggles `ai_consent`; records `ai_consent_given_at` |

---

### Companies

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/companies` | Yes | List companies | Template: `companies/list.html` | No | Excludes archived by default; toggle with `?include_archived=1` |
| GET | `/companies/new` | Yes | New company form | Template: `companies/form.html` | No | Blank form |
| POST | `/companies` | Yes | Create company | Redirect: `/companies/{id}` | Yes | Normalizes name; checks uniqueness; returns form with errors on failure |
| GET | `/companies/{id}` | Yes | Company detail | Template: `companies/detail.html` | No | Shows company info and associated applications |
| GET | `/companies/{id}/edit` | Yes | Edit company form | Template: `companies/form.html` | No | Pre-filled form |
| POST | `/companies/{id}/edit` | Yes | Submit company edit | Redirect: `/companies/{id}` | Yes | Re-checks uniqueness; ownership enforced |
| POST | `/companies/{id}/archive` | Yes | Archive company | Redirect: `/companies` | Yes | Sets `is_archived = TRUE` |
| POST | `/companies/{id}/unarchive` | Yes | Unarchive company | Redirect: `/companies/{id}` | Yes | Sets `is_archived = FALSE` |
| POST | `/companies/{id}/delete` | Yes | Hard-delete company | Redirect: `/companies` | Yes | Only allowed if no applications; ownership enforced |

---

### Applications

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/applications` | Yes | List applications | Template: `applications/list.html` | No | Search, filter, sort, paginate; excludes archived by default |
| GET | `/applications/new` | Yes | New application form | Template: `applications/form.html` | No | Pre-populates company dropdown with user's companies |
| POST | `/applications` | Yes | Create application | Redirect: `/applications/{id}` | Yes | Validates company ownership; initial status = `Saved` |
| GET | `/applications/{id}` | Yes | Application detail | Template: `applications/detail.html` | No | Full detail: status, history timeline, interviews, resumes, reminders |
| GET | `/applications/{id}/edit` | Yes | Edit application form | Template: `applications/form.html` | No | Pre-filled; company field editable |
| POST | `/applications/{id}/edit` | Yes | Submit application edit | Redirect: `/applications/{id}` | Yes | Does not change status (use status route); ownership enforced |
| POST | `/applications/{id}/archive` | Yes | Archive application | Redirect: `/applications` | Yes | Sets `is_archived = TRUE` |
| POST | `/applications/{id}/unarchive` | Yes | Unarchive application | Redirect: `/applications/{id}` | Yes | Sets `is_archived = FALSE` |
| POST | `/applications/{id}/delete` | Yes | Hard-delete application | Redirect: `/applications` | Yes | Only for `Saved` status with no history entries; two-step confirm |

---

### Application Lifecycle (Status Management)

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/applications/{id}/status` | Yes | Status change form | Template: `applications/status_form.html` | No | Shows current status; dropdown of valid next statuses |
| POST | `/applications/{id}/status` | Yes | Submit status transition | Redirect: `/applications/{id}` | Yes | Validates state machine; inserts history record in same transaction |
| GET | `/applications/{id}/status/correct` | Yes | Status correction form | Template: `applications/correction_form.html` | No | Shows any status as options; note is mandatory |
| POST | `/applications/{id}/status/correct` | Yes | Submit status correction | Redirect: `/applications/{id}` | Yes | Bypasses state machine; sets `is_correction = TRUE`; note required |

---

### Interviews

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/interviews` | Yes | All upcoming interviews | Template: `interviews/list.html` | No | Shows all interviews across all applications; sorted by `scheduled_at` |
| GET | `/applications/{id}/interviews/new` | Yes | New interview round form | Template: `interviews/form.html` | No | Scoped to one application |
| POST | `/applications/{id}/interviews` | Yes | Create interview round | Redirect: `/applications/{id}` | Yes | Ownership enforced through application |
| GET | `/applications/{id}/interviews/{interview_id}` | Yes | Interview detail | Template: `interviews/detail.html` | No | Shows prep notes, meeting URL, feedback |
| GET | `/applications/{id}/interviews/{interview_id}/edit` | Yes | Edit interview form | Template: `interviews/form.html` | No | Pre-filled |
| POST | `/applications/{id}/interviews/{interview_id}/edit` | Yes | Submit interview edit | Redirect: `/applications/{id}` | Yes | Ownership via application |
| POST | `/applications/{id}/interviews/{interview_id}/delete` | Yes | Delete interview round | Redirect: `/applications/{id}` | Yes | Hard-delete; ownership enforced |

---

### Resumes

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/resumes` | Yes | List resume references | Template: `resumes/list.html` | No | Shows active resumes; toggle `?include_inactive=1` |
| GET | `/resumes/new` | Yes | New resume form | Template: `resumes/form.html` | No | Blank form for URL + optional pasted text |
| POST | `/resumes` | Yes | Create resume | Redirect: `/resumes/{id}` | Yes | At least one of URL or text required |
| GET | `/resumes/{id}` | Yes | Resume detail | Template: `resumes/detail.html` | No | Shows URL, version, pasted text summary, associated applications |
| GET | `/resumes/{id}/edit` | Yes | Edit resume form | Template: `resumes/form.html` | No | Pre-filled |
| POST | `/resumes/{id}/edit` | Yes | Submit resume edit | Redirect: `/resumes/{id}` | Yes | Ownership enforced |
| POST | `/resumes/{id}/deactivate` | Yes | Deactivate resume | Redirect: `/resumes` | Yes | Sets `is_active = FALSE` |
| POST | `/resumes/{id}/activate` | Yes | Reactivate resume | Redirect: `/resumes/{id}` | Yes | Sets `is_active = TRUE` |
| POST | `/resumes/{id}/clear-text` | Yes | Delete pasted resume text | Redirect: `/resumes/{id}` | Yes | Sets `resume_text = NULL`; retains URL and record |
| POST | `/resumes/{id}/delete` | Yes | Hard-delete resume | Redirect: `/resumes` | Yes | Only if not in `application_resumes`; ownership enforced |
| POST | `/applications/{id}/resumes` | Yes | Associate resume with application | Redirect: `/applications/{id}` | Yes | Validates resume ownership |
| POST | `/applications/{id}/resumes/{resume_id}/remove` | Yes | Remove resume association | Redirect: `/applications/{id}` | Yes | Deletes from `application_resumes` only |

---

### Reminders

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/reminders` | Yes | List reminders | Template: `reminders/list.html` | No | Categorized: Upcoming, Overdue, Completed |
| GET | `/reminders/new` | Yes | New reminder form | Template: `reminders/form.html` | No | Optional application association dropdown |
| POST | `/reminders` | Yes | Create reminder | Redirect: `/reminders/{id}` | Yes | Also inserts notification in same transaction |
| GET | `/reminders/{id}` | Yes | Reminder detail | Template: `reminders/detail.html` | No | Shows due date, description, linked application |
| GET | `/reminders/{id}/edit` | Yes | Edit reminder form | Template: `reminders/form.html` | No | Pre-filled |
| POST | `/reminders/{id}/edit` | Yes | Submit reminder edit | Redirect: `/reminders/{id}` | Yes | Ownership enforced |
| POST | `/reminders/{id}/complete` | Yes | Mark reminder complete | Redirect: `/reminders` | Yes | Sets `completed_at = now()` |
| POST | `/reminders/{id}/reopen` | Yes | Reopen reminder | Redirect: `/reminders/{id}` | Yes | Sets `completed_at = NULL` |
| POST | `/reminders/{id}/delete` | Yes | Delete reminder | Redirect: `/reminders` | Yes | Hard-delete; ownership enforced |

---

### Notifications

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/notifications` | Yes | List all notifications | Template: `notifications/list.html` | No | Unread first; then read; sorted by `created_at` |
| POST | `/notifications/{id}/read` | Yes | Mark notification read | Redirect: `/notifications` | Yes | Sets `read_at = now()` |
| POST | `/notifications/read-all` | Yes | Mark all notifications read | Redirect: `/notifications` | Yes | Bulk update: `read_at = now()` where `read_at IS NULL` |
| POST | `/notifications/{id}/delete` | Yes | Delete notification | Redirect: `/notifications` | Yes | Hard-delete single record |
| POST | `/notifications/clear-read` | Yes | Delete all read notifications | Redirect: `/notifications` | Yes | Bulk delete where `read_at IS NOT NULL` |

---

### AI Analysis

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/ai` | Yes | AI analysis index / history | Template: `ai/index.html` | No | Lists past analyses; shows consent status; links to both analysis types |
| GET | `/ai/consent` | Yes | Consent acknowledgement page | Template: `ai/consent.html` | No | Shown before first analysis if consent not given |
| POST | `/ai/consent` | Yes | Submit consent | Redirect: `/ai` | Yes | Sets `ai_consent = TRUE`, `ai_consent_given_at = now()` on user record |
| GET | `/ai/resume-analysis` | Yes | Resume analysis form | Template: `ai/resume_analysis.html` | No | Requires consent; paste text area and optional resume selector |
| POST | `/ai/resume-analysis` | Yes | Submit resume analysis | Redirect: `/ai/analyses/{id}` | Yes | Validates input size; calls Gemini; saves `ai_analyses` record |
| GET | `/ai/job-match` | Yes | Job match form | Template: `ai/job_match.html` | No | Requires consent; paste resume + job description; optional application link |
| POST | `/ai/job-match` | Yes | Submit job match | Redirect: `/ai/analyses/{id}` | Yes | Validates both inputs; calls Gemini; saves record |
| GET | `/ai/analyses/{id}` | Yes | View analysis result | Template: `ai/analysis_detail.html` | No | Displays structured result with score breakdown panel |
| POST | `/ai/analyses/{id}/delete` | Yes | Delete analysis result | Redirect: `/ai` | Yes | Hard-deletes; ownership enforced |

---

### Analytics

| Method | Path | Auth Required | Purpose | Success Redirect / Template | CSRF Required | Notes |
|---|---|---|---|---|---|---|
| GET | `/analytics` | Yes | Analytics and trends page | Template: `analytics/index.html` | No | Application trends, funnel metrics, conversion rates |

---

## Route Design Notes

### PRG Pattern (Post/Redirect/Get)

All state-changing requests use the Post/Redirect/Get pattern:
1. The browser submits a **POST** form.
2. The server processes the request.
3. On success, the server responds with a **302 redirect** to a GET URL.
4. The browser follows the redirect and renders the GET response.

This prevents duplicate form submissions on browser refresh and aligns with standard web application behavior. Flash messages (success or error notifications) are stored in the session and read on the next GET request.

On validation failure, the server re-renders the **form template** directly (without redirect) with field-level error messages, pre-filling the user's input.

### CSRF Protection

All POST routes require a CSRF token. The implementation uses a **synchronizer token pattern**:
- A unique CSRF token is generated per session and stored server-side.
- Every form includes a hidden `<input name="csrf_token" value="...">` field.
- The route handler reads the token from the form body and compares it to the session-stored token.
- Mismatch results in a 403 response.
- FastAPI middleware or a dependency handles token injection and validation.

### Authentication Dependency

All routes marked `Auth Required = Yes` use a FastAPI dependency `get_current_user`:
- Reads the session cookie.
- Looks up the user in the database.
- Verifies `is_active = TRUE`.
- If the session is missing, expired, or the user is inactive: raises a redirect to `/login`.
- Returns the authenticated `User` model instance for use in route handlers and service calls.

### Path Parameter Conventions

- All `{id}` path parameters are UUIDs.
- Route handlers validate that the UUID is well-formed; malformed UUIDs return 422.
- Service methods always verify record ownership before returning data; a valid UUID for another user's record returns 404 (not 403) to avoid confirming record existence.

### Query Parameter Conventions

- `?page=1&per_page=20` — pagination (default: page 1, 20 items per page)
- `?q=search+term` — full-text search
- `?status=Applied` — status filter
- `?sort=applied_at&order=desc` — sorting
- `?employment_type=Full-time` — type filter
- `?include_archived=1` — include archived records
- `?date_from=2024-01-01&date_to=2024-12-31` — date range filter

---

## Template Structure

The following maps each template file to the route(s) that render it.

| Template File | Routes |
|---|---|
| `base.html` | Inherited by all authenticated and public templates |
| `landing.html` | `GET /` |
| `auth/login.html` | `GET /login`; `POST /login` (validation error) |
| `auth/register.html` | `GET /register`; `POST /register` (validation error) |
| `dashboard/index.html` | `GET /dashboard` |
| `profile/index.html` | `GET /profile`; `POST /profile` (validation error) |
| `companies/list.html` | `GET /companies` |
| `companies/detail.html` | `GET /companies/{id}` |
| `companies/form.html` | `GET /companies/new`; `GET /companies/{id}/edit`; POST on validation error |
| `applications/list.html` | `GET /applications` |
| `applications/detail.html` | `GET /applications/{id}` |
| `applications/form.html` | `GET /applications/new`; `GET /applications/{id}/edit`; POST on validation error |
| `applications/status_form.html` | `GET /applications/{id}/status`; POST on validation error |
| `applications/correction_form.html` | `GET /applications/{id}/status/correct`; POST on validation error |
| `interviews/list.html` | `GET /interviews` |
| `interviews/detail.html` | `GET /applications/{id}/interviews/{interview_id}` |
| `interviews/form.html` | `GET /applications/{id}/interviews/new`; edit form; POST on error |
| `resumes/list.html` | `GET /resumes` |
| `resumes/detail.html` | `GET /resumes/{id}` |
| `resumes/form.html` | `GET /resumes/new`; edit form; POST on error |
| `reminders/list.html` | `GET /reminders` |
| `reminders/detail.html` | `GET /reminders/{id}` |
| `reminders/form.html` | `GET /reminders/new`; edit form; POST on error |
| `notifications/list.html` | `GET /notifications` |
| `ai/index.html` | `GET /ai` |
| `ai/consent.html` | `GET /ai/consent` |
| `ai/resume_analysis.html` | `GET /ai/resume-analysis`; POST on validation error |
| `ai/job_match.html` | `GET /ai/job-match`; POST on validation error |
| `ai/analysis_detail.html` | `GET /ai/analyses/{id}` |
| `analytics/index.html` | `GET /analytics` |
| `partials/nav.html` | Included in `base.html` |
| `partials/footer.html` | Included in `base.html` |
| `partials/flash.html` | Included in `base.html`; renders session flash messages |
| `partials/pagination.html` | Included in list templates |
| `partials/status_badge.html` | Included in application list and detail templates |
