# Shape: счётчик смен источника (2026-10-02)

## Goal

Раз в неделю, после недельного отчёта, одна строка в служебный бот для проверки гипотезы Б «источник лида переписывают при визите в шоурум»:
`Смены источника за неделю 22.09–28.09: N (из них → Showroom: M); пар снапшотов: K из 7. Топ: Facebook → Showroom 3, Fără sursă → Web 1`.
Только агрегаты и названия источников mefi как есть, без id. Код ничего не решает: строку «гипотеза Б отклонена» после 4 недель подряд с N = 0 вписывают в PLAN руками.

## Approach

Чистая функция `source_changes(snapshots, days, config)` в `metrics/source_changes.py`. Вход тот же, что у `manager_touches` (w14): кортеж `PreviousSnapshot` по цепочке `touch_snapshot_dates` (последний успешный до недели + успешные внутри), пары через `pairwise`. Смена = `lead_id` есть в обоих снапшотах пары и `source_name` различается; null с обеих сторон не смена, null ↔ значение смена (подпись `without_source_label`). Пара засчитывается в неделю, если день `current` входит в `days`; K = число таких пар. «→ Showroom» = новое значение ∈ `sources.showroom_visit` из `status-mapping.yaml` (не литерал в коде). Топ-3 «откуда → куда» по убыванию, ничья по имени.

Шаг в `app.report_job`: для `level == "weekly"` после `run_report`, в своём `try/except` с `alert_failure`, как проверка бэкапа у daily. Неделя и дата снапшота берутся тем же `report_period`/`report_snapshot_date`, что у отчёта; цепочка грузится `success_snapshot_dates` + `touch_snapshot_dates` + `load_lead_frame`. Текст строки собирается в `app.py` (служебный бот по-русски, шаблон RO не нужен), отправка `notify_ops`.

## Files

- `src/digest/metrics/source_changes.py` — новый: `SourceChange(from_source, to_source, count)`, `SourceChanges(change_count, to_showroom_count, top, pair_count)`, `source_changes(...)`. Без try/except и логов (PRINCIPLES).
- `src/digest/reports/runner.py` — `refactor:` отдельным коммитом до фичи: из `touch_snapshot_chain` выделить загрузку цепочки по датам, чтобы шаг не копировал цикл загрузки; w14 зелёный до и после.
- `src/digest/app.py` — `source_changes_line(...)` (текст) и `weekly_source_changes_check(deps, now)`; вызов в `report_job` для weekly.
- `tests/unit/test_metrics_source_changes.py` — новый.
- `tests/integration/test_app.py` (или текущий файл тестов `report_job`) — шаг в ops и изоляция сбоя.
- `docs/kpi-definitions.md` — короткий раздел «Смены источника (служебная проверка)», не KPI.
- `docs/PLAN.md` — кандидат переходит в работу; итог сессии.

## Schema / API changes

Нет. Не модуль реестра, не попадает в `/settings` и `IMPLEMENTED_MODULES`, новых ключей конфига нет.

## Test plan

Unit, seam `source_changes(snapshots, days, config)`, синтетические кадры через `make_snapshot`/`make_lead`:
- `test_changed_source_between_adjacent_snapshots_is_counted` (плюс → Showroom в `to_showroom_count`).
- `test_lead_missing_in_one_snapshot_is_not_a_change` (лид пропал или новый в паре).
- `test_missing_day_gives_one_pair_over_two_days_and_k_six` (пропущенный день: смена всё равно видна, `pair_count = 6`).
- `test_null_to_source_is_change_and_null_to_null_is_not`.
- `test_top_is_three_transitions_by_count_then_name`.
- `test_pair_before_week_is_not_counted` (пара, у которой `current` вне `days`).
- `test_no_pairs_gives_zero_changes_and_zero_pairs`.

Интеграция, seam `report_job(deps, "weekly", None)` с фейковой сессией Telegram и Postgres:
- `test_weekly_job_sends_source_changes_line_to_ops_only` (строка в ops, в группе её нет, без id лидов).
- `test_source_changes_failure_does_not_affect_weekly_report` (сбой загрузки → отчёт отправлен, алерт «Проверка смен источника упала»).
- `test_daily_job_does_not_send_source_changes_line`.

## Tradeoffs / alternatives considered

- **Модуль w-уровня в реестре.** Отвергнуто: это служебная проверка гипотезы, руководству не нужна; модуль требует шаблона RO, места в `/settings` и `ReportContext`, а через месяц его снимать.
- **Внутри раннера после отправки (`run_report`).** Отвергнуто: раннер отвечает за отчёт и `report_runs`; шаг в `report_job` рядом с проверкой бэкапа даёт изоляцию сбоя тем же приёмом и не трогает идемпотентность.
- **Сравнение начала и конца недели (два снапшота).** Отвергнуто: A → B → A за неделю не видно, и пропавший в середине лид неотличим. Цепочка соседних пар как у w14 ловит каждую смену и даёт честный K.
- **Не учтено осознанно:** лид, отсутствующий в среднем снапшоте (есть i−2 и i, нет i−1), смену не даёт; при битых лидах это единицы, и они уже видны алертом `missing_from_previous`.

## Open questions

1. Догон на старте (`catch_up_level`) недельного отчёта: строку не шлём (предлагаю так; пропуск недели виден по отсутствию строки, ряд «4 недели подряд» тогда начинается заново). Или слать и при догоне?
2. Слать строку, если `run_report` вернул не «отправлено» (failed, повтор уже отправленного)? Предлагаю: да, всегда при срабатывании weekly-джобы: проверка от отчёта не зависит.

## Согласовано (02.10.2026)

1. Строка уходит каждый раз, когда запускается недельная джоба: по расписанию (`report_job`) или догоном при старте (`catch_up_level`). Дубля нет: догон запускает джобу, только если недельный отчёт ещё не ушёл. В коде: при догоне строка не шлётся, если `run_report` вернул `already_sent` (отчёт уже ушёл) или `in_progress` (строку пошлёт идущий прогон).
2. Строка уходит независимо от исхода отчёта по расписанию: успех, сбой, повтор уже отправленного.
3. Остальное по shape.
