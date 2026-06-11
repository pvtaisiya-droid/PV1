from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.i18n import language_middleware
from app.routers import cases, dashboard, partners, products, safety_reports, submissions


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

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(dashboard.router)
app.include_router(safety_reports.router)
app.include_router(cases.router)
app.include_router(partners.router)
app.include_router(products.router)
app.include_router(submissions.router)


@app.get("/health")
def health():
    return {"status": "ok"}
