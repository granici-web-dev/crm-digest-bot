# Первые сессии в Claude Code — порядок

Принцип: контекст → стандарты → план → код. Каждая сессия начинается с чтения `CLAUDE.md`; для крупных шагов — plan mode (Shift+Tab) до первого правки файла.

## Сессия 0 · Подготовка репозитория (без Claude Code)

```bash
mkdir sofabelle-digest && cd sofabelle-digest && git init
cp -r <этот набор>/* <этот набор>/.claude . && cp .env.example .env
npx skills add CoRLab-Tech/skills@rigorous
git add -A && git commit -m "chore: project setup kit"
```

Заполни `.env` (ключ `leads:read`, тестовый токен бота, `TELEGRAM_TEST_CHAT_ID`). Продовые значения — только на сервере.

## Сессия 1 · `/rigorous teach`

Отвечает на 15 вопросов, пишет `PRINCIPLES.md`, `STACK.md`, `TESTING.md`. Ориентиры для ответов — `docs/decisions/ADR-001-stack.md` и `docs/research.md` §«Рекомендованные стеки». Ключевые позиции:

- Python 3.12, aiogram 3, APScheduler, SQLAlchemy 2 + asyncpg + Alembic, pandas, matplotlib, anthropic, Playwright, httpx. Типизация — mypy strict. Линтер — ruff.
- Anti-choices: no ORM-magic (явные запросы), no Redis, no agent frameworks, no text-to-SQL в v1.
- Тесты: pytest; реальный Postgres в тестах (testcontainers), никаких моков БД; чистые unit-тесты для `metrics/`; эталонный фикстур обязателен.
- Комментарии — только WHY, по-русски.

После teach: `git commit -m "docs: engineering standards"`. Обнови статус ADR-001 на accepted.

## Сессия 2 · Разведка mefi API (одна сессия, без кода в репо)

Промпт:

> Прочитай CLAUDE.md и docs/brief.md §2. Сделай один POST /leads/search с per_page=3 и lifecycle=["active","lost","junk"], затем GET /leads/{id} для первого. Покажи: отдаёт ли search custom_fields; реальные id статусов и источников (нужны для фильтров); как выглядит поле Showroom в custom_fields. Ключ в .env. Ничего не записывай в mefi. Результат сохрани в docs/mefi-api-notes.md.

Результат — `docs/mefi-api-notes.md` с id статусов/источников и решением «нужен ли GET на каждый лид». Этот файл заменяет угадывание в коде.

## Сессия 3 · `/rigorous shape` — фундамент

Промпт:

> /rigorous shape Фундамент бота по docs/brief.md: схема Postgres (leads_snapshot с датой снапшота, statuses, settings, schedules, report_runs), клиент mefi leads:read с пагинацией и retry на 429, ежедневный снапшот, загрузка config/status-mapping.yaml с валидацией и категорией UNMAPPED, реестр модулей из config/modules.yaml. Без Telegram и без метрик. Учти docs/mefi-api-notes.md.

Согласуй бриф → `/rigorous craft` → `/rigorous critique`. Коммит.

## Сессия 4 · `/rigorous shape` — реестр метрик

> /rigorous shape Модуль metrics/ по docs/kpi-definitions.md: базовые множества, 9 KPI, SPI с весами и порогами, speed-to-lead, просроченные revenire, когорта, дельта. Чистые функции над DataFrame снапшота. Сначала перенеси лист 01_Input_Leads и 03_KPI_Agenti из SB KPi.xlsx в tests/fixtures/etalon-2026-05.json, затем /rigorous tdd: тест на эталон красный → зелёный.

Это ядро. Не идти дальше, пока эталон не сходится с Excel по всем 6 консультантам.

## Сессия 5 · `/rigorous shape` — планировщик и доставка

> /rigorous shape Планировщик по таблице schedules (APScheduler, Europe/Bucharest), отправка в Telegram (текст Markdown с лимитом 4096 → серия сообщений, фото через BytesIO, документы), служебный бот, DRY_RUN в тестовую группу, запись report_runs. Модули MVP из config/modules.yaml с enabled: true, шаблоны RO/RU в templates/.

## Сессия 6 · `/rigorous shape` — /settings

> /rigorous shape Команда /settings для admin по Telegram ID: aiogram_dialog, уровни Daily/Weekly/Monthly/Yearly/Chat → модули ✅/⬜, серые для неподключённых источников, язык RO/RU, время отправки. Хранение в settings.

## Сессия 7 · `/rigorous harden` + деплой

Валидация ответов API, идемпотентность отправки (не слать дважды при рестарте), алерты в служебный бот, Docker Compose, бэкап. Неделя прогона в тестовой группе с DRY_RUN, сверка с ручными отчётами продавцов глазами. Затем переключение chat_id на продовую группу — с подтверждением.

## Дальше (этап 2+)

- Разведка источника B через Chrome (Serghei логинится, Claude собирает Network-запросы) → `docs/mefi-session-notes.md` → shape `mefi-sync`.
- Режим вопросов: shape по docs/brief.md §7 — инструменты как обёртки над `metrics/`, enum-параметры, футер с источником, логирование вопросов.
- Источники D/E/F/G — по одному модулю за сессию.

## Правила для каждой сессии

- Начинать с `/rigorous shape`, не с кода. Ответ shape — одна страница; если больше — задача слишком большая, дели.
- После craft — `/rigorous critique`, потом коммит. Один коммит — одна фича.
- Если Claude предлагает добавить агентный фреймворк, text-to-SQL, Redis, ORM-магию — отказ, ссылка на CLAUDE.md «Чего не делать».
- `/clear` между несвязанными задачами; контекст держать коротким.
