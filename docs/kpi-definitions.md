# KPI — определения, формулы, пороги

Источник: `SB KPi.xlsx` (Sales Performance Index v2.0), листы `15_KPI_Definitii`, `02_Setari_Targete`, `03_KPI_Agenti`. Считаются по консультанту, по шоуруму и по компании за период. Изменение формулы или порога — только через ADR и обновление эталонного фикстура.

## Базовые множества (за период, по `created_at` лида)

| Обозначение | Определение |
|---|---|
| `LEADS` | все лиды, кроме `PARTNERSHIP` |
| `IRR_LEADS` | лиды категории `LOST · IRELEVANT` (статусы IRELEVANT, SPAM) |
| `USEFUL` | `LEADS − IRR_LEADS` |
| `CLIENTI` | лиды со `status.name = Clienți` или `converted_at != null` |
| `OFFERS` | лиды с кастомным полем `Ofertat = DA` (до подключения источника B); после — лиды с ≥1 офертой из Oferte |
| `NAR` | лиды `LOST · NU RASPUNS` |
| `BUGET` | лиды `LOST · BUGET` |
| `PNP` | лиды `LOST · PRODUS NEPOTRIVIT` |
| `SHOWROOM_VISITS` | лиды с `source.name = Showroom` |
| `ACTIVE_OFFERS_14` | `OFFERS` с категорией `ACTIVE` и `status_changed_at` старше 14 дней на дату расчёта |

## KPI

| KPI | Название | Формула | Цель | Вес в SPI |
|---|---|---|---|---|
| SCR | Sales Conversion Rate | `CLIENTI / USEFUL` | > 10 % | 30 % |
| CDR | Contact Discipline Rate | `(LEADS − NAR) / LEADS` | > 90 % | 20 % |
| PLR | Price Lost Rate | `BUGET / (USEFUL − NAR)` | < 25 % | 20 % |
| SC | Showroom Conversion | `CLIENTI ∩ SHOWROOM_VISITS / SHOWROOM_VISITS` | > 20 % | 15 % |
| PFR | Product Fit Rate | `PNP / USEFUL` | < 10 % | 10 % |
| ACR | Active Control Rate | `ACTIVE_OFFERS_14 / LEADS` | < 20 % | 5 % |
| L2O | Lead to Offer | `OFFERS / USEFUL` | > 50 % | информативно |
| O2C | Offer to Contract | `CLIENTI / OFFERS` | > 20 % | информативно |
| IRR | Irrelevant Rate | `IRR_LEADS / LEADS` | ≤ 20 % | штраф к SPI |

Деление на ноль → метрика `null`, в отчёте «—», в SPI компонент не участвует и веса перенормируются.

## Пороги (из `02_Setari_Targete`)

| KPI | Elite / Perfect | Bine | Minim / Maxim | Problemă |
|---|---|---|---|---|
| SCR | ≥ 10 % | ≥ 7 % | ≥ 5 % | < 5 % |
| CDR | ≥ 95 % | — | ≥ 90 % | < 90 % |
| PLR (меньше — лучше) | ≤ 20 % | — | ≤ 25 % | ≥ 35 % |
| SC | ≥ 20 % | ≥ 15 % | ≥ 10 % | < 10 % |
| PFR (меньше — лучше) | ≤ 10 % | ≤ 15 % | ≤ 20 % | > 20 % |
| ACR (меньше — лучше) | ≤ 20 % | — | ≤ 30 % | > 30 % |
| IRR | ≤ 20 % acceptabil | — | ≤ 30 % atenție | > 30 % |

## SPI (сводный балл)

`Score_k` для каждого взвешенного KPI: 100 при достижении Elite/Perfect, линейно до 0 при Problemă (для «меньше — лучше» — зеркально). `SPI = Σ(w_k × Score_k) / Σ w_k − Penaliz`, где `Penaliz` — штраф за IRR выше 30 % (точная шкала — в `03_KPI_Agenti`, колонки Score_* и Penaliz; перенести в код 1:1 и зафиксировать эталоном).

Уровни: Elite ≥ 80 · Bine 65–79 · Acceptabil 50–64 · Problemă < 50. Рекомендация — по худшему взвешенному KPI (текст в `templates/recommendations.{ro,ru}.yaml`).

## Дополнительные метрики (не из SPI)

| Метрика | Формула |
|---|---|
| Speed-to-lead | Формула брифа недействительна: заметки через API недоступны, а `last_contact_at` и `status_changed_at` по одному снапшоту первое касание не дают (`docs/mefi-api-notes.md`, 24.09.2026). Переопределяется в ADR-002 перед включением w5. В MVP w5 выключен. |
| Просроченные revenire | лиды с кастомным `Data revenire ≤ today` и без изменения статуса после этой даты |
| Когортная конверсия | `CLIENTI из лидов месяца M на дату D / USEFUL месяца M` — считается по снапшоту на D |
| Дельта к периоду | `(X_now − X_prev) / X_prev`; при `X_prev = 0` → «n/a» |

## Эталон

`tests/fixtures/etalon-2026-05.json`: лиды мая–июня 2026 из `SB KPi.xlsx` (лист `01_Input_Leads`) и ожидаемые значения всех KPI по 6 консультантам из `03_KPI_Agenti`. Тест `test_kpi_matches_etalon` должен проходить до любого рефакторинга `metrics/`.
