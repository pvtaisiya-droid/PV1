# Паспорт проекта PV1

Дата актуализации: 2026-06-17
Текущая версия паспорта: 1.1
Статус проекта: MVP core создан, требуется дальнейшее развитие и валидация.

## 1. Название проекта

**PV1 / Pharmacovigilance MVP** — MVP веб-системы фармаконадзора для фармацевтической компании.

## 2. Краткое описание проекта

PV1 — это локально запускаемый MVP веб-системы фармаконадзора. Система предназначена для базового управления входящей safety information и последующей обработки валидных сообщений в ICSR cases.

В текущем MVP система покрывает следующие процессы:

- прием и хранение входящих `safety reports`;
- triage входящих сообщений;
- создание ICSR cases из валидных safety reports;
- ведение данных пациента;
- ведение препаратов в кейсе;
- ведение нежелательных реакций;
- создание submissions;
- ведение audit trail для ключевых действий;
- загрузку, связь с case/safety report и скачивание документов-вложений;
- light MVP-блок МФСФ/PSMF как набор управляемых компонентов и версий.

Система построена как база для дальнейшего расширения: PBRER/PSUR, RMP, literature, signal detection, MedDRA coding, PostgreSQL migration и GPT/AI extraction.

## 3. Цель проекта

Цель проекта — создать новую современную систему вместо старой базы Microsoft Access.

Основные архитектурные цели:

- заменить Access-логику модульным Python/FastAPI приложением;
- использовать SQLAlchemy ORM как основной слой доступа к данным;
- использовать SQLite только для разработки и MVP;
- сохранить возможность будущей миграции на PostgreSQL через `DATABASE_URL`;
- не привязывать бизнес-логику к конкретной базе данных;
- подготовить архитектуру к будущему подключению GPT/AI-модулей;
- обеспечить GxP-подобный подход: трассируемость ключевых действий через audit trail.

## 4. Текущий статус проекта

| Область | Статус | Комментарий |
|---|---|---|
| Структура проекта | Реализовано | Создано FastAPI-приложение в папке `pv_mvp`. |
| База данных | Реализовано | SQLAlchemy ORM + SQLite `pv_system.db`. |
| ORM-модели | Реализовано | Созданы основные MVP-таблицы. |
| Web UI | Реализовано частично | Есть единый shell с компактной двухуровневой боковой навигацией, рабочие страницы Dashboard, Safety Reports/PV Intake, Cases/ICSRs, Partners, Products, Substances, Contracts, Contract contacts, Partner Reconciliation, Submissions, Documents, Audit Log, Users & Roles, PSUR/PBRER и light MVP-блок МФСФ/PSMF; для части будущих модулей остаются страницы-заглушки. Интерфейс двуязычный RU/EN, русский язык по умолчанию. |
| JSON API | Реализовано частично | Есть основные API-группы для partners, products, substances, product-substances, contracts, contract contacts, safety reports, cases, submissions. |
| Audit trail | Реализовано частично | Добавлен рабочий раздел Audit Log с таблицей событий, поиском, фильтрами по пользователю, модулю, действию и датам, просмотром деталей; аудит фиксирует actor/time/source module и old/new значения для ключевых действий MVP. |
| Seed data | Реализовано | `python -m app.seed` создает тестовый набор данных. |
| CSV export | Реализовано | `GET /api/cases/export.csv`. |
| GPT/AI | Заготовка | Созданы `app/ai/extractor.py` и `app/ai/prompts.py`; реальная интеграция не подключена. |
| Авторизация и RBAC | Реализовано частично | Добавлен MVP-слой users/roles/permissions: несколько ролей у пользователя, отдельные permissions, permission-aware UI, управление Users & Roles. Полноценный login/session механизм пока не реализован. |
| Alembic migrations | Запланировано | В зависимостях и структуре пока нет Alembic. |
| PostgreSQL | Запланировано | Модели проектируются PostgreSQL-friendly; production DB пока не подключалась. |

Что уже реализовано:

- автоматическое создание SQLite-таблиц при старте приложения;
- модульные ORM-модели;
- Pydantic-схемы для API;
- CRUD/service layer;
- HTML-формы для основных workflow;
- двуязычный UI RU/EN через Jinja helper и cookie языка;
- оформление UI по брендбуку ARS PharmRussia: логотип, основной синий `#36A0DE`, градиентный синий `#008DC6`, серый `#D8D9DB`, шрифтовой стек с `Univia Pro`;
- Swagger UI `/docs`;
- seed-данные с валидным safety report, case, patient, product, reaction и submission;
- light MVP МФСФ/PSMF с 3 тестовыми компонентами, простым версионированием, сборкой партнерского МФСФ и HTML-экспортом;
- literature monitoring MVP: план мониторинга, журнал поисков, результаты/публикации, вложения через Documents, создание черновика ИСНР, связи с ПООБ/PSMF/RMP, CSV export и audit trail;
- audit trail для создания safety report, triage, создания case, изменения статуса case, добавления patient/product/reaction/follow-up/submission.

Что находится в процессе или требует усиления:

- расширенная валидация бизнес-правил;
- фильтры и поиск в UI;
- полноценный login/session механизм поверх MVP RBAC;
- миграции Alembic;
- тесты;
- более строгий GxP/audit подход;
- интеграция AI/GPT только после проектирования human review.

Что запланировано:

- PostgreSQL migration;
- развитие role-based access control до production-ready auth/session;
- MedDRA coding;
- PBRER/PSUR;
- RMP;
- signal detection;
- GPT extraction и editable review form;
- импорт данных из старой Access базы.

## 5. Архитектура проекта

Фактическая структура проекта:

```text
pv_mvp/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── auth.py
│   ├── rbac.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── crud.py
│   ├── audit.py
│   ├── i18n.py
│   ├── templating.py
│   ├── seed.py
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── extractor.py
│   │   └── prompts.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── contract_contacts.py
│   │   ├── contracts.py
│   │   ├── dashboard.py
│   │   ├── partners.py
│   │   ├── partner_reconciliation.py
│   │   ├── placeholders.py
│   │   ├── products.py
│   │   ├── safety_reports.py
│   │   ├── substances.py
│   │   ├── users_roles.py
│   │   ├── cases.py
│   │   └── submissions.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── contract_contacts.html
│   │   ├── contracts.html
│   │   ├── dashboard.html
│   │   ├── partner_reconciliation.html
│   │   ├── placeholder.html
│   │   ├── partners.html
│   │   ├── products.html
│   │   ├── safety_reports.html
│   │   ├── safety_report_detail.html
│   │   ├── cases.html
│   │   ├── case_detail.html
│   │   ├── case_new.html
│   │   ├── substances.html
│   │   ├── users_roles.html
│   │   └── submissions.html
│   └── static/
│       ├── brand/
│       │   └── ars-pharmrussia-logo.png
│       └── style.css
├── .env.example
├── README.md
├── requirements.txt
├── run.py
├── start.cmd
├── start.ps1
├── pv_system.db
└── passport_PV1.md
```

Назначение ключевых файлов:

| Файл | Назначение |
|---|---|
| `app/main.py` | Создание FastAPI app, подключение роутеров, language/access middleware, static files, health endpoint. |
| `app/auth.py` | MVP access middleware: выбор текущего пользователя через cookie/query, загрузка permissions в `request.state`, Jinja helpers и dependencies `require_permission`. |
| `app/rbac.py` | Базовый каталог permissions и бизнес-ролей MVP, bootstrap users/roles/permissions/user_roles/role_permissions. |
| `app/database.py` | `DATABASE_URL`, SQLAlchemy engine, session, `init_db`. |
| `app/models.py` | ORM-модели и индексы таблиц. |
| `app/schemas.py` | Pydantic-схемы для API и service layer. |
| `app/crud.py` | Бизнес-операции и работа с ORM. |
| `app/audit.py` | Создание audit trail записей. |
| `app/psmf.py` | Сервисная логика light MVP МФСФ/PSMF: компоненты, версии, seed, сборка партнерского МФСФ и HTML-экспорт. |
| `app/i18n.py` | RU/EN словарь интерфейса, выбор языка по query/cookie, Jinja helper для переводов и ссылок переключения языка. |
| `app/templating.py` | Единый `Jinja2Templates` для HTML UI с подключенными i18n globals. |
| `app/seed.py` | Создание тестовых данных. |
| `app/routers/*.py` | HTML routes и JSON API endpoints; `placeholders.py` содержит временные страницы для будущих разделов. |
| `app/templates/*.html` | Jinja2 templates для web UI. |
| `app/routers/psmf.py` | HTML routes блока МФСФ/PSMF и действия над версиями компонентов. |
| `app/templates/psmf.html` | UI МФСФ/PSMF с вкладками Overview, Components, Partner PSMF и Audit. |
| `app/static/style.css` | Пользовательские стили поверх Bootstrap 5 с цветами ARS PharmRussia. |
| `app/static/brand/ars-pharmrussia-logo.png` | Логотип ARS PharmRussia, извлеченный из брендбука и используемый в navbar на белом фоне. |
| `app/ai/*` | Заготовка будущего AI/GPT extraction модуля. |
| `requirements.txt` | Python-зависимости. |
| `.env.example` | Пример переменных окружения. |
| `run.py` | Альтернативный запуск Uvicorn. |
| `start.cmd` | Windows-обертка для запуска при отключенном выполнении PowerShell-сценариев; вызывает `start.ps1` с `-ExecutionPolicy Bypass` только для текущего процесса. |
| `start.ps1` | Windows-скрипт запуска из папки проекта: освобождает порт `8000`, при необходимости готовит `.venv`, устанавливает зависимости и запускает сайт на `http://127.0.0.1:8000/`. |

## 6. Технологический стек

| Слой | Технологии |
|---|---|
| Backend | Python 3.11+, FastAPI |
| ORM | SQLAlchemy ORM |
| Database | SQLite для MVP/dev; PostgreSQL планируется для production |
| API schemas | Pydantic |
| Frontend | Jinja2 templates, HTML, Bootstrap 5, RU/EN localization helper |
| Static assets | CSS в `app/static/style.css`, логотип в `app/static/brand/`, Bootstrap CDN, Lucide icons CDN |
| Server | Uvicorn |
| Environment | python-dotenv, `.env`, `.env.example` |
| Migrations | Alembic запланирован, пока не подключен |
| Future AI | GPT/AI module для анализа safety reports и извлечения ICSR-данных |

## 7. База данных

Текущая база данных: SQLite.  
Файл базы данных по умолчанию: `pv_system.db`.

Подключение задается в `app/database.py`:

```python
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pv_system.db")
```

Правила работы с БД:

- вся работа с БД должна идти через SQLAlchemy ORM;
- нельзя писать бизнес-логику, завязанную напрямую на SQLite;
- raw SQL не должен использоваться в бизнес-логике;
- enum-значения хранятся как строки, а не как database enum;
- первичные ключи таблиц представлены UUID-строками `String(36)`;
- общие поля большинства таблиц: `id`, `created_at`, `updated_at`, `is_active`, `is_deleted`, `deleted_at`, `deleted_by`, `delete_reason`, `version`;
- будущий переход на PostgreSQL должен выполняться через изменение `DATABASE_URL` и миграции, без переписывания бизнес-логики.

SQLite используется только для разработки, локального запуска и MVP. Для production планируется PostgreSQL.

## 8. Основные таблицы базы данных

| Таблица | Назначение | Ключевые связи |
|---|---|---|
| `tblUsers` | Пользователи системы. Legacy-поле `role` сохраняется для совместимости, фактический доступ задается через `tblUserRoles` и `tblRolePermissions`. | Назначение cases, audit trail, triage/submission actions, M-M с roles. |
| `tblRoles` | Бизнес-роли MVP: Admin, PV Responsible, Deputy PV Responsible, QA Reviewer, Read-only Auditor / Viewer, Executive Approver. | M-M с users и permissions. |
| `tblPermissions` | Отдельные permissions доступа: `view`, `create`, `edit`, `soft_delete`, `approve`, `comment`, `upload`, `export`, `audit_view`, `manage_users`, `manage_reference_data`, `manage_system_settings`. | M-M с roles. |
| `tblUserRoles` | Связь пользователей с ролями, позволяет одному пользователю иметь несколько ролей одновременно. | M-1 к `tblUsers`, M-1 к `tblRoles`, хранит assigned metadata. |
| `tblRolePermissions` | Связь ролей с permissions. | M-1 к `tblRoles`, M-1 к `tblPermissions`. |
| `tblPartners` | Партнеры: код, название, статус, частота сверки. | 1-M с safety reports, cases, products, submissions, contracts, contract contacts. |
| `tblSubstances` | Активные вещества. | M-M с products через `tblProductSubstances`. |
| `tblProducts` | Лекарственные продукты. | M-M с substances; 1-M с case products. |
| `tblProductSubstances` | Связь продукт-вещества. | Связывает `tblProducts` и `tblSubstances`. |
| `tblContracts` | Договоры с партнерами по ЛП. | M-1 к `tblPartners`, M-1 к `tblProducts`; связь `partner_id + product_id` защищена от дублей, статус "действителен сейчас" вычисляется по датам. |
| `tblContractContacts` | Контактные лица по договорам и получатели сверок. | M-1 к `tblPartners`; хранит ФИО, email, должность, актуальность, PV/contact type флаги, To/Cc-флаги для сверки и комментарии; пара `partner_id + email` защищена от дублей для заполненных email. |
| `tblSafetyReports` | Входящие safety reports до создания ICSR case. | Может быть связан с partner и 0..1 case. |
| `tblIncomingRequests` | Журнал сообщений по безопасности / входящих сигналов. | Может быть связан с partner, product и 0..1 case; поддерживает triage status, mock GPT draft, CSV-журнал и создание case из сообщения. |
| `tblCases` | Центральная таблица ICSR cases. | Связана с patients, case products, reactions, follow-ups, attachments, submissions, incoming requests, audit. |
| `tblPatients` | Пациенты в составе case. | M-1 к `tblCases`. |
| `tblCaseProducts` | Препараты в конкретном case. | M-1 к `tblCases`, опционально к `tblProducts`. |
| `tblReactions` | Нежелательные реакции/adverse events. | M-1 к `tblCases`. |
| `tblCaseProductReactionAssessments` | Оценка связи препарат-реакция. | Связывает `tblCaseProducts` и `tblReactions`. |
| `tblFollowUps` | Follow-up информация по case. | M-1 к `tblCases`. |
| `tblAttachments` | Документы-вложения и их метаданные. | Может ссылаться на case или safety report; хранит имя файла, MIME-тип, локальный путь, размер и SHA-256 checksum. |
| `tblLiteratureMonitoringPlans` | Планы литературного мониторинга. | M-1 к `tblPartners`, optional `tblSubstances`, M-M к `tblProducts` через `tblLiteratureMonitoringPlanProducts`; хранит sources, frequency, strategy, keywords, territory, responsible user, dates, status. |
| `tblLiteratureSearchLogs` | Журнал проведенных литературных поисков. | M-1 к plan, optional override partner/substance, M-M к products через `tblLiteratureSearchLogProducts`; хранит search date, period, source, strategy, counts, result, status. |
| `tblLiteratureResults` | Найденные публикации и решения по ФН. | M-1 к plan/log/partner/substance, M-M к products через `tblLiteratureResultProducts`; links to attachments, ICSR case, PSUR plan, PSMF component and RMP reference. |
| `tblSubmissions` | Отправки наружу. | В MVP связана с case; оставлены поля `pbrer_id`, `rmp_id` для будущего. |
| `psmf_components` | Компоненты МФСФ/PSMF: основной раздел, общее приложение или партнер-специфичное приложение. | Может ссылаться на `tblPartners`; имеет текущий статус и номер текущей версии. |
| `psmf_component_versions` | Версии текстов компонентов МФСФ/PSMF. | M-1 к `psmf_components`; хранит content, status, author/reviewer metadata, lock-флаг. |
| `tblAuditTrail` | Audit trail / Audit Log. | Логирует действия по entity, case, user/changed_by, changed_at, source_module, old/new values и comment. |

Ключевые индексы реализованы для:

- `tblCases`: case number, worldwide ID, partner, dates, workflow status, assignee, seriousness, country, composite indexes;
- `tblSafetyReports`: report number, received date, source, partner, triage status;
- `tblProducts`: product code, name, normalized name, authorization fields;
- `tblSubstances`: substance name, normalized name, INN, ATC, CAS;
- `tblReactions`: case, reported term, MedDRA PT/SOC, seriousness;
- `psmf_components`: component type/scope, status, partner;
- `psmf_component_versions`: component, version, status;
- `tblLiteratureMonitoringPlans`: partner/status, substance, responsible user, dates;
- `tblLiteratureSearchLogs`: plan, partner/result, search date, status;
- `tblLiteratureResults`: plan/status, partner, result type/decision, publication date;
- `tblAuditTrail`: entity, case/time, user, timestamp, action.

## 9. Основной бизнес-процесс

Основной flow MVP:

```text
Incoming safety information
-> tblSafetyReports
-> Triage
-> valid ICSR
-> tblCases
-> Patient/Product/Reaction
-> Submission
-> Audit Trail
```

Описание процесса:

1. Входящее сообщение сохраняется в `tblSafetyReports`.
2. На этапе triage пользователь оценивает минимум критериев ICSR:
   - identifiable patient;
   - identifiable reporter;
   - suspect product;
   - adverse event.
3. Если сообщение валидно, оно может быть преобразовано в `tblCases`.
4. В case добавляются данные пациента, препарата и реакции.
5. Для case создается submission.
6. Ключевые действия записываются в `tblAuditTrail`.

Важный принцип: не каждый `tblSafetyReports` становится `tblCases`. Таблица safety reports должна хранить весь входящий поток, включая invalid, duplicate и non-safety сообщения.

## 10. Web UI

Текущие страницы web UI:

| Страница | Route | Назначение |
|---|---|---|
| Dashboard | `/`, `/dashboard` | Карточки с агрегированными показателями. |
| Safety Messages / Message Journal | `/incoming-requests` | Регистрация входящих сообщений по безопасности, triage, demo/mock GPT draft, CSV "Журнал всех сообщений", создание case из сообщения. |
| Safety Reports / PV Intake | `/safety-reports` | Список и форма создания входящих reports. В боковой навигации показан как PV Intake. |
| Safety Report Detail / Triage | `/safety-reports/{report_id}` | Просмотр raw text, minimum criteria, triage, создание case. |
| Cases / ICSRs | `/cases` | Список cases/ICSRs, экспорт CSV, переход к detail. В боковой навигации показан как ICSRs. |
| New Case | `/cases/new` | Ручное создание case. |
| Case Detail | `/cases/{case_id}` | Metadata, patient/product/reaction forms, follow-ups, submissions, audit trail. |
| Partners | `/partners` | Список и форма создания партнеров. |
| Products | `/products` | Список products и форма добавления ЛП. |
| Substances | `/substances` | Список веществ, добавление вещества и связь вещества с ЛП. |
| Contracts | `/contracts` | Список договоров, создание договора с привязкой к партнеру и ЛП, автоматический статус действительности. |
| Contract contacts | `/contract-contacts` | Список контактных лиц по договорам и создание контакта с привязкой к партнеру; дубли `partner + email` блокируются. |
| Submissions | `/submissions` | Список submissions, создание submission для case, изменение статуса. |
| PSUR / PBRER | `/psur` | Страница-заглушка для будущего модуля ПООБ/PSUR/PBRER. |
| RMP | `/rmp` | Страница-заглушка для будущего модуля ПУР/RMP. |
| Literature Monitoring | `/literature-monitoring` | Рабочий MVP-модуль: вкладки Plan/Journal/Results, фильтры, формы, карточки, вложения, создание ИСНР, связи с PSUR/PSMF/RMP и CSV export. |
| МФСФ / PSMF | `/psmf` | Рабочий light MVP-блок МФСФ/PSMF: обзор, компоненты, партнерская сборка и журнал аудита. |
| Documents | `/documents` | Рабочий реестр документов: загрузка файла, связь с case или safety report, фильтры, скачивание и soft delete. |
| Audit Log | `/audit-log` | Рабочий единый журнал действий с поиском, фильтрами и деталями событий. |
| Users & Roles | `/users-roles` | Рабочий MVP-раздел управления пользователями, множественными ролями и permissions. Доступен пользователям с `manage_users`; поддерживает создание пользователя, назначение/снятие ролей, изменение permissions роли и archive пользователя при наличии `soft_delete`. |
| Settings | `/settings` | Страница-заглушка для будущих настроек. |

UI использует единый двухуровневый боковой layout в `app/templates/base.html`. На desktop меню закреплено слева и показывает главные группы (`Dashboard`, `Operations`, `Reference Data`, `Controlled Records`, `Administration / System`) плюс подпункты только выбранной группы. Состояние группы определяется текущим разделом и синхронизируется клиентским JS; sidebar можно свернуть до режима иконок с tooltip через `title`. На узких экранах меню открывается через offcanvas-кнопку. UI должен оставаться простым, рабочим и ориентированным на операционные PV-процессы.

MVP RBAC в UI:

- текущий пользователь отображается в верхней панели;
- для локального MVP-тестирования доступно переключение текущего пользователя через cookie `pv_user_id`;
- пункты меню скрываются по permissions: `manage_users` для Users & Roles, `audit_view` для Audit Log, `manage_system_settings` для Settings, `view` для основных рабочих разделов;
- формы создания/изменения в основных разделах скрываются по `create`, `edit`, `approve`, `export`, `manage_reference_data`;
- прямые POST/PATCH/export endpoints защищены dependencies `require_permission(...)` / `require_any_permission(...)`.

## 11. API endpoints

Swagger UI доступен по адресу:

```text
/docs
```

### Dashboard / service endpoints

| Method | Endpoint | Назначение |
|---|---|---|
| `GET` | `/` | HTML Dashboard. |
| `GET` | `/dashboard` | HTML Dashboard. |
| `GET` | `/health` | Health check. |

Отдельный JSON endpoint для dashboard statistics пока не реализован. При необходимости его следует добавить и отразить в этом паспорте.

### МФСФ / PSMF

| Method | Endpoint | Назначение |
|---|---|---|
| `GET` | `/psmf` | HTML-раздел МФСФ/PSMF с вкладками обзор, компоненты, партнерский МФСФ и журнал аудита. |
| `POST` | `/psmf/components/{component_id}/save` | Сохранение текста черновика компонента МФСФ. |
| `POST` | `/psmf/components/{component_id}/submit` | Перевод черновика компонента в статус "На проверке". |
| `POST` | `/psmf/components/{component_id}/approve` | Утверждение компонента в статусе "На проверке". |
| `POST` | `/psmf/components/{component_id}/new-version` | Создание новой черновой версии из последней утвержденной версии. |
| `POST` | `/psmf/partner-preview` | Формирование предварительного партнерского МФСФ и audit-события. |
| `GET` | `/psmf/partner-preview/download` | Скачивание предварительного партнерского МФСФ как HTML. |

### Partners

| Method | Endpoint |
|---|---|
| `GET` | `/api/partners` |
| `POST` | `/api/partners` |
| `GET` | `/api/partners/{partner_id}` |

### Products / Substances

| Method | Endpoint |
|---|---|
| `GET` | `/api/products` |
| `POST` | `/api/products` |
| `GET` | `/api/substances` |
| `POST` | `/api/substances` |
| `POST` | `/api/product-substances` |

HTML-страница `/substances` управляет веществами и связью вещества с ЛП через `tblProductSubstances`.

### Contracts

| Method | Endpoint |
|---|---|
| `GET` | `/api/contracts` |
| `POST` | `/api/contracts` |
| `GET` | `/api/contracts/{contract_id}` |

### Contract contacts

| Method | Endpoint |
|---|---|
| `GET` | `/api/contract-contacts` |
| `POST` | `/api/contract-contacts` |
| `GET` | `/api/contract-contacts/{contact_id}` |

Для `POST /api/contracts` и `POST /api/contract-contacts` действует серверная проверка дублей: одна активная связь `partner + product` для договора и один активный контакт `partner + email`.

### Users / Roles / Permissions

HTML-раздел `/users-roles` доступен только при permission `manage_users`.

| Method | Endpoint | Назначение |
|---|---|---|
| `GET` | `/api/users` | Список активных пользователей. |
| `GET` | `/api/roles` | Список ролей. |
| `GET` | `/api/permissions` | Список permissions. |
| `POST` | `/users-roles/switch-user` | MVP-переключение текущего пользователя через cookie `pv_user_id` для тестирования ролей без полноценного login. |
| `POST` | `/users-roles/users` | Создание пользователя с одной или несколькими ролями. |
| `POST` | `/users-roles/users/{user_id}/roles` | Назначение роли пользователю. |
| `POST` | `/users-roles/users/{user_id}/roles/{role_id}/remove` | Снятие роли с пользователя. |
| `POST` | `/users-roles/users/{user_id}/archive` | Soft delete/archive пользователя при permission `soft_delete`. |
| `POST` | `/users-roles/roles/{role_id}/permissions` | Изменение набора permissions роли. |

### Safety Reports

| Method | Endpoint |
|---|---|
| `GET` | `/api/safety-reports` |
| `POST` | `/api/safety-reports` |
| `GET` | `/api/safety-reports/{report_id}` |
| `PATCH` | `/api/safety-reports/{report_id}/triage` |
| `POST` | `/api/safety-reports/{report_id}/create-case` |

### Cases

| Method | Endpoint |
|---|---|
| `GET` | `/api/cases` |
| `POST` | `/api/cases` |
| `GET` | `/api/cases/{case_id}` |
| `GET` | `/api/cases/{case_id}/overview` |
| `PATCH` | `/api/cases/{case_id}/status` |
| `POST` | `/api/cases/{case_id}/patients` |
| `POST` | `/api/cases/{case_id}/products` |
| `POST` | `/api/cases/{case_id}/reactions` |
| `POST` | `/api/cases/{case_id}/followups` |
| `POST` | `/api/cases/{case_id}/submissions` |
| `GET` | `/api/cases/export.csv` |

### Literature Monitoring

| Method | Endpoint |
|---|---|
| `GET` | `/api/literature-monitoring/plans` |
| `GET` | `/api/literature-monitoring/logs` |
| `GET` | `/api/literature-monitoring/results` |
| `GET` | `/api/literature-monitoring/results/{result_id}` |
| `GET` | `/api/literature-monitoring/results/export.csv` |

### Submissions

| Method | Endpoint |
|---|---|
| `GET` | `/api/submissions` |
| `POST` | `/api/submissions` |
| `PATCH` | `/api/submissions/{submission_id}/status` |

### Documents / Attachments

| Method | Endpoint | Назначение |
|---|---|---|
| `GET` | `/documents` | HTML-реестр документов с поиском и фильтрами по типу, партнеру, ЛП и датам. |
| `POST` | `/documents` | Multipart upload файла с опциональным display name, типом документа и связью с case или safety report. |
| `GET` | `/documents/{document_id}/download` | Скачивание сохраненного файла из локального хранилища `uploads/documents`. |
| `POST` | `/documents/{document_id}/delete` | Soft delete записи документа при наличии permission `soft_delete`. |

## 12. Audit Trail и GxP-подход

Система должна сохранять audit trail для критичных действий. Текущая таблица: `tblAuditTrail`.

Критичные действия, которые должны логироваться:

- создание safety report;
- triage safety report;
- создание case;
- изменение статуса case;
- добавление пациента;
- добавление препарата в case;
- добавление реакции;
- добавление follow-up;
- добавление связи ЛП-вещества;
- создание договора;
- создание контактного лица по договору;
- создание submission;
- изменение статуса submission;
- загрузка документа;
- скачивание документа;
- soft delete документа;
- создание пользователя;
- назначение роли пользователю;
- снятие роли с пользователя;
- изменение permissions роли;
- archive пользователя через soft delete;
- будущие действия approve, lock, unlock, delete, submit.

Текущая реализация audit trail находится в:

- `app/audit.py`;
- вызовах `log_audit(...)` в `app/crud.py`.

GxP-подход для дальнейшего развития:

- сохранять кто, когда и что изменил;
- хранить old/new values для значимых изменений;
- фиксировать reason/comment для изменения статусов;
- не удалять критичные записи физически без traceability;
- использовать `is_deleted` для soft-delete там, где это применимо;
- обеспечить неизменяемость audit trail на уровне бизнес-логики;
- в будущем добавить RBAC, электронные подписи и более строгую валидацию.

## 13. GPT / AI-ready архитектура

В проекте уже создана заготовка:

```text
app/ai/
├── __init__.py
├── extractor.py
└── prompts.py
```

Будущий AI/GPT-модуль должен помогать с:

- анализом свободного текста входящего safety report;
- извлечением ICSR-данных;
- предварительной оценкой валидности ICSR;
- подсказками по missing information;
- подготовкой draft case narrative;
- возможной помощью в MedDRA-кодировании.

Принципиальное правило:

**GPT-результаты не должны сохраняться автоматически как финальные данные без проверки человеком.**

Требуется human review / human confirmation:

- исходный raw text сохраняется отдельно;
- GPT JSON output должен быть доступен для проверки;
- пользователь должен подтвердить результат перед сохранением в case;
- будущие поля/таблицы должны поддерживать признаки `gpt_extracted`, `human_confirmed`, `confirmed_by_user_id`, `confirmed_at`.

Планируемая будущая таблица: `tblAIExtractions`.

## 14. Установка проекта

Создание виртуального окружения:

```bash
python -m venv .venv
```

Активация на Linux/macOS:

```bash
source .venv/bin/activate
```

Активация на Windows:

```bash
.venv\Scripts\activate
```

Установка зависимостей:

```bash
pip install -r requirements.txt
```

Создание seed-данных:

```bash
python -m app.seed
```

Запуск:

```bash
uvicorn app.main:app --reload
```

Альтернативный запуск:

```bash
python run.py
```

Windows-запуск из папки проекта с автоматическим освобождением порта `8000`:

```powershell
.\start.cmd
```

Если политика PowerShell разрешает запуск `.ps1`, можно использовать `.\start.ps1`. Если выполнение сценариев отключено, нужно запускать `.\start.cmd`.

## 15. Переменные окружения

Файл `.env.example` сейчас содержит:

```text
DATABASE_URL=sqlite:///./pv_system.db
```

Текущие переменные:

| Переменная | Назначение | Пример |
|---|---|---|
| `DATABASE_URL` | Connection string базы данных. | `sqlite:///./pv_system.db` |

Планируемые будущие переменные:

| Переменная | Назначение |
|---|---|
| `OPENAI_API_KEY` | API key для GPT/AI extraction. |
| `OPENAI_MODEL` | Модель для анализа входящих сообщений. |
| `AI_EXTRACTION_ENABLED` | Флаг включения AI extraction. |
| `ENVIRONMENT` | dev/test/prod окружение. |
| `SECRET_KEY` | Секрет для будущей авторизации/session logic. |

Секреты не должны храниться в Git и не должны попадать в `passport_PV1.md` в реальных значениях.

## 16. Запуск проекта

Локальный запуск через VS Code terminal:

```bash
cd pv_mvp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --reload
```

Упрощенный запуск на Windows:

```powershell
.\start.cmd
```

Скрипт запускается из папки проекта, закрывает процесс, занимающий порт `8000`, устанавливает зависимости из `requirements.txt` и стартует Uvicorn на `http://127.0.0.1:8000/`. `start.cmd` нужен для систем, где прямой запуск `.ps1` заблокирован execution policy.

Открыть сайт:

```text
http://127.0.0.1:8000
```

Открыть API docs:

```text
http://127.0.0.1:8000/docs
```

Health check:

```text
http://127.0.0.1:8000/health
```

## 17. Seed data

Seed-скрипт:

```bash
python -m app.seed
```

Seed создает:

- пользователя `admin@example.com` с ролью `admin`;
- партнера `PARTNER-001` / Global Pharma Partner Ltd.;
- вещество Ibuprofen, ATC `M01AE01`;
- продукт IbuRelief 200 mg Tablets;
- входящий safety report;
- triage как valid ICSR;
- case `CASE-2026-0001`;
- patient: female, 42 years;
- case product: IbuRelief, suspect, 200 mg oral;
- reaction: Rash, non-serious, recovering;
- planned submission для партнера.

Seed-скрипт должен быть идемпотентным: повторный запуск не должен создавать дубликаты основных seed-записей.

## 18. Миграция SQLite -> PostgreSQL

Текущий MVP использует SQLite только для разработки и локальной демонстрации.

План production:

- PostgreSQL как основная production DB;
- Alembic migrations для управления схемой;
- `DATABASE_URL` для переключения между SQLite и PostgreSQL;
- отсутствие бизнес-логики, завязанной на SQLite;
- SQLAlchemy ORM как единый слой доступа к данным;
- хранение enum-значений как строк для упрощения миграций;
- UUID-строки `String(36)` для переносимости;
- все новые модели должны быть PostgreSQL-friendly.

Пример будущего `DATABASE_URL`:

```text
DATABASE_URL=postgresql+psycopg://user:password@host:5432/pv_system
```

Перед миграцией нужно:

- подключить Alembic;
- создать baseline migration;
- проверить индексы и ограничения;
- проверить работу отношений SQLAlchemy;
- проверить типы `DateTime(timezone=True)`;
- добавить тесты на ключевые business flows.

## 19. Roadmap

| Этап | Содержание | Статус |
|---|---|---|
| MVP core | Tables, ORM, CRUD, UI, API, seed, audit trail | В основном реализовано |
| UI improvement | Filters, search, better validation messages, pagination | Запланировано |
| МФСФ / PSMF | Light MVP компонентов, версий, партнерской сборки и HTML preview; полноценный PSMF Builder остается будущим расширением | Light MVP реализован |
| PBRER / PSUR | Schedule, reports, linked products/cases/literature | Запланировано |
| RMP | RMP records, safety concerns | Запланировано |
| Literature | MVP: plans, search log, publications/results, document attachments, ICSR draft creation, PSUR/PSMF/RMP links, CSV export, audit trail | Light MVP реализован |
| Signal detection | Aggregation, line listings, signal workflows | Запланировано |
| GPT extraction | AI extraction from safety reports, editable review form | Запланировано |
| PostgreSQL migration | Alembic, PostgreSQL connection, migration scripts | Запланировано |
| Role-based access control | MVP users/roles/permissions реализован; production login/session, более тонкая матрица доступа и hardening audit остаются в развитии | Частично реализовано |
| Access import | Импорт данных из старой Microsoft Access базы | Запланировано |

## 20. Правила разработки

Обязательные правила:

- не использовать raw SQL в бизнес-логике;
- не смешивать UI, CRUD и ORM models в одном файле;
- все изменения структуры БД отражать в `app/models.py` и `passport_PV1.md`;
- все новые endpoints документировать в `passport_PV1.md`;
- все новые таблицы документировать в `passport_PV1.md`;
- все изменения бизнес-процессов документировать в `passport_PV1.md`;
- сохранять GxP-подход и auditability;
- не сохранять GPT-результаты как финальные данные без human confirmation;
- SQLite считать dev/MVP базой, а не production-решением;
- бизнес-логику писать PostgreSQL-ready;
- все новые зависимости добавлять в `requirements.txt` и отражать в паспорте.

Рекомендуемая модульность:

- ORM: `app/models.py`;
- Pydantic schemas: `app/schemas.py`;
- business/service logic: `app/crud.py` или отдельные service modules при росте проекта;
- audit logic: `app/audit.py`;
- route handlers: `app/routers/*.py`;
- UI templates: `app/templates/*.html`;
- static styles/assets: `app/static/*`;
- AI/GPT: `app/ai/*`.

## ОБЯЗАТЕЛЬНО: passport_PV1.md должен постоянно обновляться

`passport_PV1.md` является главным источником актуальной информации о проекте.

При любом изменении проекта необходимо сразу обновить `passport_PV1.md`.

Нельзя изменять код, структуру проекта, базу данных, API, бизнес-логику или зависимости без обновления `passport_PV1.md`.

Если добавлена новая таблица — она должна быть описана в `passport_PV1.md`.

Если добавлен новый endpoint — он должен быть описан в `passport_PV1.md`.

Если изменен бизнес-процесс — он должен быть описан в `passport_PV1.md`.

Если добавлена новая зависимость — она должна быть указана в `passport_PV1.md`.

Если изменился способ запуска проекта — `passport_PV1.md` должен быть обновлен.

Если добавлен новый модуль, страница, сервис или AI-функция — `passport_PV1.md` должен быть обновлен.

Любой Pull Request, commit или изменение считается неполным, если `passport_PV1.md` не отражает актуальное состояние проекта.

Если Codex или другой AI-ассистент вносит изменения в проект, он обязан проверить, нужно ли обновить `passport_PV1.md`, и при необходимости обновить его в том же изменении.

## 21.1. Модуль сверки сообщений с партнерами

Добавлен модуль **«Сверка с партнерами» / Partner Reconciliation** для подготовки, проверки, сохранения и подтверждения сверки ICSR-сообщений с партнерами.

Ключевые файлы:

- `app/reconciliation.py` — сервис формирования сверки, выборка кейсов и сообщений партнера, автоматическое сопоставление.
- `app/reconciliation_documents.py` — сохранение сформированного документа сверки в `uploads/reconciliations`, генерация XLSX и lightweight DOCX без дополнительных зависимостей.
- `app/reconciliation_excel.py` — генерация `.xlsx` без внешних зависимостей.
- `app/outlook_service.py` — Microsoft Graph OAuth 2.0 и операции Outlook draft/send/attachment для MVP.
- `app/routers/partner_reconciliation.py` — HTML routes и JSON API для сверок.
- `app/templates/partner_reconciliation.html` — Access-like UI: партнеры, период, контакт, поиск, фильтр статуса, таблицы, расхождения, подтверждение и блок Outlook.

Новые таблицы:

- `tblPartnerReconciliations` — заголовок сверки: партнер, контакт, период, тип сверки, язык, статус сверки, подготовил, контактные snapshot-поля, счетчики строк, сформированный документ (`document_path`, `document_filename`, `document_format`), email preview (`email_subject`, `email_body`, `email_to`, `email_cc`), Outlook status/message/link/error, даты `generated_at`, `draft_created_at`, `sent_at`, подтверждение и комментарии.
- `tblPartnerReconciliationItems` — строки сверки: внутренний case, номер сообщения партнера, ЛП, сторона источника, тип сообщения, даты получения/передачи, НР, seriousness, статус сверки, confidence/match method, комментарий reviewer, discrepancy flag/comment, подтверждение и display snapshot-поля.
- `tblContractContacts` расширена полями для выбора получателей сверки: `is_pv_contact`, `is_reconciliation_recipient`, `cc_reconciliation`, `is_primary`, `contact_type`, `comments`; email может отсутствовать, такие контакты не попадают в Outlook To/Cc.

Основные web routes:

- `GET /partner-reconciliation` — вкладка сверки с партнерами, генерация RU/EN preview, просмотр сохраненной сверки.
- `POST /partner-reconciliation/save` — сохранение результата и генерация файла сверки (`generated`).
- `POST /partner-reconciliation/{reconciliation_id}/generate-document` — повторная генерация XLSX/DOCX документа сверки.
- `GET /partner-reconciliation/{reconciliation_id}/document` — скачивание сохраненного документа сверки.
- `GET /outlook/auth` и `GET /outlook/callback` — OAuth 2.0 delegated authorization для Microsoft Graph.
- `POST /partner-reconciliation/{reconciliation_id}/outlook-draft` — создание черновика Outlook через Microsoft Graph и прикрепление документа сверки.
- `POST /partner-reconciliation/{reconciliation_id}/outlook-send` — отправка ранее созданного черновика через Outlook после явного подтверждения пользователя.
- `POST /partner-reconciliation/{reconciliation_id}/confirm` — подтверждение сверки; совпавшие строки переводятся в `confirmed`.
- `POST /partner-reconciliation/items/{item_id}` — ручная проверка строки: статус, комментарий, связь с внутренним case.
- `GET /partner-reconciliation/export` — экспорт preview или сохраненной сверки в Excel.

JSON API:

- `GET /api/partner-reconciliations`
- `GET /api/partner-reconciliations/{reconciliation_id}`

Статусы строк сверки:

- `matched`
- `missing_in_our_database`
- `missing_in_partner_data`
- `duplicate`
- `follow_up`
- `not_valid_icsr`
- `requires_review`
- `confirmed`

Статусы записи сверки:

- `draft`
- `generated`
- `outlook_draft_created`
- `sent`
- `confirmed`
- `discrepancy`
- `closed`
- `error`

Автоматическое сопоставление выполняется по правилам:

- точное совпадение внутреннего case ID;
- совпадение номера партнера / worldwide case ID;
- совпадение связки препарат + дата + пациент + нежелательное событие;
- похожее описание для возможных дублей;
- follow-up по исходному case;
- отсутствие совпадения переводит строку в расхождение (`missing_in_partner_data`, `missing_in_our_database` или `requires_review`).

Excel export содержит 5 листов:

- `Cover` / `Обложка`
- `Cases from our company`
- `Cases from partner`
- `Discrepancies`
- `Sign-off`

Outlook-интеграция:

- используется Microsoft Graph, не локальный Outlook-клиент;
- OAuth 2.0 delegated permissions настраиваются через `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, `MICROSOFT_REDIRECT_URI`, `MICROSOFT_SCOPES`;
- минимальные scopes MVP: `offline_access`, `User.Read`, `Mail.ReadWrite`, `Mail.Send`;
- получатели To берутся из активных контактов партнера с `is_reconciliation_recipient = true` и заполненным email;
- получатели Cc берутся из активных контактов партнера с `cc_reconciliation = true` и заполненным email;
- если To-получателей нет, черновик Outlook не создается;
- письмо не отправляется автоматически после генерации документа, отправка доступна только отдельным действием с confirmation dialog;
- access/refresh tokens не пишутся в audit log или БД; в MVP токены хранятся только в памяти процесса;
- вложение до 3 МБ отправляется как Graph `fileAttachment`; для больших файлов MVP показывает ошибку о неподдерживаемом размере.

Seed data расширены партнерами, продуктами, контактами, ICSR-кейсами, follow-up, partner-only сообщением, дублем и невалидным сообщением для демонстрации сверки.

## 21.2. MVP users, roles and permissions

Добавлен первый рабочий слой **Users & Roles / RBAC MVP**.

Ключевые файлы:

- `app/auth.py` — access middleware, текущий пользователь из cookie/query, Jinja helpers `has_permission`, `current_user`, `current_roles`, dependencies `require_permission` и `require_any_permission`.
- `app/rbac.py` — каталог базовых permissions, бизнес-роли MVP и bootstrap справочников доступа.
- `app/routers/users_roles.py` — HTML и API маршруты управления пользователями, ролями и permissions.
- `app/templates/users_roles.html` — интерфейс управления пользователями, множественными ролями и матрицей permissions.

Новые таблицы:

- `tblRoles`
- `tblPermissions`
- `tblUserRoles`
- `tblRolePermissions`

Базовые роли MVP:

- `admin` — Admin / Системный администратор.
- `pv_responsible` — PV Responsible / УЛФ.
- `deputy_pv_responsible` — Deputy PV Responsible / Заместитель УЛФ.
- `qa_reviewer` — QA Reviewer / Руководитель качества.
- `readonly_auditor` — Read-only Auditor / Viewer.
- `executive_approver` — Executive Approver / Подписант.

Минимальные permissions:

- `view`
- `create`
- `edit`
- `soft_delete`
- `approve`
- `comment`
- `upload`
- `export`
- `audit_view`
- `manage_users`
- `manage_reference_data`
- `manage_system_settings`

Особенности реализации MVP:

- один пользователь может иметь несколько ролей одновременно через `tblUserRoles`;
- доступ вычисляется через объединение permissions всех активных ролей пользователя;
- legacy-поле `tblUsers.role` оставлено для совместимости с seed и старым кодом, но не является основным механизмом доступа;
- текущий пользователь выбирается через cookie `pv_user_id`; полноценный login/session пока не реализован;
- Admin получает все permissions и автоматически восстанавливает недостающие permissions при bootstrap, чтобы снизить риск lockout;
- Users & Roles доступен по `/users-roles` только при `manage_users`;
- изменение permissions роли и назначение/снятие ролей фиксируются в audit trail;
- soft delete поля `deleted_at`, `deleted_by`, `delete_reason` добавлены в общий `CommonMixin`; archive пользователя реализован в Users & Roles.

## 21.3. Light MVP МФСФ / PSMF

Добавлен рабочий light MVP-блок **«МФСФ / PSMF»** для демонстрации мастер-файла системы фармаконадзора как набора управляемых компонентов, а не одного Word-документа.

Ключевые файлы:

- `app/psmf.py` — сервисная логика PSMF: списки, статистика, статусные переходы, версия, seed, сборка партнерского МФСФ и HTML-экспорт.
- `app/routers/psmf.py` — HTML routes `/psmf`, действия над компонентами, генерация preview и скачивание HTML.
- `app/templates/psmf.html` — UI с вкладками Overview, Components, Partner PSMF и Audit.
- `app/models.py` — ORM-модели `PSMFComponent` и `PSMFComponentVersion`.

Новые таблицы:

- `psmf_components` — компонент МФСФ с кодом, названием, типом `MAIN_SECTION`/`ANNEX`, scope `GLOBAL`/`PARTNER_SPECIFIC`, optional partner, описанием, статусом и текущей версией.
- `psmf_component_versions` — версии текстов компонентов с content, status, change summary, created/approved metadata и lock-флагом.

Статусы light MVP:

- `draft` — черновик, текст можно редактировать и сохранить.
- `under_review` — на проверке, текст read-only, доступно утверждение.
- `approved` — утверждено, прямое редактирование запрещено, доступно создание новой версии.

Seed при `init_db()` идемпотентно создает:

- партнеров `Партнер 1` и `Партнер 2`, если их еще нет;
- общий раздел МФСФ `1` в статусе `approved`;
- общее приложение `Приложение А-1` в статусе `approved`;
- партнер-специфичное приложение `Приложение Б-1.1` для `Партнер 1` в статусе `draft`;
- audit-события создания компонентов с `source_module = "PSMF"`.

Логика партнерской сборки:

- для любого партнера включаются все `GLOBAL`-компоненты со статусом `approved`;
- дополнительно включаются `PARTNER_SPECIFIC`-компоненты выбранного партнера;
- для партнера без partner-specific приложения показывается предупреждение;
- для неутвержденных компонентов показывается предупреждение;
- preview можно скачать как простой HTML-файл; PDF/DOCX пока не реализуются.

Audit:

- используется единый `tblAuditTrail`, отдельный `psmf_audit_trail` не создавался;
- PSMF-события пишутся с `source_module = "PSMF"`;
- типы объектов: `PSMF_COMPONENT`, `PSMF_VERSION`, `PARTNER_PSMF_PREVIEW`, `PARTNER_PSMF_EXPORT`.

## 21.4. Documents / Attachments MVP

Раздел **Documents** переведен из заглушки в рабочий MVP-реестр вложений. Файлы загружаются через HTML multipart form, сохраняются локально в `uploads/documents`, а в `tblAttachments` хранится запись с display name, типом, связью с case или safety report, MIME-типом, размером, SHA-256 checksum и путем хранения. Скачивание выполняется через `/documents/{document_id}/download`; путь дополнительно проверяется, чтобы отдавать только файлы из разрешенной директории. Создание и скачивание документов пишутся в `tblAuditTrail` с `source_module = "Documents"`.

Для production остаются отдельные вопросы: вынести стратегию хранения в конфигурацию, определить лимиты размера/типы файлов, добавить антивирусную проверку и immutable/versioned storage для GxP-сценариев.

## 22. Журнал изменений

| Дата | Версия / этап | Что изменено | Автор / источник изменения |
|---|---|---|---|
| 2026-06-11 | 0.1 / MVP core | Создан MVP FastAPI + SQLAlchemy + SQLite: ORM-модели, CRUD, UI, API, seed, audit trail, AI stubs, CSV export. | Codex по ТЗ пользователя |
| 2026-06-11 | 0.1 / project passport | Создан `passport_PV1.md` как главный паспорт проекта с архитектурой, таблицами, endpoints, roadmap и правилами обновления. | Codex по запросу пользователя |
| 2026-06-11 | 0.2 / UI localization and branding | Добавлены RU/EN интерфейс с русским языком по умолчанию, переключатель языка, общий Jinja i18n helper, cookie языка, логотип ARS PharmRussia и стили по брендбуку. | Codex по запросу пользователя |
| 2026-06-11 | 0.2 / Windows start script | Добавлен `start.ps1` для запуска сайта из папки проекта на `http://127.0.0.1:8000/` с автоматическим закрытием процесса, занимающего порт `8000`. | Codex по запросу пользователя |
| 2026-06-11 | 0.2 / Windows execution policy wrapper | Добавлен `start.cmd`, который запускает `start.ps1` с `-ExecutionPolicy Bypass` для случаев, когда прямой запуск PowerShell-сценариев отключен системой. | Codex по запросу пользователя |
| 2026-06-11 | 0.3 / partners and contract data | Обновлена вкладка партнеров до полей код, название, статус и частота сверки; добавлены договоры, контактные лица по договорам, отдельная вкладка веществ и управление связью вещества с ЛП. | Codex по запросам пользователя |
| 2026-06-11 | 0.4 / partner reconciliation | Добавлена вкладка «Сверка с партнерами»: генерация RU/EN формы, связь с партнерами/ЛП/контактами/ICSR, автоматическое сопоставление, ручная проверка строк, сохранение draft, подтверждение, API и Excel export. | Codex по запросу пользователя |
| 2026-06-14 | 0.5 / sidebar shell and placeholders | Верхняя навигация заменена единым боковым layout; добавлены пункты меню для ключевых MVP-разделов и страницы-заглушки для PSUR/PBRER, RMP, PSMF, Documents, Audit Log, Users & Roles и Settings. | Codex по запросу пользователя |
| 2026-06-14 | 0.6 / compact two-level sidebar | Левое меню переработано в компактную двухуровневую навигацию: главные группы и вложенные пункты выбранной группы, убрана внутренняя прокрутка сайдбара, добавлен режим сворачивания до иконок и сохранен мобильный drawer. | Codex по запросу пользователя |
| 2026-06-14 | 0.7 / RBAC MVP | Добавлены таблицы roles, permissions, user_roles и role_permissions; реализованы несколько ролей у пользователя, permission-aware навигация и формы, раздел Users & Roles, текущий пользователь через cookie для MVP-тестирования, серверные permission checks для ключевых действий и audit для назначения ролей/permissions. | Codex по запросу пользователя |
| 2026-06-15 | 0.8 / Audit Log | Расширен существующий audit trail до рабочего раздела Audit Log: добавлены поля changed_by, changed_at, source_module и comment, SQLite-дорасширение схемы, фильтры по пользователю/модулю/действию/дате, поиск, раскрытие деталей события и более точное логирование old/new значений для справочников, ИСНР, сверок, документов, пользователей и ролей. | Codex по запросу пользователя |
| 2026-06-15 | 0.9 / PSMF light MVP | Добавлен рабочий блок «МФСФ / PSMF»: таблицы `psmf_components` и `psmf_component_versions`, 3 демо-компонента, вкладки обзора/компонентов/партнерского МФСФ/аудита, статусные действия, preview-сборка для партнера, HTML-экспорт и audit-события с `source_module = "PSMF"`. | Codex по запросу пользователя |
| 2026-06-15 | 1.0 / Documents and link deduplication | Исправлены проверки пункта 10 и 13: блокируются дубли `partner + product` в договорах и `partner + email` в контактах, раздел Documents поддерживает реальную загрузку файла, связь с case/safety report, скачивание, checksum/size metadata и audit-событие скачивания. | Codex по запросу пользователя |
| 2026-06-17 | 1.1 / Outlook Graph reconciliation | Модуль сверки с партнерами расширен: сохранение XLSX/DOCX документа сверки, поля Outlook/email в существующих таблицах, расширенные контактные флаги To/Cc, Microsoft Graph OAuth 2.0 delegated flow, создание Outlook draft с вложением, отправка только после подтверждения, audit-события и тесты. | Codex по запросу пользователя |
| 2026-06-22 | 1.2 / Literature Monitoring MVP | Добавлен рабочий модуль «Литературный мониторинг»: таблицы планов, журнала поисков, публикаций и product-link tables, основное меню, вкладки Plan/Journal/Results, вложения через Documents, создание черновика ИСНР из публикации, связи с PSUR/PSMF/RMP, CSV export, audit trail и тесты. | Codex по запросу пользователя |

## 23. Open questions

| Вопрос | Статус | Комментарий |
|---|---|---|
| Авторизация | Открыто | MVP RBAC реализован без настоящего login; нужно определить production auth mechanism: session, OAuth, JWT или другой подход. |
| Outlook / Microsoft Graph | Открыто | MVP поддерживает delegated OAuth и хранит токены только в памяти процесса; для production нужно определить защищенное per-user token storage, rotation/revocation и admin consent policy. |
| Роли пользователей | Частично закрыто | Есть отдельные `tblRoles`, `tblPermissions`, `tblUserRoles`, `tblRolePermissions` и раздел Users & Roles. Нужно уточнить более тонкую матрицу доступа по разделам/объектам перед production. |
| PostgreSQL | Открыто | Нужно подключить Alembic и проверить миграцию схемы. |
| Интеграция GPT | Открыто | Нужно определить provider, prompts, data model, human review workflow. |
| MedDRA | Открыто | Нужно решить источник словаря, лицензирование, coding workflow. |
| PBRER / PSUR | Открыто | Нужно уточнить scope первой версии periodic reports. |
| RMP | Открыто | Нужно определить минимальную модель RMP и safety concerns. |
| МФСФ / PSMF | Частично закрыто | Light MVP реализован; для production нужны роли PSMF Owner/Reviewer/QPPV, полный workflow, immutable snapshots и DOCX/PDF export. |
| Импорт Access базы | Открыто | Нужно получить структуру старой базы, mapping таблиц и правила очистки данных. |
| Attachments | Частично закрыто | MVP upload/download реализован с локальным хранилищем `uploads/documents`; для production нужно определить конфигурацию storage, лимиты, контроль типов файлов, антивирусную проверку и immutable/versioned хранение. |
| Audit immutability | Открыто | Нужно усилить защиту audit trail от изменения/удаления. |
| Тестирование | Открыто | Нужно добавить automated tests для ключевых workflows. |
| Валидация данных | Открыто | Требуется расширить бизнес-валидацию форм и API. |
