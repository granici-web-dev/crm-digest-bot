# KPI — определения, формулы, пороги

Источник: `SB KPi.xlsx` (Sales Performance Index v2.0), формулы сняты скриптом из ячеек `01_Input_Leads` (колонки V..AG) и `03_KPI_Agenti` (C..AE). v1 повторяет workbook один в один (ADR-002), отступления помечены **[наше]**. Пороги — `config/kpi.yaml`, ключи совпадают с `02_Setari_Targete`. Изменение формулы или порога — только через ADR и обновление эталона.

Считаются по консультанту, по шоуруму и по компании за период.

## Признаки лида (как `01_Input_Leads` V..AG)

| Признак | Excel | У нас |
|---|---|---|
| Нормализация статуса | `UPPER(Status)`, `Ț→T`, `Ș/Ş→S` | не нужна: категория по `status.name` из `config/status-mapping.yaml` (инвариант 4) |
| `is_clienti` | `Status_Normalizat` содержит `CLIENTI` | категория `WON` по `status.name = Clienți`. `converted_at` в счёт не идёт (в workbook контракты только по статусу), расхождение с ним — алерт |
| `is_irelevant` | содержит `IRELEVANT` | категория `LOST · IRELEVANT` (IRELEVANT, SPAM). **[наше]** SPAM в Excel не считался бы; в эталоне SPAM нет, на цифры не влияет |
| `is_nu_a_raspuns` | содержит `NU A RASPUNS` | `LOST · NU RASPUNS` |
| `is_buget` | содержит `BUGET` | `LOST · BUGET` |
| `is_produs_nepotrivit` | содержит `PRODUS` | `LOST · PRODUS NEPOTRIVIT` |
| `is_ofertat` | `Ofertat` содержит `DA` | кастомное поле `Ofertat` (field_id 20) = `✅DA`; до подключения источника B |
| `is_showroom_visit` | `Sursa` содержит `SHOWROOM` | `source.name = Showroom` |
| `is_lead` | дата `Data ultimei solicitări` не пустая | лид в снапшоте с `created_at` в периоде |
| агент | `TRIM(Desemnat)`: схлопывает двойной пробел (`Raileanu  Leon` → `Raileanu Leon`) | `assigned_to.id` → `config/managers.yaml`, имя не сравнивается |

Лиды без консультанта входят в итог по компании, но ни в одну строку по консультантам (в эталоне таких 5 из 314).

## Базовые множества (за период, по `created_at` лида)

| Обозначение | Определение | Колонка `03_KPI_Agenti` |
|---|---|---|
| `LEADS` | все лиды с датой, **включая** DESIGNER и INFLUENCER. **[наше]** Минус лиды тестовых аккаунтов (`config/managers.yaml`, `test_account: true`) | C `Lead-uri` |
| `IRR_LEADS` | `LEADS` с `is_irelevant` | D `Irelevante` |
| `USEFUL` | `LEADS − IRR_LEADS` | E `Lead-uri utile` |
| `CLIENTI` | `LEADS` с `is_clienti` | F `CLIENTI` |
| `OFFERS` | `LEADS` с `is_ofertat` | G `Oferte` |
| `NAR` | `LEADS` с `is_nu_a_raspuns` | H `Nu a răspuns` |
| `BUGET` | `LEADS` с `is_buget` | I `Buget` |
| `PNP` | `LEADS` с `is_produs_nepotrivit` | J `Produs nepotrivit` |
| `SHOWROOM_VISITS` | `LEADS` с `is_showroom_visit` | K `Vizite showroom` |
| `ACTIVE_OFFERS_14` | `OFFERS`, не `CLIENTI`, не `IRR_LEADS`, `analysis_date − date(last_contact_at) > 14` дней; `last_contact_at = null` → не входит | L `Oferte active >14 zile` |

`analysis_date` — дата расчёта отчёта; в эталоне `02_Setari_Targete!B3` = 2026-06-27. Порог дней — `active_offer_stale_days`.

**Дефект workbook.** Формула `01_Input_Leads!AC` ссылается на `'02_Setari_Targete'!$B$2`, а ячейка пустая (дата лежит в B3). Разность отрицательная, поэтому в Excel `ACTIVE_OFFERS_14 = 0` у всех консультантов и `Score ACR = 5`. Кроме того, `DATEVALUE("dd-mm-yyyy")` зависит от локали Excel. Мы считаем по B3, как задумано. С B3 по эталону: Raileanu 9, Dragoi 16, Roibu 16, Moaca 6, Godja 15, Doja 3. `Score ACR` падает до 3 у Roibu, Moaca, Godja; их SPI на 2 ниже Excel (50, 42, 55), уровень и рекомендация не меняются. `expected_by_agent` в эталоне хранит значения Excel как есть; как их сверять, решает shape `metrics/`.

## KPI

| KPI | Название | Формула | Ячейка | Цель |
|---|---|---|---|---|
| SCR | Sales Conversion Rate | `CLIENTI / USEFUL` | M | > 10 % |
| L2O | Lead to Offer | `OFFERS / USEFUL` | N | > 50 %, информативно |
| O2C | Offer to Contract | `CLIENTI / OFFERS` | O | > 20 %, информативно |
| CDR | Contact Discipline Rate | `(LEADS − NAR) / LEADS` | P | > 90 % |
| PLR | Price Lost Rate | `BUGET / (LEADS − IRR_LEADS − NAR)` | Q | < 25 % |
| SC | Showroom Conversion | `CLIENTI / SHOWROOM_VISITS`, все клиенты консультанта, **без пересечения** с визитами | R | > 20 % |
| PFR | Product Fit Rate | `PNP / USEFUL` | S | < 10 % |
| ACR | Active Control Rate | `ACTIVE_OFFERS_14 / LEADS` | T | < 20 % |
| IRR | Irrelevant Rate | `IRR_LEADS / LEADS` | U | ≤ 20 %, штраф к SPI |

Доли не округляются до вывода.

**Деление на ноль.** Excel: `IFERROR(…, 0)`. У нас метрика `null`, в отчёте и в ответе чата «—». Балл SPI и рекомендация считаются от 0, как в Excel. Следствие: при нулевом знаменателе PLR, PFR, ACR, IRR получают лучший балл, SCR, CDR, SC — худший. Консультант без лидов получает SPI 54.

## Баллы SPI (`03_KPI_Agenti` V..AB)

Проверка сверху вниз, первая выполненная ступень. Пороги по ключам `config/kpi.yaml`.

| KPI | Ступень 1 | Ступень 2 | Ступень 3 | Иначе | Макс. |
|---|---|---|---|---|---|
| SCR ↑ | ≥ `scr_elite` (10 %) → 30 | ≥ `scr_bine` (7 %) → 24 | ≥ `scr_minim` (5 %) → 18 | 10 | 30 |
| CDR ↑ | ≥ `cdr_perfect` (95 %) → 20 | ≥ `cdr_minim` (90 %) → 16 | ≥ `cdr_contact_floor` (80 %) → 10 | 5 | 20 |
| PLR ↓ | ≤ `plr_perfect` (20 %) → 20 | ≤ `plr_maxim` (25 %) → 16 | ≤ `plr_problema` (35 %) → 10 | 5 | 20 |
| SC ↑ | ≥ `sc_perfect` (20 %) → 15 | ≥ `sc_bine` (15 %) → 12 | ≥ `sc_minim` (10 %) → 8 | 4 | 15 |
| PFR ↓ | ≤ `pfr_perfect` (10 %) → 10 | ≤ `pfr_bine` (15 %) → 8 | ≤ `pfr_maxim` (20 %) → 5 | 2 | 10 |
| ACR ↓ | ≤ `acr_perfect` (20 %) → 5 | ≤ `acr_maxim` (30 %) → 3 | — | 1 | 5 |

`cdr_contact_floor` (80 %) в `02_Setari_Targete` нет, он зашит в формулу W. **[наше]** Вынесен в `config/kpi.yaml` с пометкой, значение то же.

Штраф IRR (AB): ≤ `irr_acceptabil` (20 %) → 0; ≤ `irr_atentie` (30 %) → −5; иначе −10.

`SPI = max(0, Score_SCR + Score_CDR + Score_PLR + Score_SC + Score_PFR + Score_ACR + Penalizare_IRR)`, максимум 100. Баллы — сразу очки, без весов и перенормировки.

## Уровни (AD, `config/kpi.yaml` → `levels`)

| SPI | Уровень |
|---|---|
| ≥ 90 | Elite |
| ≥ 80 | Gold |
| ≥ 70 | Silver |
| ≥ 60 | Bronze |
| < 60 | Coaching |

## Главная рекомендация (AE)

Первое выполненное условие сверху вниз. Текст RO из workbook, RU — в `templates/recommendations.{ro,ru}.yaml`.

| Приоритет | Условие | Текст RO |
|---|---|---|
| 1 | IRR > `irr_atentie` (30 %) | Verifică utilizarea statusului IRELEVANT |
| 2 | CDR < `cdr_minim` (90 %) | Coaching disciplină contact |
| 3 | PLR > `plr_maxim` (25 %) | Coaching preț / valoare |
| 4 | SCR < `scr_minim` (5 %) | Coaching închidere |
| 5 | ACR > `acr_perfect` (20 %) | Prioritate follow-up oferte active |
| 6 | иначе | Performanță stabilă |

## Дополнительные метрики (не из SPI)

| Метрика | Формула |
|---|---|
| Speed-to-lead | Формула брифа недействительна: заметки через API недоступны, а `last_contact_at` и `status_changed_at` по одному снапшоту первое касание не дают (`docs/mefi-api-notes.md`, 24.09.2026). Переопределяется в ADR-003 перед включением w5. В MVP w5 выключен. |
| Просроченные revenire | лиды с кастомным `Data revenire ≤ today` и без изменения статуса после этой даты |
| Когортная конверсия | `CLIENTI из лидов месяца M на дату D / USEFUL месяца M` — считается по снапшоту на D |
| Дельта к периоду | `(X_now − X_prev) / X_prev`; при `X_prev = 0` → «n/a» |

## Эталон

`tests/fixtures/etalon-2026-05.json`, собирается `scripts/build_etalon.py` из `docs/reference/SB KPi.xlsx`. Лиды 01.05–16.06.2026 (314 строк с непустым Status, без контактов клиента), пороги `02_Setari_Targete`, ожидаемые значения по 6 консультантам из `03_KPI_Agenti` (C..AE, доли без округления). Тест `test_kpi_matches_etalon` должен проходить до любого рефакторинга `metrics/`.
