# PV1 — результат этапа 0

Дата: 10.11.2026. Проверки выполнены в этом сеансе на Windows, исключительно на синтетических данных. Это готовность локальной основы, не приёмка всей платформы или интеграции РЗН.

**Этап 0 выполнен.** Установка, реальный HTTP-вход и основной экран, четыре выбранных pytest-сценария, новая схема и обновление предыдущей ревизии проверены. Для R3 на Windows требуется короткий путь тестового хранилища.

## Рабочая копия и установка

- Путь: `C:\Users\skreshchanovskaia\Documents\ChatGPT\ФН разработка\PV1`.
- Origin: `https://github.com/pvtaisiya-droid/PV1.git`.
- Исходная ветка: `share/pv1-20261030`; её полученный SHA совпал с ранее проверенным: `c80347d42c2a8cb516aeab3eba384df1967df0f6`.
- Локальная ветка: `codex/rzn-as2-foundation`, создана заново от этого SHA. Коммитов, push, merge и публикации не выполнялось.
- В родительской папке был отдельный репозиторий без коммитов с незакоммиченными документами и `review-2026-11-10`; они сохранены без изменений. До клонирования копии приложения не было.
- Прочитаны корневой `AGENTS.md`, `README.md`, необходимая инструкция миграций, код установки и выбранных тестов. Других `AGENTS.md` в копии нет. `docs/audit-2026-09-21.md` и `docs/audit-fix-plan.md` отсутствуют; не восстанавливались.
- Python **3.12.14**, отдельная `.venv`, без системных site-packages; pip **25.0.1**. Lock-файлы проекта рассчитаны на Windows/Python 3.12. Версии зависимостей не изменены.

Выполненные команды получения копии и окружения (PowerShell):

```powershell
git clone --branch share/pv1-20261030 --single-branch https://github.com/pvtaisiya-droid/PV1.git PV1
$env:GIT_TERMINAL_PROMPT='0'
$env:GCM_INTERACTIVE='never'
git -c credential.helper= ls-remote https://github.com/pvtaisiya-droid/PV1.git refs/heads/share/pv1-20261030
git -C PV1 -c credential.helper= fetch origin share/pv1-20261030
git -C PV1 switch -c codex/rzn-as2-foundation --track origin/share/pv1-20261030
Set-Location 'C:\Users\skreshchanovskaia\Documents\ChatGPT\ФН разработка\PV1'
& 'C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1104-2876-0_26.1104.2876.0_x64__3k8sg7r9htsxt\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements-dev.lock.txt
.\.venv\Scripts\python.exe -m pip check
```

Dev lock включает runtime lock. Установка успешна; `pip check`: `No broken requirements found`. Системный `python.exe` из WindowsApps оказался неработающим псевдонимом, поэтому использован указанный runtime. Первоначальный сетевой запрос Git из sandbox не разрешил DNS; клонирование с сетевым доступом завершилось успешно. Во время его задержки дополнительный fetch без credential helper подтвердил SHA; попытка остановить уже завершившийся процесс ничего не изменила.

## Изоляция и воспроизводимые проверки

Все пути ниже относительно рабочей копии, кроме явно указанного `%LOCALAPPDATA%`. До импорта приложения и команд БД явно установлены `DATABASE_URL` и `PV_STORAGE_ROOT`.

| Назначение | База | Хранилище |
|---|---|---|
| Последняя успешная HTTP-проверка | `.tmp-stage-0/runtime-3/database.sqlite3` | `.tmp-stage-0/runtime-3/storage` |
| Успешная репетиция обновления | `.tmp-stage-0/upgrade-2/database.sqlite3` | `.tmp-stage-0/upgrade-2/storage` |
| Первая группа pytest | `.tmp-stage-0/pytest-1/**` | соответствующие `uploads`, `empty-storage` |
| HTTP-тесты pytest | `.tmp-stage-0/pytest-2/**` | соответствующие `uploads` |
| Успешный повтор R3 | `C:\Users\skreshchanovskaia\AppData\Local\Temp\pv1-s0-r3\**` | соответствующие `uploads` |

`tests/conftest.py` создаёт шаблон через `app.manage init-db`, копирует его в `tmp_path/pv_test.db`, устанавливает обе переменные до импорта и изолирует файлы каждого теста. Вход выполняется через `/login`; dev-переключатели исходной фикстуры не использованы для обхода входа. Реальный HTTP smoke выполнен в `prod` с обоими переключателями пользователей `false`.

Команды из корня PV1:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m tools.stage0_verify smoke --root .tmp-stage-0/runtime-3
.\.venv\Scripts\python.exe -m tools.stage0_verify upgrade --root .tmp-stage-0/upgrade-2
```

Для повторения выбрать **новые** имена подкаталогов внутри `.tmp-stage-0`: скрипт отказывается перезаписывать существующий каталог. Smoke выполняет штатные `python -m app.manage init-db` и `check-db`, затем штатную функцию `bootstrap_admin` с одноразовыми учётными данными в памяти. Пароль передаётся дочернему процессу через stdin, не через аргументы командной строки, Git или отчёт. Uvicorn запускается на свободном loopback-порту. Проверяются `/health` 200, неавторизованный `/` 303, `/login` 200, неверный пароль 401, правильный вход 303, главный экран 200 и запись активной сессии в БД. После проверки останавливается только дерево созданного процесса и проверяется освобождение порта.

Репетиция создаёт настоящую схему `20261002_0027` командой `python -m alembic upgrade 20261002_0027`, проверяет исходную ревизию и отсутствие нового столбца, добавляет два связанных компонента PSMF и две их текстовые версии. Далее выполняет `python -m app.manage upgrade-db` и `check-db`. Результат: **20261002_0028 (head)**, все прежние поля четырёх строк неизменны, `row_version=1` у обеих версий, неверная FK-ссылка отклоняется, `integrity_check=ok`, `foreign_key_check` пуст. `stamp`, downgrade и перебор исторических ревизий не использовались.

## Выбранные pytest и фактические результаты

Проверялись только оставшиеся риски: сравнение новой схемы с моделями, отзыв сессии, связанный ручной случай, импорт R3 со встроенным файлом. Названия и реализации тестов предварительно прочитаны.

Общие настройки перед pytest:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
$env:DATABASE_URL='sqlite:///C:/Users/skreshchanovskaia/Documents/ChatGPT/ФН разработка/PV1/.tmp-stage-0/pytest-guard.sqlite3'
$env:PV_STORAGE_ROOT=Join-Path $PWD '.tmp-stage-0/pytest-guard-storage'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
```

Фактически выполненные запуски:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -x --basetemp=.tmp-stage-0/pytest-1 tests/test_migrations.py::test_alembic_upgrades_empty_database_to_head tests/test_authentication.py::test_logout_revokes_session tests/test_manual_case_entry.py::test_manual_case_creates_complete_linked_icsr_atomically tests/test_e2b_import.py::test_r3_embedded_base64_document_is_saved_as_attachment
.\.venv\Scripts\python.exe -m pytest -v -x -o faulthandler_timeout=30 --basetemp=.tmp-stage-0/pytest-2 tests/test_authentication.py::test_logout_revokes_session tests/test_manual_case_entry.py::test_manual_case_creates_complete_linked_icsr_atomically tests/test_e2b_import.py::test_r3_embedded_base64_document_is_saved_as_attachment
$stage0Temp=Join-Path ([System.IO.Path]::GetTempPath()) 'pv1-s0-r3'
.\.venv\Scripts\python.exe -m pytest -v -x -o faulthandler_timeout=30 --basetemp=$stage0Temp tests/test_e2b_import.py::test_r3_embedded_base64_document_is_saved_as_attachment
```

`--basetemp` pytest может очистить указанный каталог: при повторении использовать новый, выделенный только для этой проверки путь. Последние два запуска выполнялись вне sandbox, где системный Temp имеет короткий путь, указанный в таблице.

| Тест | Результат этого сеанса |
|---|---|
| `test_alembic_upgrades_empty_database_to_head` | Прошёл в первом запуске; проверяет head, поля, PK/FK, уникальные ограничения, индексы и соответствие моделям. Затем запуск остановлен на следующем сценарии; единого итогового summary у него нет. |
| `test_logout_revokes_session` | PASSED во втором запуске |
| `test_manual_case_creates_complete_linked_icsr_atomically` | PASSED во втором запуске: случай, пациент, препарат, реакция и взаимосвязанное сообщение |
| `test_r3_embedded_base64_document_is_saved_as_attachment` | Сначала FAILED из-за длинного пути Windows; повтор отдельно с коротким Temp — **1 passed in 14.67s**, подтверждены импорт и точные байты вложения |

Второй запуск: **2 passed, 1 failed in 24.56s**. Итого четыре выбранных сценария получили успешный результат, но не в одном общем зелёном прогоне. Полный pytest, конкурентные модули, нагрузка и матрица ролей не запускались.

Существенные неудачные попытки и исправления:

- Первоначальный HTTP smoke (`runtime`) получил `WinError 10013` на сокете sandbox и ограниченно завершился по таймауту. Повтор (`runtime-2`) вне sandbox прошёл. Аналогично первый pytest остановлен после завершения миграционного теста при задержке TestClient; три оставшихся сценария перенесены вне sandbox, где задержка исчезла.
- Первая репетиция (`upgrade`) остановилась до обновления на `NOT NULL psmf_components.component_type`: вспомогательный скрипт не задавал обязательные значения при прямом SQL. Скрипт исправлен; повтор (`upgrade-2`) прошёл. Код миграций не менялся.
- R3 в длинном каталоге pytest получил `WinError 206`. Короткий отдельный системный Temp устранил проблему без изменения приложения, теста и проверок. Длинные пути хранилища на Windows остаются эксплуатационным ограничением, требующим отдельного решения при необходимости.
- В smoke-скрипте уточнена остановка Windows venv launcher вместе с дочерним интерпретатором; повторён только затронутый smoke (`runtime-3`): **PASS**, порт `55943`, launcher PID `32972`, сервер PID `48884`. Дерево остановлено, порт освобождён. Код выхода остановленного процесса `1` получен от принудительного завершения Windows; сам скрипт завершился с кодом `0` после проверок порта и БД.

Локальные журналы: `.tmp-stage-0/smoke-2.log`, `smoke-3.log`, `upgrade.log`, `upgrade-2.log`, `pytest-1.log`, `pytest-2.log`, `pytest-r3.log`; HTTP access log — в соответствующем `runtime*/uvicorn.log`. Все они исключены из Git.

## Повторный запуск и продолжение

Запуск проверенной локальной базы из PowerShell:

```powershell
Set-Location 'C:\Users\skreshchanovskaia\Documents\ChatGPT\ФН разработка\PV1'
$env:PYTHONUTF8='1'
$env:DATABASE_URL='sqlite:///C:/Users/skreshchanovskaia/Documents/ChatGPT/ФН разработка/PV1/.tmp-stage-0/runtime-3/database.sqlite3'
$env:PV_STORAGE_ROOT=Join-Path $PWD '.tmp-stage-0/runtime-3/storage'
$env:PV_APP_MODE='prod'
$env:PV_USER_SWITCH_ENABLED='false'
$env:PV_ALLOW_QUERY_USER_SWITCH='false'
$env:PV_AUTH_COOKIE_SECURE='false'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 55943
```

Если порт занят, выбрать свободный; чужие процессы не останавливать. Сервер останавливается Ctrl+C. Одноразовый пароль smoke не сохранён. Чтобы войти самостоятельно, **до запуска сервера**, с теми же изолированными переменными, можно назначить свой пароль штатной командой восстановления тестового администратора:

```powershell
$stage0Login = & .\.venv\Scripts\python.exe -c "from app.database import SessionLocal; from app.models import User; db=SessionLocal(); print(db.query(User).filter(User.password_hash.is_not(None)).one().login); db.close()"
.\.venv\Scripts\python.exe -m app.manage recover-admin --login $stage0Login
Remove-Variable stage0Login
```

Команда восстановления здесь дана для продолжения; в текущей проверке не выполнялась. Ввод нового пароля интерактивный, без эха. Не запускать `init-db` повторно на существующей базе.

Добавлены только этот отчёт и `tools/stage0_verify.py`; код приложения, существующие тесты, миграции и зависимости не изменены. `.venv`, `.tmp-stage-0`, базы, логи и файлы импорта исключены существующим `.gitignore`; стандартные `pv_system.db` и `uploads` не создавались. Изменения оставлены незакоммиченными для следующего чата.

Следующее действие: продолжать от этой локальной ветки, сначала прочитав отчёт и `git status`; затем уточнить профиль РЗН и карту данных по этапу 1 предыдущего плана. Ранее неподтверждённый старый аудит не закрыт. Обновление реальной/неверсированной базы и старых ревизий, восстановление backup, конкурентные изменения и полная функциональность платформы этим этапом не подтверждаются. Внешние сервисы, РЗН и Outlook не подключались, сообщения не отправлялись, исходящий R3/AS2 не разрабатывался.
