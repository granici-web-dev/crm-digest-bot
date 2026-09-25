# KPI — определения, формулы, пороги

Формулы — по `docs/brief.md` §3 (ADR-002). `SB KPi.xlsx` составлен через ИИ, из него берутся только лиды для эталона; расхождения с workbook перечислены в конце. Пороги и цели — `config/kpi.yaml`. Изменение формулы или порога — только через ADR и пересборку эталона.

Считаются по консультанту, по шоуруму и по компании за период.

## Признаки лида

| Признак | Определение |
|---|---|
| категория | по `status.name` из `config/status-mapping.yaml` (инвариант 4), не подстрокой |
| `is_clienti` | категория `WON`: `status.name = Clienți`. `converted_at` в счёт не идёт, расхождение с ним — алерт снапшота |
| `is_irelevant` | `LOST · IRELEVANT`: IRELEVANT, SPAM |
| `is_partnership` | `PARTNERSHIP`: DESIGNER, INFLUENCER |
| `is_nu_a_raspuns` | `LOST · NU RASPUNS` |
| `is_buget` | `LOST · BUGET` |
| `is_produs_nepotrivit` | `LOST · PRODUS NEPOTRIVIT` |
| `is_ofertat` | кастомное поле `Ofertat` (field_id 20) = `✅DA` |
| `is_showroom_visit` | `source.name = Showroom` |
| консультант | `assigned_to.id` → `config/managers.yaml`, имя не сравнивается |

Лиды без консультанта входят в итог по компании, но ни в одну строку по консультантам.

## Базовые множества (за период, по `created_at` лида)

| Обозначение | Определение |
|---|---|
| `LEADS` | лиды с `created_at` в периоде, **кроме** PARTNERSHIP и лидов тестовых аккаунтов (`test_account: true`) |
| `IRR_LEADS` | `LEADS` с `is_irelevant` |
| `USEFUL` | `LEADS − IRR_LEADS` (= все − IRELEVANT − PARTNERSHIP, бриф §3) |
| `CLIENTI` | `LEADS` с `is_clienti` |
| `OFFERS` | `LEADS` с `is_ofertat` |
| `NAR` | `LEADS` с `is_nu_a_raspuns` |
| `BUGET` | `LEADS` с `is_buget` |
| `PNP` | `LEADS` с `is_produs_nepotrivit` |
| `SHOWROOM_VISITS` | `LEADS` с `is_showroom_visit` |
| `ACTIVE_OFFERS_14` | `OFFERS`, не `CLIENTI`, не `IRR_LEADS`, `analysis_date − date(last_contact_at) > 14` дней; `last_contact_at = null` → не входит |

`analysis_date` — дата расчёта отчёта, дата `last_contact_at` берётся в Europe/Bucharest. Порог дней — `active_offer_stale_days`. `status_changed_at` не используется: в mefi он `null`, если статус задан при создании лида.

## KPI (отчёты MVP: m5 и режим вопросов)

| KPI | Название | Формула | Цель |
|---|---|---|---|
| SCR | Sales Conversion Rate | `CLIENTI / USEFUL` | > 10 % |
| L2O | Lead to Offer | `OFFERS / USEFUL` | > 50 %, информативно |
| O2C | Offer to Contract | `CLIENTI / OFFERS` | > 20 %, информативно |
| CDR | Contact Discipline Rate | `(LEADS − NAR) / LEADS` | > 90 % |
| PLR | Price Lost Rate | `BUGET / (LEADS − IRR_LEADS − NAR)` | < 25 % |
| SC | Showroom Conversion | `count(CLIENTI ∩ SHOWROOM_VISITS) / count(SHOWROOM_VISITS)` | > 20 % |
| PFR | Product Fit Rate | `PNP / USEFUL` | < 10 % |
| ACR | Active Control Rate | `ACTIVE_OFFERS_14 / LEADS` | < 20 % |
| IRR | Irrelevant Rate | `IRR_LEADS / LEADS` | ≤ 20 % |

Цели — секция `targets` в `config/kpi.yaml`: первые ступени порогов (`scr_elite`, `cdr_minim`, `plr_maxim`, `sc_perfect`, `pfr_perfect`, `acr_perfect`, `irr_acceptabil`) плюс L2O и O2C. Они тоже `provisional`, но показываются как ориентир рядом с фактом. Цель выполнена при ≥ (↑) или ≤ (↓) включительно, как ступени баллов; KPI = null → «—».

Доли не округляются до вывода. **Деление на ноль:** метрика `null`, в отчёте и в ответе чата «—».

**Период:** все счётчики фильтруются по `created_at` лида, полуинтервал `[start, end)`, Europe/Bucharest.

`is_duplicate` в KPI v1 не учитывается; пересмотреть после оценки доли дублей в снапшотах.

## Предварительно, не показывать до калибровки (ADR-002)

Механика ниже пришла из workbook и не согласована с владельцем. Функции в `metrics/` есть, пороги в `config/kpi.yaml` (`status: provisional`), но в отчёты MVP и в ответы чата SPI, баллы, уровни и рекомендации не выводятся. Калибровка — по 2–3 месяцам снапшотов плюс Excel 2024–2025, отдельным ADR. Ступени, очки и цепочка рекомендаций — секции `scores`, `irr_penalty`, `recommendations` в `config/kpi.yaml`. KPI = `null` → очки нижней ступени (штраф IRR −10), условие рекомендации не срабатывает; при `LEADS = 0` рекомендации нет.

### Баллы

Проверка сверху вниз, первая выполненная ступень.

| KPI | Ступень 1 | Ступень 2 | Ступень 3 | Иначе | Макс. |
|---|---|---|---|---|---|
| SCR ↑ | ≥ `scr_elite` (10 %) → 30 | ≥ `scr_bine` (7 %) → 24 | ≥ `scr_minim` (5 %) → 18 | 10 | 30 |
| CDR ↑ | ≥ `cdr_perfect` (95 %) → 20 | ≥ `cdr_minim` (90 %) → 16 | ≥ `cdr_contact_floor` (80 %) → 10 | 5 | 20 |
| PLR ↓ | ≤ `plr_perfect` (20 %) → 20 | ≤ `plr_maxim` (25 %) → 16 | ≤ `plr_problema` (35 %) → 10 | 5 | 20 |
| SC ↑ | ≥ `sc_perfect` (20 %) → 15 | ≥ `sc_bine` (15 %) → 12 | ≥ `sc_minim` (10 %) → 8 | 4 | 15 |
| PFR ↓ | ≤ `pfr_perfect` (10 %) → 10 | ≤ `pfr_bine` (15 %) → 8 | ≤ `pfr_maxim` (20 %) → 5 | 2 | 10 |
| ACR ↓ | ≤ `acr_perfect` (20 %) → 5 | ≤ `acr_maxim` (30 %) → 3 | — | 1 | 5 |

Штраф IRR: ≤ `irr_acceptabil` (20 %) → 0; ≤ `irr_atentie` (30 %) → −5; иначе −10.

`SPI = max(0, сумма баллов + штраф IRR)`, максимум 100.

### Уровни (`config/kpi.yaml` → `levels`)

| SPI | Уровень |
|---|---|
| ≥ 90 | Elite |
| ≥ 80 | Gold |
| ≥ 70 | Silver |
| ≥ 60 | Bronze |
| < 60 | Coaching |

### Главная рекомендация

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
| Просроченные revenire | лиды с кастомным `Data revenire ≤ today` и без изменения статуса после этой даты (`status_changed_at` null или раньше `Data revenire`). Категории: ACTIVE, ACTIVE_FOLLOWUP, LOST · STAND BY, UNMAPPED; WON, PARTNERSHIP и остальные причины LOST не входят |
| Когортная конверсия | `CLIENTI из лидов месяца M на дату D / USEFUL месяца M` — считается по снапшоту на D |
| Дельта к периоду | `(X_now − X_prev) / X_prev`; при `X_prev = 0` → «n/a» |

## Эталон

`tests/fixtures/etalon-2026-05.json` — регрессионный фикстур. Собирается `scripts/build_etalon.py` из `docs/reference/SB KPi.xlsx`: 314 лидов 01.05–16.06.2026 без контактов клиента, пороги `02_Setari_Targete`, `analysis_date` 2026-06-27.

- `expected_by_agent` — счётчики и 9 KPI по 6 консультантам, посчитаны скриптом по формулам этого файла независимо от `metrics/` (простые циклы, без pandas).
- `excel_reference` — значения `03_KPI_Agenti` как есть, для сравнения; тесты по нему не проверяют.
- `meta.manual_check` — Moaca Andreea пересчитана вручную по счётчикам; скрипт падает при расхождении. Клиентов у неё нет, пересечение в SC ручной проверкой не покрыто.

Консультанты в эталоне по имени (в Excel нет `assigned_to.id`), с пробелами, схлопнутыми как `TRIM` в workbook.

## Расхождения с SB KPi.xlsx

| Тема | Workbook | У нас |
|---|---|---|
| Партнёрства | DESIGNER, INFLUENCER входят в `LEADS` и `USEFUL` | исключены из `LEADS` |
| SC | все клиенты консультанта / визиты | клиенты среди визитов / визиты |
| Деление на ноль | `IFERROR(…, 0)` | `null`, «—» |
| Категории | поиск подстроки в нормализованном статусе | точный `status.name` через конфиг; SPAM = IRELEVANT |
| Консультант | `TRIM(Desemnat)` | `assigned_to.id` |
| ACR | формула `01_Input_Leads!AC` ссылается на пустую `02_Setari_Targete!$B$2`, ACR = 0 у всех | по дате расчёта |
| `cdr_contact_floor` | 80 % зашит в формулу `03_KPI_Agenti!W` | ключ в `config/kpi.yaml` |
