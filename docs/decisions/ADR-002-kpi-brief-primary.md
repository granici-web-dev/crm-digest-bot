# ADR-002 · KPI по брифу, SB KPi.xlsx только тестовые данные

Статус: **accepted** (25.09.2026), заменяет редакцию от 24.09.2026 («KPI v1 повторяет SB KPi.xlsx v2.0»). Формулы — `docs/kpi-definitions.md`, эталон — `tests/fixtures/etalon-2026-05.json`.

## Контекст

`SB KPi.xlsx` (Sales Performance Index v2.0) выглядел как принятый у владельца инструмент оценки консультантов, поэтому первая редакция ADR-002 повторяла его формулы один в один. Владелец сообщил, что workbook составлен через ИИ, а не специалистом по продажам. Его формулы и пороги не являются договорённостью с бизнесом, и повторять их, включая ошибки, незачем.

## Решение

Формулы KPI — по `docs/brief.md` §3 и `docs/kpi-definitions.md`. Из Excel берутся только 314 лидов 01.05–16.06.2026 для регрессионного фикстура. Формулы, баллы и итоговые значения workbook решений не определяют.

## Правила

- `LEADS` — лиды с `created_at` в периоде минус категория PARTNERSHIP (DESIGNER, INFLUENCER) и минус лиды тестовых аккаунтов (`test_account: true` в `config/managers.yaml`).
- `USEFUL = LEADS − IRR_LEADS`; вместе с исключением PARTNERSHIP это «все − IRELEVANT − PARTNERSHIP» из брифа.
- `SC = |CLIENTI ∩ SHOWROOM_VISITS| / |SHOWROOM_VISITS|`: клиенты среди визитов в шоурум, а не все клиенты консультанта.
- Деление на ноль → метрика `null`, в отчёте и в ответе чата «—».
- `CLIENTI` — только статус `Clienți`. `converted_at` в счёт не идёт; расхождение с ним остаётся алертом снапшота.
- SPAM → IRELEVANT (`config/status-mapping.yaml`).
- `ACTIVE_OFFERS_14`: Ofertat = `✅DA`, не CLIENTI, не IRELEVANT, `last_contact_at` старше 14 дней от даты расчёта; `last_contact_at = null` → не входит. Не `status_changed_at`: в mefi он `null`, если статус задан при создании (`docs/mefi-api-notes.md`), и такие оферты выпали бы из счёта.
- Консультант — по `assigned_to.id`, категория — по `status.name` через конфиг.

## Предварительное, не показывать

Ступени баллов, штраф IRR, SPI, уровни, главная рекомендация. Пороги хранятся в `config/kpi.yaml` со статусом `provisional`, функции живут в `metrics/`, но в отчёты MVP и в ответы чата не выводятся. Калибровка — после 2–3 месяцев реальных снапшотов плюс Excel 2024–2025 (источник F), с владельцем, отдельным ADR.

## Известная ошибка Excel

Формула `01_Input_Leads!AC` ссылается на пустую `02_Setari_Targete!$B$2` вместо даты в B3. Разность дат отрицательная, поэтому в workbook `ACTIVE_OFFERS_14 = 0` и ACR = 0 у всех консультантов. В эталоне не воспроизводится.

## Эталон

`tests/fixtures/etalon-2026-05.json` — регрессионный фикстур, а не сверка с Excel.

- `expected_by_agent` считает `scripts/build_etalon.py` по формулам брифа, независимо от `metrics/`: простые циклы, без pandas, свои константы статусов вместо разбора конфига.
- `excel_reference` хранит исходные значения `03_KPI_Agenti` для сравнения; тесты по нему не проверяют.
- `meta.manual_check` — консультант, пересчитанный вручную по счётчикам (Moaca Andreea); скрипт падает, если расчёт с этой записью разошёлся.

## Последствия

- `config/status-mapping.yaml`: `PARTNERSHIP.excluded_from_leads: true` соответствует решению.
- `m5 manager_cockpit` — таблица 9 KPI по консультантам с целями, без SPI; `m6 spi_ranking` выключен до калибровки.
- Изменение формулы = ADR + пересборка эталона.
