import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from app.unit_of_work import WriteSession


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pv_system.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        # SQLite's built-in lower() handles ASCII only; registries contain Cyrillic.
        dbapi_connection.create_function("lower", 1, lambda value: value.lower() if value is not None else None, deterministic=True)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=WriteSession,
    future=True,
)

Base = declarative_base()

from app.aggregate_versions import install as install_aggregate_versions
install_aggregate_versions()


DOSAGE_FORM_SEED_ROWS = [
    (1, "таблетки подъязычные", "sublingual tablets", "таблетки", "tablets", 1, 1),
    (2, "таблетки шипучие", "effervescent tablets", "таблетки", "tablets", 1, 2),
    (3, "капсулы", "capsules", "капсулы", "capsules", 1, 3),
    (
        4,
        "лиофилизат для приготовления раствора для инфузий",
        "lyophilizate for solution for infusion",
        "лиофилизат",
        "lyophilizate",
        1,
        4,
    ),
    (
        5,
        "лиофилизат для приготовления раствора для внутрисосудистого и внутрипузырного введения",
        "lyophilizate for solution for intravascular and intravesical administration",
        "лиофилизат",
        "lyophilizate",
        1,
        5,
    ),
    (
        6,
        "порошок для приготовления раствора для приема внутрь",
        "powder for oral solution",
        "порошок",
        "powder",
        1,
        6,
    ),
    (7, "таблетки", "tablets", "таблетки", "tablets", 1, 7),
    (
        8,
        "таблетки, диспергируемые в полости рта",
        "orodispersible tablets",
        "таблетки",
        "tablets",
        1,
        8,
    ),
    (9, "гель для наружного применения", "gel for external use", "гель", "gel", 1, 9),
    (
        10,
        "таблетки, покрытые пленочной оболочкой",
        "film-coated tablets",
        "таблетки",
        "tablets",
        1,
        10,
    ),
    (11, "сироп", "syrup", "сироп", "syrup", 1, 11),
    (12, "суспензия для приема внутрь", "oral suspension", "суспензия", "suspension", 1, 12),
    (
        13,
        "порошок для приготовления раствора для внутривенного введения и ингаляций",
        "powder for solution for intravenous administration and inhalation",
        "порошок",
        "powder",
        1,
        13,
    ),
    (
        14,
        "раствор для внутривенного и подкожного введения",
        "solution for intravenous and subcutaneous administration",
        "раствор",
        "solution",
        1,
        14,
    ),
    (
        15,
        "порошок для приготовления раствора для инъекций/инфузий",
        "powder for solution for injection/infusion",
        "порошок",
        "powder",
        1,
        15,
    ),
    (
        16,
        "лиофилизированный порошок для приготовления раствора для внутривенного и внутримышечного введения",
        "lyophilized powder for solution for intravenous and intramuscular administration",
        "лиофилизированный порошок",
        "lyophilized powder",
        1,
        16,
    ),
]

ACCOUNTING_SERVICE_SEED_ROWS = [
    (1, "Осуществление пострегистрационного фармаконадзора", "услуга"),
    (2, "Сбор и первичная оценка спонтанных сообщений", "сообщение"),
    (3, "Валидация и медицинская оценка сообщения", "сообщение"),
    (4, "Ускоренное репортирование", "сообщение"),
    (
        5,
        "Скрининг сообщений по безопасности в научно-медицинской литературе и других источниках",
        "поиск",
    ),
    (6, "Подготовка и подача ПООБ", "отчет"),
    (7, "Подготовка ПУР", "документ"),
    (8, "Поддержание МФСФ", "услуга"),
    (9, "Сверка сообщений с партнером", "сверка"),
    (10, "Ответ на запрос регулятора", "запрос"),
    (11, "Другое", "услуга"),
]


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Validate the installed schema. Startup never migrates or rewrites data."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.migration import MigrationContext
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    expected = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
        if actual != expected:
            raise RuntimeError("Database schema is not current. Run python -m app.manage init-db (new installation) or upgrade-db. See README for legacy adoption.")
        if engine.dialect.name == "sqlite":
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchmany(20)
            if violations:
                raise RuntimeError(f"Foreign key violations must be repaired before startup: {violations}")


def initialize_reference_data() -> None:
    """Explicit first-install operation, never called by application startup."""
    from app.psmf import ensure_psmf_seed_data
    from app.rbac import ensure_rbac_defaults
    from app.sop_seed import ensure_sop_starter_data
    from app.models import User

    with SessionLocal() as db:
        if db.query(User).first():
            raise RuntimeError("Reference data already initialized; use upgrade-db for an existing installation.")
        ensure_rbac_defaults(db)
        ensure_psmf_seed_data(db)
        ensure_sop_starter_data(db)
        ensure_dosage_forms(db)
        ensure_accounting_services(db)


def ensure_dosage_forms(db: Session) -> None:
    from app.models import DosageForm
    for id_, name_ru, name_en, group_ru, group_en, is_active, sort_order in DOSAGE_FORM_SEED_ROWS:
        if db.get(DosageForm, id_) is None:
            db.add(DosageForm(id=id_, name_ru=name_ru, name_en=name_en, group_ru=group_ru,
                              group_en=group_en, is_active=bool(is_active), sort_order=sort_order))
    db.commit()


def ensure_accounting_services(db: Session) -> None:
    from app.models import AccountingService
    for sort_order, name, default_unit in ACCOUNTING_SERVICE_SEED_ROWS:
        if not db.query(AccountingService).filter_by(name=name).first():
            db.add(AccountingService(name=name, default_unit=default_unit, sort_order=sort_order))
    db.commit()
