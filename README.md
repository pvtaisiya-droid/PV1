# Pharmacovigilance System

FastAPI + SQLAlchemy + SQLite internal system for pharmacovigilance workflows.

## Stack

- Python 3.11+
- FastAPI
- SQLAlchemy ORM
- SQLite for local development
- Jinja2 templates
- Bootstrap 5
- Pydantic
- python-dotenv

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.lock.txt
```

## Run

For a **new, empty database**, initialize the schema and reference data, then
set the first administrator's login and password:

```bash
python -m app.manage init-db
python -m app.manage bootstrap-admin --login pv.admin
```

The password is entered twice without echo and must contain at least 12
characters. There is no default password. Bootstrap refuses to reset an existing
password. Sign in and create other users in **Administration → Users**.

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

or:

```bash
python run.py
```

Windows one-command start from the project folder:

```powershell
.\start.cmd
```

Use `.\start.cmd` when PowerShell script execution is disabled. It runs `start.ps1`
with a process-only execution-policy bypass and starts the site at
`http://127.0.0.1:8000/`. A busy port produces an error without terminating any
process. Use `start.ps1 -Port 8001` to choose another port. Dependency installation
requires `-InstallDependencies`; development auto-reload requires `-Reload`.

## Optional demonstration data

Run this only against a separate demonstration database initialized as above:

```bash
python -m app.seed
```

## Open

```text
http://127.0.0.1:8000
```

The application has no public registration. Users sign in with a login and
password created by an administrator in **Administration → Users**. Passwords
are stored as salted scrypt hashes; active sessions are stored in the local
database. Email and external authentication services are not required.

API docs:

```text
http://127.0.0.1:8000/docs
```

## Database

By default, the application uses `pv_system.db` in the project folder. Application
startup validates the Alembic revision and SQLite foreign keys; it does not
create tables, restore permissions, seed records, or clean up business data.

Set `DATABASE_URL` to switch databases without changing service logic:

```text
DATABASE_URL=sqlite:///./pv_system.db
```

The SQLAlchemy models use string enum values and UUID string primary keys to keep the schema friendly for a future PostgreSQL migration.

## Runtime mode

The app starts in production mode by default. Development mode can be enabled explicitly for local engineering workflows; the user switch remains disabled unless it is explicitly enabled:

```text
PV_APP_MODE=prod
PV_USER_SWITCH_ENABLED=false
PV_ALLOW_QUERY_USER_SWITCH=false
PV_DEFAULT_PAGE_SIZE=25
PV_MAX_PAGE_SIZE=100
PV_MAX_UPLOAD_BYTES=26214400
PV_STORAGE_ROOT=uploads
PV_AUTH_SESSION_HOURS=12
PV_AUTH_COOKIE_SECURE=false
PV_BUSINESS_TIMEZONE=Europe/Moscow
```

Set `PV_AUTH_COOKIE_SECURE=true` when the application is served exclusively
over HTTPS.

## Outlook / Microsoft Graph

Partner reconciliation can create an Outlook draft and send it through Microsoft Graph after explicit user confirmation. Configure a Microsoft Entra app with delegated `offline_access`, `User.Read`, `Mail.ReadWrite`, and `Mail.Send` permissions, then set:

```text
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_TENANT_ID=common
MICROSOFT_REDIRECT_URI=http://127.0.0.1:8000/outlook/callback
MICROSOFT_SCOPES=offline_access User.Read Mail.ReadWrite Mail.Send
```

Secrets are read from environment variables only. OAuth tokens are kept in process memory and are not written to the database or audit log.

Authorization uses a random, single-use state tied to the current application
session, a ten-minute expiry, and PKCE (S256). Pending requests are stored in the
database so state validation works across worker processes. Invalid state is
rejected before the token exchange.

## Migrations

Alembic is the only schema migration mechanism. Before upgrading an existing
installation, stop its application workers and back up the database together
with the directory configured by `PV_STORAGE_ROOT`. Rehearse the upgrade against
a copy first, setting `DATABASE_URL` to that copy:

```bash
python -m app.manage upgrade-db
python -m app.manage check-db
```

For a database created by the former startup schema builder without an Alembic
revision, run `python -m app.manage check-legacy` first. Only after reviewing that
comparison and testing a backed-up copy, run `python -m app.manage adopt-legacy`.
The command compares the known schema before recording revision `20260824_0025`
and applying the reconciliation migration. Do not use `stamp head` to bypass
schema checks. Legacy import tables and extra columns are retained. Duplicate
values or broken references stop migration with an error; records are not
silently deleted or renumbered. SQLite migration DDL runs in a transaction.
Legacy adoption records its starting revision and upgrades in one transaction;
a failed adoption does not leave a new revision. See the
[legacy rehearsal and data review procedure](docs/migration-rehearsal.md) for
handling duplicate values or broken references before retrying on a copy.

For deliberate local administrator recovery:

```bash
python -m app.manage recover-admin --login existing-login
```

This resets the specified account's password, restores its administrator role
and permissions, revokes its sessions, and appends an audit event. It is an
explicit maintenance operation, never a startup fallback. Ordinary startup
does not derive permissions from the legacy `User.role` field.

Case status updates require `expected_version` from `GET /api/cases/{id}`.
An outdated version returns HTTP 409; reload the record before retrying.
The case form supplies this value automatically. Case and safety-report list
APIs accept `page` and `per_page`, capped by `PV_MAX_PAGE_SIZE`.

Uploaded files, E2B extracts, reconciliation documents and PSMF builds use
`PV_STORAGE_ROOT`. The E2B file limit is the minimum of the global limit and its
format-specific limit. The HTTP request limit adds 1 MiB for multipart overhead
and applies to chunked bodies too. XML/ZIP processing runs in a worker thread.

Development checks (the lock files record the tested Windows/Python 3.12 set):

```bash
pip install -r requirements-dev.lock.txt
python -m pytest -q
```

Tests initialize a separate database through the installation command and copy
it into each test's temporary directory. All test file storage is isolated there.

Before applying the partner-scoped case numbering migration to an existing
database, generate the read-only audit report:

```bash
python tools/case_numbering_audit.py --database pv_system.db --output docs/case_numbering_pre_migration_report.json
```

New cases receive an annual number from an atomic sequence scoped to the
numbering partner (`YYYY-NNN`) or to `UNASSIGNED`
(`UNASSIGNED-YYYY-NNN`). The business year is calculated in
`PV_BUSINESS_TIMEZONE`. Existing nonstandard numbers are reported and retained;
the migration does not perform ambiguous bulk renumbering.

## Workflow

```text
Dashboard -> Safety Reports -> Triage -> Create Case -> Add Patient/Product/Reaction -> Submission
```

The app records audit trail rows for core actions such as safety report creation, triage, case creation, status changes, patient/product/reaction additions, and submission creation.
