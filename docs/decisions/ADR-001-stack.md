# ADR-001 · Стек и хостинг

Статус: **accepted** (24.09.2026, `/rigorous teach`). Подробности выбора — `STACK.md`.

## Контекст

Бот для одного тенанта (Sofabelle) с перспективой стать продуктом Davoq «Asistent CRM». Аналитика — pandas-стиль (воронки, когорты, сравнение периодов), графики PNG в Telegram, Excel, планировщик, LLM tool calling; позже Playwright для mefi и PDF. Разработчик один, junior-fullstack с Claude Code. Стек Davoq — Next.js/TypeScript/Postgres на Vercel.

## Варианты

| | Python | TypeScript |
|---|---|---|
| Аналитика | pandas — короче и привычнее (весь текущий CRM-анализ на нём) | ручная агрегация в SQL или danfo.js |
| Telegram | aiogram 3 + aiogram_dialog | grammY + @grammyjs/menu |
| Планировщик | APScheduler + таблица schedules | pg-boss |
| Графики | matplotlib | Vega-Lite → resvg |
| Excel / PDF | xlsxwriter / WeasyPrint | exceljs / Playwright page.pdf |
| Playwright | есть | есть (и уже нужен для PDF) |
| LLM | anthropic SDK | @anthropic-ai/sdk |
| Перенос в Davoq | отдельный сервис рядом с Next.js; общая БД | единый стек, общие типы |
| Скорость для этого разработчика | выше | ниже |

## Решение

Python 3.12 для v1: aiogram 3 + aiogram_dialog, APScheduler 3, SQLAlchemy 2 Core + asyncpg + Alembic, Postgres 16, pandas, matplotlib, xlsxwriter, Jinja2, PyYAML, pydantic 2 + pydantic-settings, httpx, anthropic SDK (ручной tool-use цикл), stdlib `logging` (JSON) и `zoneinfo`. Инструменты: uv, ruff, mypy strict, pre-commit. Полный список и отвергнутое — `STACK.md`.

Бот — отдельный сервис, общается с Davoq только через Postgres и внутренний HTTP. Если Davoq потребует единого стека — переписывается слой бота (тонкий), реестр метрик остаётся как спецификация с эталонными тестами.

## Хостинг

VPS Hetzner CX22 + Docker Compose. v1: два сервиса, `app` (aiogram + APScheduler + отправка в служебный бот, один процесс) и `postgres`. Бэкап Postgres — ежедневный `pg_dump`, хранить 30 дней. Vercel не подходит: нужен постоянный процесс для чата и долгие задачи. На этапе 2 добавляется контейнер `mefi-sync` (Playwright).

## Изменения относительно proposed

- **SQLAlchemy Core вместо ORM.** Цифра в отчёте должна прослеживаться до SQL без неявных запросов.
- **Один сервис `app` вместо `bot` и `scheduler`.** Второй процесс не нужен одному тенанту; делим, только если появится вторая причина кроме «так было в ADR».
- **Playwright и WeasyPrint перенесены на этап 2.** Playwright нужен только для источника B, которого в MVP нет; PDF для m19 тоже на этапе 2, в MVP m19 отдаёт только Excel. Без них образ `app` лёгкий.
- **`ops-bot` не отдельный сервис.** Отправка в служебный бот — часть `app`.

## Последствия

- Два стека в портфеле Davoq (TS фронт/портал, Python аналитика). Приемлемо при чёткой границе через БД.
- Playwright-образ тяжёлый (~1 GB) — на этапе 2 отдельный контейнер `mefi-sync`, `app` его не тянет.
