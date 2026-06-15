# Паспорт проекта PV1

Дата актуализации: 2026-06-15
Текущая версия паспорта: 0.8
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
- ведение audit trail для ключевых действий.

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
| Web UI | Реализовано частично | Есть единый shell с компактной двухуровневой боковой навигацией, рабочие страницы Dashboard, Safety Reports/PV Intake, Cases/ICSRs, Partners, Products, Substances, Contracts, Contract contacts, Partner Reconciliation, Submissions; для будущих модулей добавлены страницы-заглушки. Интерфейс двуязычный RU/EN, русский язык по умолчанию. |
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
- literature module;
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
| `app/i18n.py` | RU/EN словарь интерфейса, выбор языка по query/cookie, Jinja helper для переводов и ссылок переключения языка. |
| `app/templating.py` | Единый `Jinja2Templates` для HTML UI с подключенными i18n globals. |
| `app/seed.py` | Создание тестовых данных. |
| `app/routers/*.py` | HTML routes и JSON API endpoints; `placeholders.py` содержит временные страницы для будущих разделов. |
| `app/templates/*.html` | Jinja2 templates для web UI. |
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
| `tblContracts` | Договоры с партнерами по ЛП. | M-1 к `tblPartners`, M-1 к `tblProducts`; статус "действителен сейчас" вычисляется по датам. |
| `tblContractContacts` | Контактные лица по договорам. | M-1 к `tblPartners`; хранит ФИО, email, должность, актуальность. |
| `tblSafetyReports` | Входящие safety reports до создания ICSR case. | Может быть связан с partner и 0..1 case. |
| `tblCases` | Центральная таблица ICSR cases. | Связана с patients, case products, reactions, follow-ups, attachments, submissions, audit. |
| `tblPatients` | Пациенты в составе case. | M-1 к `tblCases`. |
| `tblCaseProducts` | Препараты в конкретном case. | M-1 к `tblCases`, опционально к `tblProducts`. |
| `tblReactions` | Нежелательные реакции/adverse events. | M-1 к `tblCases`. |
| `tblCaseProductReactionAssessments` | Оценка связи препарат-реакция. | Связывает `tblCaseProducts` и `tblReactions`. |
| `tblFollowUps` | Follow-up информация по case. | M-1 к `tblCases`. |
| `tblAttachments` | Метаданные вложений. | Может ссылаться на case или safety report. |
| `tblSubmissions` | Отправки наружу. | В MVP связана с case; оставлены поля `pbrer_id`, `rmp_id` для будущего. |
| `tblAuditTrail` | Audit trail / Audit Log. | Логирует действия по entity, case, user/changed_by, changed_at, source_module, old/new values и comment. |

Ключевые индексы реализованы для:

- `tblCases`: case number, worldwide ID, partner, dates, workflow status, assignee, seriousness, country, composite indexes;
- `tblSafetyReports`: report number, received date, source, partner, triage status;
- `tblProducts`: product code, name, normalized name, authorization fields;
- `tblSubstances`: substance name, normalized name, INN, ATC, CAS;
- `tblReactions`: case, reported term, MedDRA PT/SOC, seriousness;
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
| Safety Reports / PV Intake | `/safety-reports` | Список и форма создания входящих reports. В боковой навигации показан как PV Intake. |
| Safety Report Detail / Triage | `/safety-reports/{report_id}` | Просмотр raw text, minimum criteria, triage, создание case. |
| Cases / ICSRs | `/cases` | Список cases/ICSRs, экспорт CSV, переход к detail. В боковой навигации показан как ICSRs. |
| New Case | `/cases/new` | Ручное создание case. |
| Case Detail | `/cases/{case_id}` | Metadata, patient/product/reaction forms, follow-ups, submissions, audit trail. |
| Partners | `/partners` | Список и форма создания партнеров. |
| Products | `/products` | Список products и форма добавления ЛП. |
| Substances | `/substances` | Список веществ, добавление вещества и связь вещества с ЛП. |
| Contracts | `/contracts` | Список договоров, создание договора с привязкой к партнеру и ЛП, автоматический статус действительности. |
| Contract contacts | `/contract-contacts` | Список контактных лиц по договорам и создание контакта с привязкой к партнеру. |
| Submissions | `/submissions` | Список submissions, создание submission для case, изменение статуса. |
| PSUR / PBRER | `/psur` | Страница-заглушка для будущего модуля ПООБ/PSUR/PBRER. |
| RMP | `/rmp` | Страница-заглушка для будущего модуля ПУР/RMP. |
| PSMF | `/psmf` | Страница-заглушка для будущего модуля МФСФ/PSMF. |
| Documents | `/documents` | Страница-заглушка для будущего реестра документов и вложений. |
| Audit Log | `/audit-log` | Страница-заглушка для будущего единого журнала действий. |
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

### Submissions

| Method | Endpoint |
|---|---|
| `GET` | `/api/submissions` |
| `POST` | `/api/submissions` |
| `PATCH` | `/api/submissions/{submission_id}/status` |

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
| PBRER / PSUR | Schedule, reports, linked products/cases/literature | Запланировано |
| RMP | RMP records, safety concerns | Запланировано |
| Literature | Sources, articles, literature screening | Запланировано |
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
- `app/reconciliation_excel.py` — генерация `.xlsx` без внешних зависимостей.
- `app/routers/partner_reconciliation.py` — HTML routes и JSON API для сверок.
- `app/templates/partner_reconciliation.html` — Access-like UI: партнеры, период, контакт, поиск, фильтр статуса, таблицы, расхождения, подтверждение.

Новые таблицы:

- `tblPartnerReconciliations` — заголовок сверки: партнер, контакт, период, язык, статус сверки, подготовил, контактные snapshot-поля, счетчики строк, подтверждение.
- `tblPartnerReconciliationItems` — строки сверки: внутренний case, номер сообщения партнера, ЛП, сторона источника, тип сообщения, даты получения/передачи, НР, seriousness, статус сверки, confidence/match method, комментарий reviewer, подтверждение и display snapshot-поля.

Основные web routes:

- `GET /partner-reconciliation` — вкладка сверки с партнерами, генерация RU/EN preview, просмотр сохраненной сверки.
- `POST /partner-reconciliation/save` — сохранение результата как `draft`.
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

## 23. Open questions

| Вопрос | Статус | Комментарий |
|---|---|---|
| Авторизация | Открыто | MVP RBAC реализован без настоящего login; нужно определить production auth mechanism: session, OAuth, JWT или другой подход. |
| Роли пользователей | Частично закрыто | Есть отдельные `tblRoles`, `tblPermissions`, `tblUserRoles`, `tblRolePermissions` и раздел Users & Roles. Нужно уточнить более тонкую матрицу доступа по разделам/объектам перед production. |
| PostgreSQL | Открыто | Нужно подключить Alembic и проверить миграцию схемы. |
| Интеграция GPT | Открыто | Нужно определить provider, prompts, data model, human review workflow. |
| MedDRA | Открыто | Нужно решить источник словаря, лицензирование, coding workflow. |
| PBRER / PSUR | Открыто | Нужно уточнить scope первой версии periodic reports. |
| RMP | Открыто | Нужно определить минимальную модель RMP и safety concerns. |
| Импорт Access базы | Открыто | Нужно получить структуру старой базы, mapping таблиц и правила очистки данных. |
| Attachments | Открыто | Сейчас реализованы метаданные; нужна стратегия хранения файлов. |
| Audit immutability | Открыто | Нужно усилить защиту audit trail от изменения/удаления. |
| Тестирование | Открыто | Нужно добавить automated tests для ключевых workflows. |
| Валидация данных | Открыто | Требуется расширить бизнес-валидацию форм и API. |
