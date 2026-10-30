from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import access_middleware
from app.config import get_settings
from app.database import init_db
from app.i18n import language_middleware
from app.security import RequestSizeLimitMiddleware
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.exc import IntegrityError
from fastapi.responses import JSONResponse
from app.routers import (
    accounting_data,
    authentication,
    cases,
    contract_contacts,
    contract_imports,
    contracts,
    dashboard,
    dosage_forms,
    incoming_requests,
    literature_monitoring,
    partners,
    partner_reconciliation,
    partner_onboarding,
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


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Pharmacovigilance System",
    description="Internal pharmacovigilance workflow system.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None if settings.is_prod else "/redoc",
    openapi_url=None if settings.is_prod else "/openapi.json",
)

app.middleware("http")(language_middleware)
app.middleware("http")(access_middleware)
app.add_middleware(RequestSizeLimitMiddleware)


@app.exception_handler(StaleDataError)
async def concurrent_update_error(request, exc):
    return JSONResponse(
        {"detail": "Запись уже изменена другим пользователем. Обновите страницу и повторите действие."},
        status_code=409,
    )


@app.exception_handler(IntegrityError)
async def invalid_reference_error(request, exc):
    return JSONResponse({"detail": "Нарушена связь или уникальность записи. Проверьте выбранные данные."}, status_code=422)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(authentication.router)
app.include_router(dashboard.router)
app.include_router(safety_reports.router)
app.include_router(cases.router)
app.include_router(partners.router)
app.include_router(partner_onboarding.router)
app.include_router(partner_reconciliation.router)
app.include_router(accounting_data.router)
app.include_router(incoming_requests.router)
app.include_router(literature_monitoring.router)
app.include_router(tasks.router)
app.include_router(sops.router)
app.include_router(psur.router)
app.include_router(psmf.router)
app.include_router(products.router)
app.include_router(dosage_forms.router)
app.include_router(substances.router)
app.include_router(contracts.router)
app.include_router(contract_imports.router)
app.include_router(contract_contacts.router)
app.include_router(submissions.router)
app.include_router(users_roles.router)
app.include_router(placeholders.router)


@app.get("/health")
def health():
    return {"status": "ok"}
