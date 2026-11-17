from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.templating import templates


router = APIRouter()


CHECKLIST_PAGES = {
    "new-partner": "Новый партнёр",
    "new-product": "Новый препарат",
    "new-safety-message": "Сообщение",
    "new-reconciliation": "Сверка",
    "new-document": "Документ",
    "literature-search": "Лит. поиск",
}

PLAN_FILTERS = {
    "overdue": "Просрочено",
    "today": "Сегодня",
    "week": "Эта неделя",
    "month": "Месяц",
    "quarter": "Квартал",
    "halfyear": "Полугодие",
    "year": "Год",
    "ad_hoc": "Внепланово",
}

PERIOD_FILTERS = {"today", "week", "month", "quarter", "halfyear", "year"}
DONE_STATUSES = {"Выполнено", "Закрыто"}


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, plan_filter: str = "week", db: Session = Depends(get_db)):
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    selected_filter = plan_filter if plan_filter in PLAN_FILTERS else "week"
    plan_tasks = build_plan_tasks(today)
    visible_tasks = [task for task in plan_tasks if task_matches_filter(task, selected_filter, today)]
    new_messages = dashboard_new_safety_messages(db, today)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "active_page": "dashboard",
            "today": today,
            "today_display": format_date(today),
            "plan_filters": PLAN_FILTERS,
            "selected_filter": selected_filter,
            "plan_tasks": [prepare_plan_task(task, today) for task in visible_tasks],
            "filter_cards": {
                "overdue": count_tasks(plan_tasks, "overdue", today),
                "today": count_tasks(plan_tasks, "today", today),
                "week": count_tasks(plan_tasks, "week", today),
                "messages": len(new_messages),
            },
            "new_messages": new_messages,
            "week_start": week_start,
            "week_end": week_end,
        },
    )


@router.get("/checklists", response_class=HTMLResponse)
def checklist_index(request: Request):
    return render_checklist_placeholder(request, "Все сценарии")


@router.get("/checklists/{scenario}", response_class=HTMLResponse)
def checklist_scenario(request: Request, scenario: str):
    return render_checklist_placeholder(request, CHECKLIST_PAGES.get(scenario, "Сценарий"))


def render_checklist_placeholder(request: Request, title: str):
    return templates.TemplateResponse(
        request,
        "checklist_placeholder.html",
        {
            "request": request,
            "active_page": "dashboard",
            "page_title": title,
        },
    )


def build_plan_tasks(today: date) -> list[dict[str, object]]:
    week_start = today - timedelta(days=today.weekday())
    weekend_date = week_start + timedelta(days=5)
    return [
        {
            "due_date": today - timedelta(days=1),
            "title": "Проверить просроченный ответ партнёра",
            "plan": "День",
            "module": "Кейсы",
            "object": "ICSR-2026-000",
            "status": "Ожидает ответа",
            "url": "/cases",
        },
        {
            "due_date": today,
            "title": "Провести триаж",
            "plan": "День",
            "module": "Сообщения",
            "object": "Сообщение #1",
            "status": "Новое",
            "url": "/incoming-requests",
        },
        {
            "due_date": today + timedelta(days=1),
            "title": "Ответить партнёру",
            "plan": "День",
            "module": "Кейсы",
            "object": "ICSR-2026-001",
            "status": "В работе",
            "url": "/cases",
        },
        {
            "due_date": weekend_date,
            "title": "Подготовить внеплановую проверку",
            "plan": "Внепланово",
            "module": "Задачи",
            "object": "ad_hoc-001",
            "status": "Требует проверки",
            "url": "/tasks",
        },
        {
            "due_date": date(today.year, today.month, min(28, max(today.day, 1))),
            "title": "Провести сверку",
            "plan": "Месяц",
            "module": "Сверки",
            "object": "Partner A",
            "status": "Не начато",
            "url": "/partner-reconciliation",
        },
        {
            "due_date": date(today.year, 7 if today.month <= 6 else 10, 15),
            "title": "Литературный поиск",
            "plan": "Квартал",
            "module": "Лит. мониторинг",
            "object": "Product X",
            "status": "Запланировано",
            "url": "/literature-monitoring",
        },
        {
            "due_date": date(today.year, 9 if today.month <= 6 else 12, 30),
            "title": "Проверить договор",
            "plan": "Полугодие",
            "module": "Договоры",
            "object": "Partner B",
            "status": "Запланировано",
            "url": "/contracts",
        },
        {
            "due_date": date(today.year, 12, 31),
            "title": "Пересмотр МФСФ",
            "plan": "Год",
            "module": "МФСФ",
            "object": "Общий МФСФ",
            "status": "Запланировано",
            "url": "/psmf",
        },
        {
            "due_date": today - timedelta(days=2),
            "title": "Закрытая сверка",
            "plan": "Неделя",
            "module": "Сверки",
            "object": "Partner C",
            "status": "Закрыто",
            "url": "/partner-reconciliation",
        },
    ]


def prepare_plan_task(task: dict[str, object], today: date) -> dict[str, object]:
    due_date = task["due_date"]
    status = str(task["status"])
    return {
        **task,
        "due_display": format_date(due_date),
        "is_overdue": is_overdue_task(task, today),
        "is_today": due_date == today,
        "is_weekend": due_date.weekday() >= 5,
        "is_done": status in DONE_STATUSES,
    }


def count_tasks(tasks: list[dict[str, object]], filter_key: str, today: date) -> int:
    return sum(1 for task in tasks if task_matches_filter(task, filter_key, today))


def task_matches_filter(task: dict[str, object], filter_key: str, today: date) -> bool:
    due_date = task["due_date"]
    include_overdue = filter_key in PERIOD_FILTERS and is_overdue_task(task, today)
    if filter_key == "overdue":
        return is_overdue_task(task, today)
    if filter_key == "today":
        return include_overdue or due_date == today
    if filter_key == "week":
        week_start = today - timedelta(days=today.weekday())
        return include_overdue or week_start <= due_date <= week_start + timedelta(days=6)
    if filter_key == "month":
        return include_overdue or due_date.year == today.year and due_date.month == today.month
    if filter_key == "quarter":
        return include_overdue or (
            due_date.year == today.year
            and (due_date.month - 1) // 3 == (today.month - 1) // 3
        )
    if filter_key == "halfyear":
        return include_overdue or (
            due_date.year == today.year and (due_date.month <= 6) == (today.month <= 6)
        )
    if filter_key == "year":
        return include_overdue or due_date.year == today.year
    if filter_key == "ad_hoc":
        return str(task["plan"]).lower() in {"внепланово", "ad_hoc"}
    return True


def is_overdue_task(task: dict[str, object], today: date) -> bool:
    due_date = task["due_date"]
    status = str(task["status"])
    return due_date < today and status not in DONE_STATUSES


def dashboard_new_safety_messages(db: Session, today: date) -> list[dict[str, str]]:
    messages = []
    for row in crud.list_incoming_requests(db):
        if row.triage_status not in {"not_started", "in_progress"}:
            continue
        subject = (
            row.partner.partner_name
            if row.partner
            else row.product.product_name
            if row.product
            else row.active_substance
            or row.source_name
            or row.message_number
            or "Без объекта"
        )
        source = row.source_name or row.source_type or row.channel or "Источник не указан"
        messages.append(
            {
                "date": format_date(row.received_date or today),
                "source": source,
                "subject": subject,
                "status": "требуется триаж",
                "url": f"/incoming-requests?search={row.message_number or ''}",
            }
        )
    return messages


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")
