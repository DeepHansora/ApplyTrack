# ApplyTrack — Requirements Specification
## Phase 0: Requirements and Design Freeze

> Document version: 1.0 | Status: Draft — Awaiting Owner Approval

---

## 1. Project Overview

ApplyTrack is a multi-user web application for organizing the job-search process. Each user maintains a private company directory and tracks applications from initial interest through assessment, interviews, offers, rejection, or withdrawal.

The application is built as a modular monolith using Python/FastAPI with server-rendered Jinja2 pages. It is designed with sound backend engineering principles: data modelling, validation, authentication, authorization, transactions, testing, and a maintainable layered architecture.

ApplyTrack is not a placement office or recruiter-facing tool. It is a personal productivity tool for individual job seekers. Each account owns its own companies, applications, resumes, reminders, and analysis history — completely isolated from all other users.

---

## 2. Primary Goals

- Give users one reliable place to manage job applications and all related information.
- Make application progress visible through status history, interview records, and reminders.
- Help users compare their resume content with job descriptions using Gemini-powered AI analysis.
- Demonstrate sound backend engineering: data modelling, validation, authentication, authorization, transactions, testing, and maintainable architecture.
- Keep the interface visually consistent, accessible, and practical for frequent daily use.

---

## 3. Target Users

**Individual job seekers.** ApplyTrack is a single-tenant-per-account system in which every user manages their own private job search. It is explicitly not a placement-office or team-management system.

There is a single user role: **authenticated user**. All features are available to all registered users. Administrative functions are limited to what is needed for self-service account management (profile updates, deactivation, deletion).

Multi-user means multiple independent accounts can exist on the same deployment. It does not imply shared workspaces, team collaboration, or hierarchical roles.

---

## 4. Out of Scope (Initial Release)

- Placement coordinator dashboards and team administration panels.
- Resume file uploads and server-side document storage (PDFs, DOCX, etc.).
- Email, SMS, or push notifications — in-app only.
- React, Vue, or any other JavaScript-heavy SPA frontend.
- HTMX (may be introduced in a later phase with demonstrated justification).
- Microservices, event-driven architecture, or distributed infrastructure.
- Redis and Celery background task queues (unless a later demonstrated requirement justifies them).
- Payment processing, subscription tiers, or billing.
- Mobile-native applications (responsive browser UI only).
- Recruiter or company-facing portals.

---

## 5. Definition of Success

A user can:

1. Register, verify their identity, and manage their account profile.
2. Manage a full job search end-to-end: create companies, create applications, track status transitions, record interviews, and manage resume references.
3. Understand their progress through a dashboard with metrics, activity feeds, and visual status summaries.
4. Receive in-app reminders and notification records about their job search.
5. Obtain structured AI feedback by pasting resume text and job descriptions into the Gemini-powered analysis tool.

The developer can:

- Explain the full data flow for any implemented feature.
- Explain all database constraints, security decisions, and error-handling paths.
- Demonstrate tests that cover happy paths, invalid input, missing records, and unauthorized access.

---

## 6. Agreed Product Decisions

| Decision Area | Agreed Direction |
|---|---|
| User model | Multi-user; each person manages their own isolated job search |
| Company directory | Private company records per user; not shared |
| AI provider | Google Gemini |
| Resume tracking | External resume links only; no file uploads in initial scope |
| AI resume input | External link for reference + pasted resume text for analysis |
| Reminders | In-app only; no email, SMS, or push |
| Frontend rendering | FastAPI-rendered Jinja2 pages, HTML and CSS |
| JavaScript framework | None in initial release (no React, no HTMX) |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Authentication | Secure server-side session approach; Argon2id password hashing |
| Primary keys | UUID (reduces guessability; authorization checks still required) |
| AI output handling | Pydantic-validated structured results; treated as untrusted input |

---

## 7. Functional Requirements

Each module's acceptance criteria are written as verifiable conditions. All criteria assume an authenticated user unless otherwise noted.

---

### Module A — Accounts and Profile

- [ ] A user can register by providing their full name, email address, and password.
- [ ] The system validates that the email address is unique before creating an account.
- [ ] The system stores only a secure Argon2id password hash; the plaintext password is never persisted or logged.
- [ ] A registered user can log in with their email and password; the system creates a secure server-side session.
- [ ] A logged-in user can log out; the session is invalidated server-side.
- [ ] All pages beyond the landing page, login, and registration require authentication; unauthenticated access redirects to `/login`.
- [ ] Every private record (companies, applications, resumes, reminders, etc.) is associated with its owner via `user_id` and is never accessible to other users.
- [ ] A logged-in user can update their full name and email address on the profile page.
- [ ] A logged-in user can deactivate their account; deactivation disables login without deleting data.
- [ ] Session cookies use the `Secure`, `HttpOnly`, and `SameSite` flags.
- [ ] State-changing forms include CSRF tokens; the server validates them before processing.
- [ ] Invalid login credentials result in a generic error message that does not reveal whether the email exists.

---

### Module B — Private Company Directory

- [ ] A user can create a company record with: name (required), website, industry, location, and personal notes.
- [ ] A user can view a list of all their companies.
- [ ] A user can view the detail page for any company they own, including associated applications.
- [ ] A user can edit any company they own.
- [ ] A user can archive a company they own; archived companies are hidden from default listing but remain in the database.
- [ ] A user can hard-delete a company only if it has no associated applications.
- [ ] Company names are normalized (lowercase, trimmed whitespace) for uniqueness checking; two companies with names that differ only by case are considered duplicates.
- [ ] The system prevents creating or editing a company name that would duplicate an existing company name for the same user.
- [ ] One company can be associated with multiple applications (for different roles at the same organization).
- [ ] A user cannot view, edit, archive, or delete another user's company records.

---

### Module C — Job Applications

- [ ] A user can create an application by selecting an existing company they own and providing: job title (required), job description, posting URL, employment type, location, salary minimum, salary maximum, currency, application date, status, and notes.
- [ ] A user can view a paginated list of all their applications.
- [ ] A user can search applications by company name or job title.
- [ ] A user can filter applications by: status, date range, and employment type.
- [ ] A user can sort applications by: application date, company name, job title, or salary.
- [ ] A user can view the full detail page for any application they own.
- [ ] A user can edit any application they own.
- [ ] A user can archive an application; archived applications are excluded from default listings.
- [ ] A user can hard-delete an application only if it has status `Saved` and no status history entries (i.e., never formally submitted).
- [ ] The system enforces that the selected company for an application belongs to the same user as the application.
- [ ] Salary range fields are optional; when provided, `salary_min` must be less than or equal to `salary_max`.
- [ ] A user cannot access another user's applications.

---

### Module D — Application Lifecycle

- [ ] An application can be in exactly one of the following statuses at any time: `Saved`, `Applied`, `Assessment`, `Interview`, `Offer`, `Rejected`, `Withdrawn`.
- [ ] The system supports natural, flexible job-search progressions without forcing artificial intermediate stages (e.g. direct progression from `Saved` to `Applied`, `Interview`, or `Offer`; direct progression from `Applied` to `Assessment`, `Interview`, `Offer`, `Rejected`, or `Withdrawn`).
- [ ] The system enforces allowed status transitions according to a formal state machine (defined in the database design and development plan).
- [ ] Every status change results in a new record in `application_status_history` recording: `old_status`, `new_status`, `changed_at` timestamp, and an optional note.
- [ ] The status update on the `applications` record and the insert into `application_status_history` occur within the same database transaction.
- [ ] If the transaction fails for any reason, both the status update and the history insert are rolled back together.
- [ ] A user may correct any accidental or mistaken status update by using a dedicated correction action. Corrections require a mandatory note explaining the correction and are logged with `is_correction = True` in the history table, preserving a complete and honest audit trail.
- [ ] The application detail page renders a visual timeline of all status history entries in chronological order, with clear visual distinction for correction entries.
- [ ] `Rejected` and `Withdrawn` are terminal operational states for forward progression; corrections remain available via the explicit correction action with audit trail.

---

### Module E — Interview Management

- [ ] A user can record multiple interview rounds for a single application.
- [ ] Each interview round stores: round number (integer), round name (e.g., "Technical Screen"), interview type (e.g., Phone, Video, On-site, Technical, Panel), scheduled date/time, meeting URL, status (e.g., Scheduled, Completed, Cancelled, No-show), preparation notes, and feedback/outcome notes.
- [ ] A user can create, view, edit, and delete interview records for applications they own.
- [ ] The application detail page displays all interview rounds associated with that application.
- [ ] The dashboard displays upcoming interviews (scheduled in the future) and recent past interviews.
- [ ] A user cannot access or modify interview records belonging to another user's applications.

---

### Module F — Resume References

- [ ] A user can store an external resume reference with: title (required), version label (e.g., "v2 — Backend Focus"), external URL, and optionally pasted resume text.
- [ ] `resume_text` is nullable; pasting text is only required when the user wants to run AI analysis.
- [ ] A user can create, view, edit, deactivate, and delete their resume records.
- [ ] A user can associate one or more resume versions with a specific application to record which version was submitted.
- [ ] The join between applications and resumes records a `submitted_at` timestamp and enforces no duplicate mappings.
- [ ] The application detail page shows all associated resume references.
- [ ] External resume URLs are stored as references only; the application does not fetch or render their contents.
- [ ] A user can delete the pasted `resume_text` from a resume record independently (without deleting the resume record itself).
- [ ] A user cannot access another user's resume records.

---

### Module G — In-App Reminders

- [ ] A user can create a reminder with: title (required), description, due date/time (required), and an optional link to an application.
- [ ] A user can mark a reminder as complete; the system records a `completed_at` timestamp.
- [ ] A user can re-open a completed reminder (clear `completed_at`).
- [ ] A user can view reminders organized into: Upcoming (due in the future, not complete), Overdue (due in the past, not complete), and Completed.
- [ ] Creating a reminder generates a corresponding notification record in the same transaction.
- [ ] Notification deduplication prevents inserting a second unread notification for the same reminder.
- [ ] The dashboard displays upcoming and overdue reminders for the authenticated user.
- [ ] A user can delete a reminder.
- [ ] A user cannot access another user's reminders or notifications.

---

### Module H — Gemini AI Analysis

- [ ] Before using the AI analysis feature for the first time, the user must provide affirmative consent acknowledging that their resume text and job description will be sent to Google's Gemini API.
- [ ] The consent acknowledgement is recorded (user_id, timestamp, consent version) for audit purposes.
- [ ] **Resume Analysis:** A user can submit pasted resume text to receive a structured analysis including: summary, extracted skills, experience/education observations, identified strengths, identified gaps, and improvement suggestions.
- [ ] **Job Match:** A user can submit pasted resume text and a job description to receive: matched skills, missing/weakly evidenced skills, an explanation, recommendations, and a rubric-based Compatibility Score (0–100) with a visible breakdown.
- [ ] The Compatibility Score is labeled clearly as a "Compatibility Score" and never presented as a probability of hire or recruitment likelihood.
- [ ] The Compatibility Score breakdown shows four distinct categories: Skills Match (40 pts), Experience Relevance (30 pts), Education Fit (10 pts), Keyword Alignment (20 pts).
- [ ] The scoring engine enforces strict non-double-counting: technical capabilities counted under Skills Match are excluded from Keyword Alignment, which strictly evaluates domain/industry phrasing, methodologies, and tooling terminology not classified as standalone core skills.
- [ ] Missing or unstated information (such as absent educational details or optional requirements) is treated fairly by evaluating against stated criteria without applying punitive zero-floor deductions.
- [ ] All Gemini API responses are parsed into a defined Pydantic schema before any data is saved or rendered.
- [ ] AI analysis results are saved in the `ai_analyses` table with status `pending`, `completed`, or `failed`.
- [ ] Failed analyses retain a safe, human-readable error description; they do not expose raw API error responses to the user.
- [ ] The system enforces a maximum input size for resume text and job descriptions.
- [ ] The system handles Gemini API errors gracefully: provider errors, timeouts, rate limits, and malformed responses are caught and logged without crashing the application.
- [ ] The AI analysis pages display a history of past analyses for the user.
- [ ] The user can delete a stored analysis result.
- [ ] AI output is treated as untrusted; it is validated by Pydantic and escaped by Jinja2 before rendering.
- [ ] API keys and full resume text are never written to application logs.

---

### Module I — Dashboard and Analytics

- [ ] The dashboard displays application totals grouped by status (Saved, Applied, Assessment, Interview, Offer, Rejected, Withdrawn).
- [ ] The dashboard displays total interview counts and total offers received.
- [ ] The dashboard displays recent activity (latest status changes and new applications).
- [ ] The dashboard displays upcoming interviews (next 7 days).
- [ ] The dashboard displays upcoming and overdue reminders.
- [ ] The analytics page provides: application trend over time (applications created per week/month), conversion metrics (e.g., Applied → Interview rate), and pipeline funnel visualization.
- [ ] All dashboard and analytics metrics are strictly scoped to the authenticated user.
- [ ] Metric definitions are documented and consistent with the underlying data (no phantom counts).
- [ ] The analytics page includes clear labels and contextual explanations for each metric.

---

## 8. Design System Requirements

### Visual Tokens

| Token | Value | Usage |
|---|---|---|
| Background (primary) | `#F2F2F2` | Page background |
| Primary text | `#111111` | Headings and important content |
| Muted gray | `#B6B5B5` | Secondary elements and rules |
| Secondary text | `#838282` | Supporting copy and labels |
| Echo tone 1 | `#BFBFBF` | Subtle backgrounds and dividers |
| Echo tone 2 | `#C9C9C9` | Card borders |
| Echo tone 3 | `#D1D1D1` | Input borders |
| Echo tone 4 | `#D9D9D9` | Disabled state backgrounds |
| Dark surface | `#1E1E1E` | Footer and selected dark panels |
| Heading font | Clash Display, bold; fallback: geometric sans-serif | All headings H1–H3 |
| Body font | Satoshi, medium; fallback: clean sans-serif | All body text |
| Border style | Thin neutral-gray rules | Cards, tables, form fields |
| Border radius | Restrained (small, consistent) | Buttons, inputs, cards |

### Global Consistency Rules

- Every authenticated page must use the shared base template (`base.html`) with shared navigation and footer.
- Typography, spacing, and color tokens must be applied from the shared CSS file; no inline styles for token values.
- Form elements, buttons, tables, and status labels must use reusable CSS classes and Jinja2 partials.
- The design language is Swiss editorial minimalism with a luxury-brutalist edge: typography-led, high contrast, monochromatic, spacious.
- No decorative imagery; typographic hierarchy creates visual interest.

### Page-Level Design Treatments

| Page | Treatment |
|---|---|
| Landing page | Full editorial hero, echo typography, narrative sections, dark footer |
| Login / Registration | Same typography, neutral surfaces, restrained form panel, shared nav language |
| Dashboard | Large numerical metrics, asymmetrical grid, activity list, reminder panels |
| Applications list | Clear table/list layout, compact filters, consistent status labels, pagination |
| Application detail | Structured sections, status timeline, interview list, resume references |
| AI Analysis | Large analysis heading, clear input area, structured result panels, transparent score explanation |
| Settings / Profile | Matching page heading, form fields, borders, and spacing system |
| Companies | List and detail layout consistent with applications pattern |
| Reminders | Categorized panels (Upcoming, Overdue, Completed) |
| Analytics | Large metric cards, trend charts (CSS-driven), clear metric labels |

---

## 9. Technology Stack

| Area | Technology | Rationale Note |
|---|---|---|
| Language | Python 3.12+ | Modern Python with type hints, strong ecosystem |
| Web framework | FastAPI | Async-capable, Pydantic-native, clean dependency injection |
| Frontend rendering | Jinja2 templates | Server-rendered; no JS build step; standard browser forms |
| Styling | HTML + CSS | Custom CSS with design tokens; no CSS framework initially |
| Database | PostgreSQL | JSONB support, UUID support, robust constraints, production-grade |
| ORM | SQLAlchemy 2.x | Mature Python ORM; session management; async support available |
| Schema migrations | Alembic | SQLAlchemy-native migration tool; version-controlled schema |
| Data validation | Pydantic v2 | FastAPI-native; significantly faster than v1; strict type validation |
| Password hashing | Argon2id (passlib) | Current recommended standard; memory-hard; bcrypt as fallback |
| Session management | Server-side sessions with secure cookies | HttpOnly, Secure, SameSite cookie flags |
| AI integration | Google Gemini API (default `gemini-3.8-flash`) | Structured text analysis; model configurable via `GEMINI_MODEL` environment variable |
| Testing | Pytest + FastAPI TestClient | Standard Python testing; in-process test client for route tests |
| Version control | Git + GitHub | Standard; feature branches; CI on push |
| Containerization | Docker | Consistent environments; Phase 8 deployment |
| Deployment | Railway or Render (Phase 8) | Simple PaaS with managed PostgreSQL; free/hobby tier available |

---

## 10. Project Structure

```
applytrack/
├── app/
│   ├── main.py                  # FastAPI application factory and startup
│   ├── config.py                # Settings via Pydantic BaseSettings (.env)
│   ├── database.py              # SQLAlchemy engine, session factory
│   ├── dependencies.py          # FastAPI dependencies (current_user, db session)
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── company.py
│   │   ├── application.py
│   │   ├── application_status_history.py
│   │   ├── interview.py
│   │   ├── resume.py
│   │   ├── application_resume.py
│   │   ├── reminder.py
│   │   ├── notification.py
│   │   └── ai_analysis.py
│   ├── schemas/                 # Pydantic request/response schemas
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── company.py
│   │   ├── application.py
│   │   ├── interview.py
│   │   ├── resume.py
│   │   ├── reminder.py
│   │   ├── notification.py
│   │   └── ai_analysis.py
│   ├── routers/                 # FastAPI route handlers
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── companies.py
│   │   ├── applications.py
│   │   ├── interviews.py
│   │   ├── resumes.py
│   │   ├── reminders.py
│   │   ├── notifications.py
│   │   ├── ai.py
│   │   ├── analytics.py
│   │   └── profile.py
│   ├── services/                # Business logic and workflow coordination
│   │   ├── __init__.py
│   │   ├── auth_service.py
│   │   ├── company_service.py
│   │   ├── application_service.py
│   │   ├── lifecycle_service.py
│   │   ├── interview_service.py
│   │   ├── resume_service.py
│   │   ├── reminder_service.py
│   │   ├── notification_service.py
│   │   ├── ai_service.py
│   │   └── analytics_service.py
│   ├── repositories/            # Database queries and persistence
│   │   ├── __init__.py
│   │   ├── user_repository.py
│   │   ├── company_repository.py
│   │   ├── application_repository.py
│   │   ├── interview_repository.py
│   │   ├── resume_repository.py
│   │   ├── reminder_repository.py
│   │   ├── notification_repository.py
│   │   └── ai_analysis_repository.py
│   ├── integrations/
│   │   └── gemini.py            # Gemini API client wrapper
│   ├── templates/
│   │   ├── base.html            # Shared base layout
│   │   ├── partials/            # Reusable template fragments
│   │   │   ├── nav.html
│   │   │   ├── footer.html
│   │   │   ├── flash.html
│   │   │   ├── pagination.html
│   │   │   └── status_badge.html
│   │   ├── auth/
│   │   │   ├── login.html
│   │   │   └── register.html
│   │   ├── dashboard/
│   │   │   └── index.html
│   │   ├── profile/
│   │   │   └── index.html
│   │   ├── companies/
│   │   │   ├── list.html
│   │   │   ├── detail.html
│   │   │   └── form.html
│   │   ├── applications/
│   │   │   ├── list.html
│   │   │   ├── detail.html
│   │   │   └── form.html
│   │   ├── interviews/
│   │   │   ├── list.html
│   │   │   └── form.html
│   │   ├── resumes/
│   │   │   ├── list.html
│   │   │   ├── detail.html
│   │   │   └── form.html
│   │   ├── reminders/
│   │   │   ├── list.html
│   │   │   └── form.html
│   │   ├── notifications/
│   │   │   └── list.html
│   │   ├── ai/
│   │   │   ├── index.html
│   │   │   ├── resume_analysis.html
│   │   │   ├── job_match.html
│   │   │   └── analysis_detail.html
│   │   └── analytics/
│   │       └── index.html
│   └── static/
│       └── css/
│           ├── tokens.css       # Design token custom properties
│           ├── base.css         # Reset and global styles
│           ├── layout.css       # Grid and page layout
│           ├── components.css   # Buttons, forms, tables, badges
│           └── pages/           # Page-specific styles if needed
├── migrations/                  # Alembic migration files
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── tests/
│   ├── conftest.py              # Fixtures, test database setup
│   ├── unit/
│   ├── integration/
│   └── routes/
├── .env.example                 # Template; no real values
├── requirements.txt
├── alembic.ini
├── README.md
└── .gitignore
```

---

## 11. Quality Standards

### Definition of Done

Before any feature is considered complete, all of the following must be true:

- [ ] The requirement and user flow are understood and documented.
- [ ] The frontend page uses the shared base layout and design tokens.
- [ ] Router, schema, service, repository, and model responsibilities are clear and correctly separated.
- [ ] Input validation and meaningful error states are implemented (not just happy path).
- [ ] Ownership and authorization are enforced in the service layer (not just the router).
- [ ] Database constraints and correct transaction boundaries are in place.
- [ ] Happy path, invalid input, missing record, and unauthorized access are tested.
- [ ] The user-facing flow works end-to-end in the browser (not just via API).
- [ ] The developer can explain the implementation, the trade-offs, and the security decisions.
- [ ] A focused Git commit records the stable milestone.

### Testing Requirements

| Test Category | Scope | Examples |
|---|---|---|
| Unit | Status transition rules, validation helpers, analytics calculations, normalisation functions | `test_status_machine.py`, `test_analytics_calculations.py` |
| Integration | Service + repository + test PostgreSQL database | `test_company_service.py`, `test_application_service.py` |
| Route | Full HTTP request/response via FastAPI TestClient | `test_auth_routes.py`, `test_application_routes.py` |
| Authorization | User A cannot read/update/delete User B's records | `test_cross_user_isolation.py` |
| Data integrity | Foreign keys, unique constraints, transaction rollback | `test_db_constraints.py` |
| AI integration | Mock Gemini: success, malformed output, timeout, rate limit, provider error | `test_ai_service.py` |
| UI workflow | Form validation, PRG redirect, empty state, pagination, error rendering | `test_ui_flows.py` |

---

## 12. Identified Gaps, Ambiguities, and Contradictions

The following issues were identified during requirements analysis. Each must be resolved before Phase 1 begins.

1. **Missing `updated_at` on `companies` table.** The `users` table has `updated_at` but `companies` does not. All mutable tables should have `updated_at` for change tracking and cache invalidation. Resolution: add `updated_at` to `companies`, `interviews`, and any other mutable table missing it.

2. **Missing `updated_at` on `interviews` table.** Interview records are mutable (notes, feedback, status, scheduled time all change). `updated_at` is required. Resolution: add `updated_at` to `interviews`.

3. **`application_resumes` join table has no primary key defined.** The original schema lists this as a join table with no PK. Resolution: define a composite primary key `(application_id, resume_id)` which also enforces the uniqueness rule stated in the requirements.

4. **`notifications` table mentions an unnamed "deduplication reference" field.** The field is described but not named in the schema. Resolution: name it `source_reminder_id` (nullable FK → `reminders.id`, ON DELETE SET NULL) and enforce uniqueness via a database constraint `uq_notifications_reminder_unread (source_reminder_id) WHERE read_at IS NULL` to prevent race conditions during automated or simultaneous notification generation.

5. **`interviews` and `application_status_history` have no direct `user_id` column.** Ownership must be verified by JOINing through `applications`. This is architecturally valid but must be explicitly enforced in every service-layer query so that ownership checks are never accidentally omitted. Resolution: document this pattern in service-layer guidelines and add specific authorization tests.

6. **Status transition rules were previously overly restrictive.** The initial draft assumed linear progression, but real-world hiring processes often skip phases (e.g. direct movement from `Applied` to `Interview` or `Offer`). Resolution: define a flexible state machine permitting legitimate direct transitions, while recording correction transitions with `is_correction = TRUE` and mandatory notes.

13. **Session token raw persistence vulnerability.** Storing raw session identifiers in the database risks total session takeover if database snapshots or read replicas are breached. Resolution: store a cryptographic hash (`SHA-256`) of the session token in the database, verifying incoming cookies via constant-time comparison.

14. **Account deletion and backup retention limitations.** Soft-deactivation (`is_active = FALSE`) preserves records in the database for a 30-day grace period. An explicit hard-deletion route purges live records via `ON DELETE CASCADE`. However, application code alone cannot guarantee total legal GDPR compliance due to immutable disaster recovery backups (WAL logs/snapshots retained by cloud database providers for periods determined by the specific hosting provider and plan, e.g. 7 to 30 days). Resolution: clearly document the 30-day grace period behavior, provide self-service live hard-deletion, and document backup retention cycles and restore procedures in operational runbooks.

7. **`salary_min` and `salary_max` need a CHECK constraint.** Both should be nullable (salary is optional). When both are provided, `salary_min <= salary_max` must be enforced. Resolution: add `CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max)` to the `applications` table.

8. **`resume_text` on the `resumes` table should be nullable.** External-URL-only resumes must not require pasted text. `resume_text` is only populated when the user wants to run AI analysis. Resolution: make `resume_text TEXT NULL` with no default.

9. **`ai_analyses.result` JSONB field has no documented schema.** The field stores different structures for `resume_analysis` vs `job_match` analysis types. Without a documented schema, the parsing code and display templates cannot be written consistently. Resolution: define a formal Pydantic schema for each analysis type result structure in `DATABASE_DESIGN.md` Section 8 and in `app/schemas/ai_analysis.py` before Phase 6.

10. **No `profile` table or profile-extension columns are mentioned.** The requirements mention profile updates (name, email) but if timezone, avatar, job-search preferences, or consent records are needed, there is no place to store them. Resolution: add an `ai_consent` column (`BOOLEAN NOT NULL DEFAULT FALSE`) and `ai_consent_given_at` (`TIMESTAMP NULL`) to the `users` table to satisfy the AI consent requirement. Plan additional profile columns in `users` as needed.

11. **Cascade behavior for `reminders.application_id` (nullable FK) is undefined.** When an application is archived or deleted, the reminders associated with it should not silently disappear. Resolution: use `ON DELETE SET NULL` for `reminders.application_id` so reminders become standalone (not application-linked) if their parent application is deleted. Document this in the deletion policy.

12. **No explicit composite unique index is listed for `companies.user_id + companies.name_normalized`.** The uniqueness rule is described in prose but no index is defined. A unique index (not just a constraint) is required for PostgreSQL to enforce this efficiently. Resolution: add `UNIQUE INDEX uq_companies_user_name_normalized ON companies (user_id, name_normalized)`.
