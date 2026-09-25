# STACK

Решение зафиксировано в `docs/decisions/ADR-001-stack.md` (accepted). Новая зависимость добавляется только с обоснованием в этом файле.

## Берём

| Слой | Выбор |
|---|---|
| Язык | Python 3.12 |
| Пакеты, окружение | uv |
| Линтер, форматтер | ruff (lint + format) |
| Типизация | mypy strict |
| Pre-commit | ruff, mypy, проверка TODO без номера issue |
| Telegram | aiogram 3 + aiogram_dialog (`/settings`) |
| Планировщик | APScheduler 3, расписания в таблице `schedules` |
| БД | Postgres 16 |
| Доступ к БД | SQLAlchemy 2 **Core** + asyncpg |
| Миграции | Alembic |
| Аналитика | pandas |
| Графики | matplotlib (Agg → BytesIO → `send_photo`) |
| Excel | xlsxwriter |
| Шаблоны отчётов | Jinja2 (`templates/*.ro.j2`, `templates/*.ru.j2`) |
| Конфиги | PyYAML (`config/*.yaml`) → pydantic-модели |
| Валидация, настройки | pydantic 2 + pydantic-settings |
| HTTP | httpx |
| LLM | anthropic SDK, ручной tool-use цикл |
| Логи | `logging` из stdlib с JSON-форматтером |
| Время | `zoneinfo` из stdlib, `Europe/Bucharest` всегда явно |
| Тесты | pytest, pytest-asyncio, testcontainers, respx, syrupy |
| Хостинг | VPS Hetzner CPX12 (Nuremberg) + Docker Compose; линейка CX недоступна, в ADR-001 записан CX22 |
| CI | GitHub Actions: ruff, mypy, pytest с testcontainers, сборка образа; без секретов |

## Этап 2 (не в v1)

| Что | Когда |
|---|---|
| Playwright for Python, контейнер `mefi-sync` | С источником B (Oferte/Contracte) |
| WeasyPrint, PDF для m19 | Этап 2. В MVP m19 отдаёт только Excel |

## Отвергнуто

| Что | Почему |
|---|---|
| ORM-слой SQLAlchemy (сессии, модели с relationship) | Запросы должны быть видны целиком: цифра в отчёте прослеживается до SQL без магии lazy-load |
| Redis | Очереди и расписания держит Postgres; второй stateful-сервис ради одного процесса не нужен |
| Агентные фреймворки, Claude Agent SDK | LLM только выбирает инструмент и параметры (инвариант 2); ручной цикл короче и прозрачнее |
| Text-to-SQL, PandasAI, Vanna | LLM не считает; см. `docs/research.md` §4 |
| structlog | JSON-форматтер поверх stdlib `logging` закрывает задачу без зависимости |
| factory_boy | Фабричные функции `make_lead(**overrides)` проще и типизируются mypy |
| Vercel и serverless | Нужен постоянный процесс для чата и долгие задачи (снапшоты, позже Playwright) |
| TypeScript-стек (grammY, pg-boss) | Единственный аргумент, общий стек с Davoq, не перевешивает pandas для аналитики; граница с Davoq через Postgres |

## Решения

**Python, а не TypeScript.** Вся аналитика это воронки, когорты, сравнение периодов: pandas делает это короче и привычнее для разработчика. Бот тонкий; если Davoq потребует единый стек, переписывается слой бота, а реестр метрик с эталонными тестами остаётся спецификацией.

**SQLAlchemy Core, а не ORM.** Отчёты читают снапшоты агрегирующими запросами. Core даёт типизированный построитель SQL без identity map и неявных запросов.

**Один сервис `app`.** aiogram, APScheduler и отправка в служебный бот живут в одном процессе. Compose v1: `app` + `postgres`, ежедневный `pg_dump` с хранением 30 дней. Бэкап это третий контейнер на образе `postgres:16` со скриптом `deploy/backup.sh`, без сторонних образов; свежесть дампа проверяет `app` после ежедневного отчёта. Порядок деплоя: `docs/deploy.md`. Делим `app` на `bot` и `scheduler`, только если появится вторая причина кроме «так было в ADR» (например, независимые рестарты или разная нагрузка).

**Postgres как единственное состояние.** Снапшоты, настройки, расписания, `report_runs`. Идемпотентность отправки и история воронки держатся на нём.

**stdlib где хватает.** `zoneinfo` вместо pytz, `logging` вместо structlog: меньше зависимостей, ничего не теряем.
