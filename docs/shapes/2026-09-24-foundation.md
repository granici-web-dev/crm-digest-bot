# Shape: фундамент (схема, клиент mefi, снапшот, конфиги)

Статус: подтверждён 24.09.2026 с семью правками (раздел в конце). Источники истины: CLAUDE.md, docs/brief.md, docs/mefi-api-notes.md, config/*.yaml.

## Цель

Функция `run_daily_snapshot` за один вызов выгружает все лиды mefi, категоризирует их по конфигу и атомарно пишет снапшот за дату по Бухаресту. Всё, что потом должно уйти алертом, остаётся в строке `snapshot_runs`, и ни один лид не теряется без следа.

## Подход

- Пакет `src/digest/`, uv, mypy strict, ruff, pre-commit с проверкой TODO.
- Схема: SQLAlchemy Core `MetaData`, первая миграция Alembic пишется руками.
- Клиент: httpx `AsyncClient` и один `RequestPacer` на уровне модуля: `asyncio.Lock` плюс monotonic, минимум 1.2 с между любыми запросами процесса.
- Порядок выгрузки: сортировка `created_at asc`. Новые лиды во время выгрузки встают в конец и не сдвигают страницы. Дубли убираются по id.
- Две транзакции. Отдельной транзакцией создаётся `snapshot_runs(status=running)`. Затем одной транзакцией пишутся все строки `lead_snapshots` и ставится `status=success`. При исключении эта транзакция откатывается, а отдельная помечает запуск `failed` с текстом ошибки. Частичного снапшота не бывает.
- Повторный вызов за дату, где уже есть `success`, выходит без обращения к API.

## Файлы

- `pyproject.toml`, `.pre-commit-config.yaml`, `alembic.ini`, `alembic/env.py` (async)
- `alembic/versions/0001_foundation.py`: все таблицы ниже
- `src/digest/settings.py`: pydantic-settings (`DATABASE_URL`, `MEFI_BASE_URL`, `MEFI_API_KEY`, `TENANT_ID`); `.env.example` получает `TENANT_ID=sofabelle`
- `src/digest/db/schema.py`: `MetaData` и `Table`
- `src/digest/config.py`: pydantic-модели и загрузчики трёх YAML, падают на старте
- `src/digest/mefi/models.py`: `MefiLead` (`extra="allow"`); обязательны только `id: int` и `created_at: datetime`, остальное nullable
- `src/digest/mefi/client.py`: `RequestPacer`, `MefiClient.search_all_leads()`; только `POST /leads/search`, lifecycle все три, per_page 100
- `src/digest/snapshot.py`: `categorize(status_name, mapping)`, `lead_to_snapshot_row(...)`, `run_daily_snapshot(engine, client, config, tenant_id, now)`
- `config/status-mapping.yaml`: все `custom_fields` приводятся к виду `{field_id, name}`; новая секция `raw_strip`
- `config/modules.yaml`: блок `sources: {A: {connected: true}, B..H: {connected: false}}` вместо комментария
- `config/managers.yaml`: новый; id 2–14 из enums.md с поправками по notes (11 = Marc Andra, 13 = «Raileanu  Leon» с двумя пробелами), `showroom: null` с пометкой «уточнить у клиента»
- `tests/conftest.py`, `tests/factories.py` (`make_lead`), `tests/unit/…`, `tests/integration/…`

## Схема

Все таблицы получают `tenant_id text NOT NULL`, FK на `tenants(id text PK, name)`; строка `sofabelle` пишется в миграции.

**lead_snapshots**
- Колонки: `snapshot_date`, `lead_id`, `category`, `loss_reason`, `status_name`, `source_name`, `showroom`, `ofertat bool NULL`, `data_revenire date`, `is_duplicate bool NOT NULL`, `assigned_to_id`, `assigned_to_name`, `created_at`, `status_changed_at`, `last_contact_at`, `converted_at` (все timestamptz), `raw jsonb NOT NULL`.
- `category` ∈ WON / ACTIVE / ACTIVE_FOLLOWUP / LOST / PARTNERSHIP / UNMAPPED. `counts_as` применяют метрики, не снапшот.
- PK `(tenant_id, snapshot_date, lead_id)`.

**snapshot_runs**
- Служебные: `id`, `snapshot_date`, `attempt`, `status` (running | success | failed), `started_at`, `finished_at`, `duration_ms`.
- Счётчики: `api_total` (meta.total), `leads_written`, `unmapped_count`, `skipped_count`, `missing_since_previous`, `rate_limited_count`.
- Подробности: `skipped_leads jsonb [{id, reason}]`, `previous_snapshot_date`, `new_unmapped_lead_ids int[]`, `won_converted_mismatch_ids int[]`, `custom_field_mismatches jsonb`, `error text`.
- Частичный уникальный индекс `(tenant_id, snapshot_date) WHERE status='success'`.

**Остальные**
- `settings(tenant_id, key, value jsonb, updated_at, updated_by)`, PK `(tenant_id, key)`.
- `module_settings(tenant_id, module_id, enabled, params jsonb, updated_at, updated_by)`.
- `schedules(tenant_id, report_level, cron, enabled)`, уникальность `(tenant_id, report_level)`, таймзона всегда Europe/Bucharest.
- `report_runs(id, tenant_id, report_level, period_start, period_end, snapshot_date, chat_id, status, started_at, finished_at, error, message_ids jsonb)`, уникальность `(tenant_id, report_level, period_start, chat_id)`. Без `skipped_leads`.

## Правила снапшота

- UNMAPPED: `status = null` или `status.name` после trim не в конфиге. «Новый» UNMAPPED значит, что `lead_id` не был UNMAPPED в предыдущем успешном снапшоте.
- Битая форма: нет `id` или `created_at`, либо не тот тип → лид пропускается и попадает в `skipped_leads` с причиной.
- Кастомные поля ищутся по `field_id`. Если `name` не совпадает с конфигом, значение = null, запись в `custom_field_mismatches`.
- Ofertat: `✅DA` → true, `❌NU` → false, null → null. Иное значение → null и запись в `custom_field_mismatches`.
- Исчезнувшие лиды: `missing_since_previous` считается против последнего успешного снапшота, не строго вчерашнего.
- Несовпадение WON: `Clienți` при `converted_at = null` и обратный случай → `won_converted_mismatch_ids`.
- 429: ждём `Retry-After` (без заголовка 10 с) и повторяем. После 5 подряд бросаем исключение, запуск `failed`, дальше повтор в 19:10 (планировщик в сессии 5). На 5xx и сетевые ошибки внутренних ретраев нет.
- raw пишется без контактов клиента: поля из секции `raw_strip` удаляются до записи.

## Тест-план

Реальный Postgres через testcontainers, respx на `tests/fixtures/mefi/search_3_leads.json`.

Unit:
- `test_null_status_is_unmapped`
- `test_unknown_status_is_counted_as_unmapped`
- `test_status_name_matched_after_trim_without_case_folding`
- `test_lead_without_created_at_is_skipped_and_counted`
- `test_showroom_name_mismatch_nulls_value_and_records_mismatch`
- `test_unknown_ofertat_value_is_null`
- `test_raw_strip_removes_contact_fields_keeps_textareas`
- `test_status_in_two_categories_fails_config_load`
- `test_enabled_module_with_disconnected_source_fails_config_load`
- `test_managers_yaml_rejects_duplicate_ids`

Интеграция, клиент:
- `test_search_body_requests_all_lifecycles_per_page_100`
- `test_requests_are_spaced_at_least_1_2s`: фейковые `clock` и `sleep`, проверяется аргумент `sleep`, реального ожидания нет
- `test_429_waits_retry_after_and_retries`
- `test_pagination_dedupes_by_id`

Интеграция, БД:
- `test_migrations_upgrade_from_zero`
- `test_snapshot_writes_rows_and_success_run`
- `test_failed_snapshot_leaves_no_rows_and_failed_run`
- `test_second_call_same_date_after_success_is_noop`
- `test_new_unmapped_ids_only_on_first_appearance`
- `test_missing_since_previous_counts_disappeared_leads`
- `test_snapshot_date_is_bucharest_date`: 23:30 UTC это уже завтра по Бухаресту

## Отвергнутые варианты

- Upsert или DELETE+INSERT при повторе: двухтранзакционная схема делает частичный снапшот невозможным.
- Отдельная таблица алертов (outbox): для v1 хватает полей в `snapshot_runs`.
- `sort=created_at desc`: новый лид во время выгрузки сдвигает страницы, один лид теряется.
- Падать на несовпадении `name` кастомного поля: заблокировало бы весь снапшот из-за переименования подписи.

## Семь подтверждённых правок (24.09.2026)

1. `lead_snapshots.is_duplicate bool NOT NULL` из поля mefi. Решение об исключении дублей принимают метрики.
2. `report_runs` без `skipped_leads`; счётчик только в `snapshot_runs`. PRINCIPLES.md поправить отдельным коммитом `docs:` до craft.
3. `RequestPacer` принимает `sleep` и `clock` параметрами с дефолтами `asyncio.sleep` и `time.monotonic`.
4. raw без контактов: `name`, `phone`, `email`, `identity`, `business`, `company`, `location.address_line`, `location.coordinates`. Список в `config/status-mapping.yaml`, секция `raw_strip`. Textarea-поля и `location.city` остаются.
5. `managers.yaml`: Marketing Sofa (id 7) `active: false` с комментарием, поля `role` нет.
6. `settings` и `module_settings` как две таблицы.
7. Craft двумя коммитами: первый схема, миграция, конфиги и их тесты; второй клиент mefi и снапшот.
