# Sofabelle CRM Digest Bot — стартовый набор для Claude Code

Скопируй содержимое в корень нового репозитория. Дальше — `docs/first-sessions.md`, шаг за шагом.

```
CLAUDE.md                     ← контекст для каждой сессии Claude Code: инварианты, ловушки API, «чего не делать»
.claude/settings.json         ← разрешения: тесты и git без вопросов; .env и записи в mefi запрещены
.env.example                  ← все переменные окружения; скопировать в .env и заполнить
config/status-mapping.yaml    ← статусы mefi → категории; шоурумы; источники; окна времени
config/modules.yaml           ← реестр 47 модулей отчётов с источниками и дефолтом enabled
docs/brief.md                 ← спецификация (вход для /rigorous shape)
docs/kpi-definitions.md       ← формулы 9 KPI + SPI, пороги, веса, эталон
docs/research.md              ← выжимка ресёрча: почему пишем сами, что берём
docs/decisions/ADR-001-stack.md ← стек и хостинг (proposed → accepted после teach)
docs/first-sessions.md        ← порядок сессий 0–7 с готовыми промптами
```

Чего здесь нет — и появится в первых сессиях:
- `PRINCIPLES.md`, `STACK.md`, `TESTING.md` — пишет `/rigorous teach`
- `docs/mefi-api-notes.md` — реальные id статусов/источников из разведки API
- `tests/fixtures/etalon-2026-05.json` — из `SB KPi.xlsx`; положи Excel в `docs/reference/` перед сессией 4
- `templates/*.ro.j2`, `*.ru.j2` — тексты отчётов

Что нужно от других людей (не блокирует сессии 0–7):
- директор Sofabelle: письмо в mefi про API для Oferte/Contracte/Facturi; доступы в Meta BM / Google Ads / GA4 / TikTok BC; документы себестоимости
- Serghei: сервисный read-only пользователь в mefi без 2FA; разведка источника B через Chrome; Excel 2024–2025

## Локальный прогон

Нужны Docker, uv и заполненный `.env` (см. `.env.example`, включая `POSTGRES_PASSWORD` и `DATABASE_URL` на localhost).

```
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m digest snapshot
uv run python -m digest report daily --date YYYY-MM-DD --dry-run
```

`snapshot` всегда снимает состояние mefi за сегодня по Бухаресту; строки Vizita, Oferta и Contract появятся в отчёте со второго дня подряд. `--dry-run` отправляет только в `TELEGRAM_TEST_CHAT_ID`.

## Деплой

VPS, Docker Compose, бэкапы, обновление и откат: `docs/deploy.md`. CI (`.github/workflows/ci.yml`) на каждый push и pull request: ruff, mypy, pytest с Postgres из testcontainers, сборка образа.
