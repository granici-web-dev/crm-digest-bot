# Ресёрч рынка — выжимка (24.09.2026)

Полный отчёт — в проекте Sofabelle (claude.ai). Здесь только то, что влияет на решения в коде.

## Вывод

Готового продукта под mefi.ro нет. Пишем своё на зрелых фреймворках, open-source берём частями. Оценка для одного junior-fullstack с Claude Code: MVP 30–40 чел.-дней, полная спецификация 45–60.

## Почему не готовое

| Вариант | Почему нет |
|---|---|
| Metabase | Нет Telegram-канала (issue #14146 открыт с 2020), webhook только для alerts, не для dashboard subscriptions. Возможен позже как веб-витрина поверх нашего Postgres |
| Grafana | Telegram только для алертов; PDF-отчёты — Enterprise и только email; инструмент мониторинга, не воронки |
| Databox | Ближайший по идее (AI Genie, semantic layer), но Telegram нет, custom-источники платно; ориентир для позиционирования Davoq |
| n8n / Make | Единственный no-code, закрывающий «расписание + Telegram + AI-агент + Postgres». Годится для демо; 10 KPI с SPI и когортами в Code-нодах — техдолг; /settings неудобен; лицензия n8n (Sustainable Use) критична для перепродажи в Davoq |
| Bitrix24 / amoCRM боты | Только своя CRM |
| Vanna | Архивирован 29.03.2026 |
| PandasAI | LLM исполняет сгенерированный код — противоречит инварианту «LLM не считает» |
| WrenAI, Dataherald, sqlchat, Chat2DB, DB-GPT | Text-to-SQL для ad-hoc аналитики; для 10 фиксированных KPI избыточно и менее надёжно |
| amocrm-sales-bot, tg-group-analytics-bot | Скрипты на 200–500 строк; можно подсмотреть структуру, не форкать |

## Что берём

- **Подход:** tool calling над реестром детерминированных функций (мини-semantic layer в коде). Text-to-SQL — не раньше v2, read-only, whitelist витрин, пометка «нестандартный запрос».
- **Паттерн function calling:** gptsql (`github.com/tatari-tv/gptsql`) — но вместо универсального `run_sql_command` набор узких функций с enum-параметрами.
- **Меню /settings:** aiogram_dialog (Python) или @grammyjs/menu (TS).
- **Референс архитектуры TS-варианта:** `github.com/arhebs/tg-group-analytics-bot` (Telegraf + Postgres + миграции с advisory lock).

## Рекомендованные стеки

**Python (рекомендация ресёрча):** aiogram 3 (3.26, 08.2026) + aiogram_dialog · APScheduler 3.x, расписания в таблице `schedules` · SQLAlchemy 2 + asyncpg + Alembic · pandas · matplotlib (Agg → BytesIO → send_photo) · xlsxwriter · WeasyPrint · anthropic SDK (сырой tool-use цикл, не Agent SDK) · Playwright for Python · httpx · Docker Compose.

**TypeScript:** grammY (1.46, 08.2026) + @grammyjs/menu · pg-boss (cron на Postgres, без Redis) · Drizzle/Kysely + pg · Vega-Lite → PNG · exceljs · Playwright (PDF + mefi) · @anthropic-ai/sdk. Telegraf — не брать (v4 без поддержки с 02.2025, v5 не вышел).

Выбор — в `/rigorous teach`; аргумент за TS — только единый стек с Davoq.

## mefi.ro

Публичной документации API нет; `leads:read` — непубличный доступ. Встроенные KPI и дашборды есть, но только внутри mefi; запланированной доставки отчётов нет; Telegram-интеграции нет; WhatsApp Business API — для переписки с клиентами, не для отчётов. Сторонних интеграций/ботов для mefi не найдено. Обновление 2025: drag&drop отчёты, Kanban, KPI — значит UI меняется, скрейпер (источник B) будет ломаться.

## Румыния / Davoq

Упакованного продукта «AI-аналитик продаж в Telegram/WhatsApp поверх румынской CRM» не найдено. Агентства (AgentVocal, RoboMarketing, AI Factory, AI-Automated, NeoDigital, CODE24) продают голосовых ботов и заказную n8n-автоматизацию. Дифференциатор — детерминированные цифры, румынский язык, знание локальных CRM. Для тиражирования закладывать с первого дня: `tenant_id`, адаптеры источников (`MefiAdapter`, …), интерфейс канала (Telegram → WhatsApp).

## Оговорки

Звёзды GitHub для части проектов — из вторичных источников. Лицензия n8n не перепроверялась. Оценки трудозатрат — экспертные; основная неопределённость — Playwright-часть и качество данных в mefi.
