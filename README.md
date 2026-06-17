# Pharmacovigilance MVP

FastAPI + SQLAlchemy + SQLite MVP for basic pharmacovigilance workflows.

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
pip install -r requirements.txt
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

or:

```bash
python run.py
```

Windows one-command start from the project folder:

```powershell
.\start.cmd
```

Use `.\start.cmd` when PowerShell script execution is disabled. It runs `start.ps1` with a process-only execution-policy bypass, frees port `8000` if it is already in use, installs dependencies, and starts the site at `http://127.0.0.1:8000/`.

## Create Seed Data

```bash
python -m app.seed
```

## Open

```text
http://127.0.0.1:8000
```

API docs:

```text
http://127.0.0.1:8000/docs
```

## Database

By default, the app creates `pv_system.db` in the project folder.

Set `DATABASE_URL` to switch databases without changing service logic:

```text
DATABASE_URL=sqlite:///./pv_system.db
```

The SQLAlchemy models use string enum values and UUID string primary keys to keep the schema friendly for a future PostgreSQL migration.

## Runtime mode

The app starts in demo mode by default. Production mode disables the demo user switch by default and hides API documentation endpoints:

```text
PV_APP_MODE=prod
PV_DEMO_USER_SWITCH=false
PV_ALLOW_QUERY_USER_SWITCH=false
PV_DEFAULT_PAGE_SIZE=25
PV_MAX_PAGE_SIZE=100
PV_MAX_UPLOAD_BYTES=26214400
```

## Outlook / Microsoft Graph

Partner reconciliation can create an Outlook draft and send it through Microsoft Graph after explicit user confirmation. Configure a Microsoft Entra app with delegated `offline_access`, `User.Read`, `Mail.ReadWrite`, and `Mail.Send` permissions, then set:

```text
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_TENANT_ID=common
MICROSOFT_REDIRECT_URI=http://127.0.0.1:8000/outlook/callback
MICROSOFT_SCOPES=offline_access User.Read Mail.ReadWrite Mail.Send
```

Secrets are read from environment variables only. The MVP keeps OAuth tokens in process memory and does not write access or refresh tokens to the database or audit log.

## Migrations

Alembic is configured for schema history. Create or apply migrations with:

```bash
alembic revision --autogenerate -m "change description"
alembic upgrade head
```

## Workflow

```text
Dashboard -> Safety Reports -> Triage -> Create Case -> Add Patient/Product/Reaction -> Submission
```

The app records audit trail rows for core actions such as safety report creation, triage, case creation, status changes, patient/product/reaction additions, and submission creation.
