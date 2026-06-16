from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import access_middleware
from app.database import init_db
from app.i18n import language_middleware
from app.routers import (
    cases,
    contract_contacts,
    contracts,
    dashboard,
    incoming_requests,
    partners,
    partner_reconciliation,
    placeholders,
    products,
    psmf,
    psur,
    safety_reports,
    sops,
    substances,
    submissions,
    tasks,
    users_roles,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Pharmacovigilance MVP",
    description="SQLite + FastAPI MVP for pharmacovigilance workflows.",
    version="0.1.0",
    lifespan=lifespan,
)

app.middleware("http")(language_middleware)
app.middleware("http")(access_middleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard.router)
app.include_router(safety_reports.router)
app.include_router(cases.router)
app.include_router(partners.router)
app.include_router(partner_reconciliation.router)
app.include_router(incoming_requests.router)
app.include_router(tasks.router)
app.include_router(sops.router)
app.include_router(psur.router)
app.include_router(psmf.router)
app.include_router(products.router)
app.include_router(substances.router)
app.include_router(contracts.router)
app.include_router(contract_contacts.router)
app.include_router(submissions.router)
app.include_router(users_roles.router)
app.include_router(placeholders.router)


@app.get("/health")
def health():
    return {"status": "ok"}
