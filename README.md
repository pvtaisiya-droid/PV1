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

## Workflow

```text
Dashboard -> Safety Reports -> Triage -> Create Case -> Add Patient/Product/Reaction -> Submission
```

The app records audit trail rows for core actions such as safety report creation, triage, case creation, status changes, patient/product/reaction additions, and submission creation.
