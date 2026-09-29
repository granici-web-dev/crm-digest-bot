# Shape: w13 «Calitatea datelor CRM»

Статус: отложен 29.09 (см. «Решение 29.09: w13 отложен» в конце). Кода w13 нет.

## Цель

Раз в неделю руководство видит, у кого из консультантов лиды с незаполненными полями, от которых зависят отчёты: шоурум, ответственный, Data revenire, Ofertat, дата конверсии, статус. Итог сравнивается с прошлой неделей. Полный список лидов с названием проверки лежит в Excel w12.

## Близкий модуль

В `docs/report-menu.md` такого нет: d3 считает просроченные даты, а не пустые; ops-алерты (UNMAPPED, Clienți без `converted_at`) уходят в служебный бот и в группу не попадают. Новый id **w13**, `name: data_quality`, label «Calitatea datelor CRM».

## Подход

Проверки задаются списком в `status-mapping.yaml`. Каждая проверка это id, подпись RO, `applies_to` (категории и ключи причин LOST, которые уже есть в конфиге) и необязательное `missing` (поле кадра из закрытого enum). Мини-языка условий нет: у проверки только «лид из `applies_to`» и, по желанию, «поле пустое». Чистая функция `metrics/data_quality.py` строит булев кадр «лид × проверка» и сводку по консультантам. Эту сводку читают модуль w13, лист w12 и будущий инструмент чата.

## Охват (требование 2)

Одна глобальная граница плюс `applies_to` у каждой проверки:

1. `created_at ≥ data_quality.leads_created_from` (2026-06-01). Отдельный ключ, а не `clients.contracts_count_from`: значение то же, смысл разный (ручной ввод старых клиентов против начала работы продавцов в mefi). Если одну дату поменяют, вторая молча не сдвинется.
2. PARTNERSHIP и лиды тестового аккаунта вне охвата: первые не лиды (ADR-002), вторые уже убраны в `prepare_lead_frame`.
3. Лид моложе `data_quality.min_lead_age_hours` (24) на конец окна снапшота не проверяется: лид с сайта, созданный в воскресенье в 18:00, ещё никто не успел заполнить, а за «не взятые» уже отвечает d2.
4. Дальше каждая проверка смотрит только на свои категории.

**Почему не глобальное «не LOST».** В конфиге Stand BY это причина LOST (`STAND_BY`), глобальный фильтр «открытые» выкинул бы проверку «Stand BY без Data revenire». Clienți без даты конверсии это WON, тоже не «открытый». Поэтому архив определяется иначе: **LOST кроме `STAND_BY` не входит ни в одну проверку**. Продавцу не покажут закрытый NU A RASPUNS или IRELEVANT без шоурума: исправлять там нечего, и отчёты по потерям это поле почти не двигает. Валидатор конфига запрещает указывать в `applies_to` причину LOST без `followup_field`, чтобы архив не вернулся правкой YAML.

WON входит в проверки шоурума и ответственного: контракт без шоурума или консультанта выпадает из m2, m4, m5 и d6.

## Ключи конфига

`config/status-mapping.yaml`, новая секция рядом с `custom_fields`:

```yaml
data_quality:
  leads_created_from: 2026-06-01
  min_lead_age_hours: 24
  # applies_to: категории (WON, ACTIVE, ACTIVE_FOLLOWUP, UNMAPPED) и ключи причин LOST
  # с followup_field. missing: showroom | responsible | data_revenire | ofertat | converted_at | source.
  # responsible пустой = assigned_to пуст или not_taken в managers.yaml.
  checks:
    - { id: fara_showroom,     applies_to: [ACTIVE, ACTIVE_FOLLOWUP, STAND_BY, WON, UNMAPPED], missing: showroom,      label: "Fără showroom" }
    - { id: fara_responsabil,  applies_to: [ACTIVE, ACTIVE_FOLLOWUP, STAND_BY, WON, UNMAPPED], missing: responsible,   label: "Fără responsabil" }
    - { id: fara_data_revenire, applies_to: [STAND_BY, ACTIVE_FOLLOWUP],                        missing: data_revenire, label: "Fără Data revenire" }
    - { id: ofertat_gol,       applies_to: [ACTIVE, ACTIVE_FOLLOWUP],                          missing: ofertat,       label: "Ofertat necompletat" }
    - { id: fara_data_conversie, applies_to: [WON],                                            missing: converted_at,  label: "Clienți fără dată de conversie" }
    - { id: status_necunoscut, applies_to: [UNMAPPED],                                                                 label: "Status necunoscut" }
```

`ofertat` пустой = `ofertat IS NULL` в снапшоте: поле пустое или значение не из ✅DA/❌NU (второе уже алертится снапшотом). Pydantic-модель `DataQualityCheck`, валидаторы: id уникальны, `applies_to` из известных категорий и ключей причин с `followup_field`, `missing` из enum `DataQualityField`.

`config/modules.yaml`: `w13: { name: data_quality, label: "Calitatea datelor CRM", sources: [A], enabled: true }`. Своих `params` нет: всё в секции выше, её же читает лист w12 и будущий инструмент чата.

## Сигнатуры

`src/digest/metrics/data_quality.py`:

```python
def data_quality_flags(lead_frame: pd.DataFrame, analysis_date: date, config: AppConfig) -> pd.DataFrame
    # индекс лидов в охвате, по булевой колонке на check.id в порядке конфига

@dataclass(frozen=True)
class ManagerDataQuality:
    manager_name: str | None          # None: «Fără responsabil»
    lead_count: int                    # различные лиды хотя бы с одной проверкой
    counts_by_check: dict[str, int]    # только ненулевые, в порядке конфига
    lead_ids: tuple[int, ...]          # старые первыми

@dataclass(frozen=True)
class DataQuality:
    leads_created_from: date
    lead_count: int
    counts_by_check: dict[str, int]
    groups: tuple[ManagerDataQuality, ...]
    complete_managers: tuple[str, ...]      # активные консультанты без единой проверки
    lead_ids: tuple[int, ...]
    previous_lead_count: int | None
    previous_snapshot_date: date | None
    def of_manager(self, manager_name: str) -> "DataQuality"   # для инструмента чата, как OverdueRevenire.of_manager

def data_quality(lead_frame, previous_week: PreviousSnapshot | None, report_date, config) -> DataQuality
```

Группировка через `manager_names` из `daily_checks.py`: `not_taken` и пустой `assigned_to` дают `None`, то есть строку «Fără responsabil». Проверка `fara_responsabil` поэтому всегда попадает только в эту строку, особого случая в коде нет. Лиды неактивных сотрудников (Palega, Iordache, Elena Ureche) идут строкой под своим именем после консультантов: спрятать их значит потерять лиды из итога. Unknown id уже алертит раннер.

**Порядок строк по `managers.yaml` (шоурум, затем порядок в файле), а не по числу лидов.** Сортировка по убыванию превратила бы блок в рейтинг худших.

Прошлая неделя: та же функция на `previous_week.frame` с `analysis_date = previous_week.snapshot_date`. Снапшот берёт раннер по правилу w6 (`previous_week_snapshot`: воскресенье, иначе `first_snapshot_on_or_after(..., until=снапшот отчёта)` строго раньше, иначе `None` → «—»). В раннере загрузка `previous_week` включается, если среди модулей есть w6 **или** w13 (`PREVIOUS_WEEK_MODULE_IDS`).

## Шаблон RO (`templates/data_quality.j2`)

Тон нейтральный: «de completat», не «erori», без рейтинга, строка тех, у кого всё заполнено.

```
📋 Câmpuri de completat în mefi (lead-uri create din 01.06.2026, fără cele închise)
Total: 118 lead-uri (săpt. trecută: 131)

Roibu Valeria 14: Ofertat necompletat 9, Fără Data revenire 5
Moaca Andreea 6: Fără showroom 2, Ofertat necompletat 4: #1712 #1730
Dragoi Mihaela 21: ...
Fără responsabil 38: Fără responsabil 38, Fără showroom 11
Complet: Godja Adina Maria, Marc Andra
Lista completă: foaia „Calitatea datelor” din fișierul Excel.
```

Ссылки по инварианту 7: 10 на весь блок, самые старые лиды первыми, `LeadLinks.block_lines` как в d3, остаток «și încă N». Прошлой недели нет: «(săpt. trecută: —)». Подмена снапшота подписывается как в w6. Лидов нет: «Toate câmpurile sunt completate». Итог это число различных лидов, у консультанта число различных лидов и разбивка по проверкам (сумма по проверкам может быть больше итога, это видно по формату «N: a, b»).

## Excel w12

Новый лист «Calitatea datelor» (имя из `templates/texts.j2`) в `weekly_workbook`: строка на пару лид × проверка, столбцы ID (гиперссылка, как на листе «Lead-uri»), Consilier, Showroom, Status, Creat, Verificare. Контактов нет, столбцы только из кадра снапшота. Лист есть всегда, когда w12 включён, независимо от w13.

## Файлы

- `config/status-mapping.yaml`: секция `data_quality`.
- `config/modules.yaml`: w13.
- `src/digest/config.py`: `DataQualityCheck`, `DataQualitySettings`, enum `DataQualityField`, валидаторы.
- `src/digest/metrics/data_quality.py`: новая.
- `src/digest/reports/modules/weekly.py`: `data_quality_report`; `modules/__init__.py`: w13 в `IMPLEMENTED_MODULES`.
- `src/digest/reports/modules/weekly_excel.py`: лист.
- `src/digest/reports/runner.py`: `PREVIOUS_WEEK_MODULE_IDS`.
- `templates/data_quality.j2`, `templates/texts.j2` (имя листа, заголовки столбцов).
- `docs/kpi-definitions.md`: раздел «Качество данных (w13)»; `docs/report-menu.md`: w13; `docs/INDEX.md` не меняется.

## Тесты

Seam: `data_quality_flags`, `data_quality`, загрузка конфига, рендер шаблона, `weekly_workbook`, выбор снапшотов в раннере.

`tests/unit/test_metrics_data_quality.py`:
- `test_each_check_fires_only_for_its_categories` (параметризован по проверкам)
- `test_lead_created_before_cutoff_is_not_checked`
- `test_closed_loss_is_never_checked_but_stand_by_is`
- `test_partnership_is_not_checked`
- `test_lead_younger_than_min_age_is_not_checked` (граница ровно 24 ч, Europe/Bucharest)
- `test_not_taken_and_unassigned_leads_go_to_fara_responsabil`
- `test_lead_with_two_issues_counts_once_in_total_and_in_both_checks`
- `test_rows_follow_managers_yaml_order_not_counts`
- `test_active_manager_without_issues_is_listed_as_complete`
- `test_inactive_employee_leads_get_named_row`
- `test_previous_week_uses_previous_frame_and_its_snapshot_date`
- `test_no_previous_week_gives_none`
- `test_lead_ids_oldest_first`
- `test_of_manager_filters_groups`

`tests/unit/test_config.py`: неизвестная категория в `applies_to`, причина LOST без `followup_field`, неизвестное `missing`, повтор id: ValueError.
Рендер: syrupy на четырёх наборах (проверки есть, пусто, без прошлой недели, ссылки сверх лимита); нет кириллицы.
Excel: лист есть, строк = число пар, столбцы без контактов, нет кириллицы.
Раннер (интеграционный): при включённом только w13 `previous_week` загружается.
Эталон не трогается: новых формул KPI нет.

## Альтернативы (отвергнуты)

- **Мини-язык условий в YAML** (`field: showroom, op: is_null, and: ...`): гибче, но это интерпретатор без второго потребителя. `applies_to` + `missing` покрывает весь стартовый набор и все кандидаты ниже, кроме противоречий между полями.
- **Глобальный охват «категории ACTIVE и ACTIVE_FOLLOWUP»** из промпта: теряет Stand BY и Clienți без даты, см. «Охват».
- **Секция в `modules.yaml` `params` w13**: список проверок нужен ещё листу w12 и чату, выключение w13 не должно ломать им конфиг.
- **Динамика по каждому консультанту**: шум на малых числах (2 → 3 это «+50 %») и ещё сильнее похоже на разбор полётов. Только итог, как просили.

## Кандидаты в проверки (не добавляю без согласования)

Дёшевы, считаются из того же кадра:

1. **Fără sursă** (`missing: source`): сейчас 0, но от источника зависят w1, w6, m7, d1. Ноль стоит одной строки конфига и сработает, когда форма сайта сломается.
2. **Статус «Ofertat», а поле Ofertat не ✅DA.** Противоречие: L2O считает по полю. Нужен второй вид проверки (`status_name` из списка + поле ≠ значение), это +1 ветка кода.
3. **Шоурум лида не совпадает с шоурумом консультанта** (`managers.yaml showroom`). Разъезжаются цифры m4 по шоуруму и m5 по консультанту. Сначала замер: передача клиента между шоурумами может быть нормой.
4. **Лид на неактивном сотруднике** (Palega, Iordache, Elena Ureche): его никто не ведёт.

Отвергнутый кандидат: «дата конверсии есть, статус не Clienți». Это П1 (возможные расторжения), и алерт в ops уже есть. В группе это выглядело бы как обвинение до ответа продавцов.

## Риски

- **Продавцы воспримут как донос.** Меры: нейтральный заголовок «Câmpuri de completat», строка «Complet: …», порядок по шоуруму, без процентов и рейтинга, без динамики по людям. Состав группы и формат по именам спрашиваем у директора продаж (П6).
- **Одни и те же лиды каждую неделю.** Если никто не правит, блок становится фоном. Динамика итога это показывает; если за 3–4 недели итог не падает, это сигнал владельцу, а не повод менять формулу.
- **Шум Ofertat.** Возможно, продавцы ставят поле только при оферте, и пустое у IN PROCES это норма. Тогда проверка даст сотню лидов. Вопрос П5; до ответа проверка в конфиге, но её легко убрать строкой.
- **Цифры из промпта посчитаны по всем 1793 лидам**, в том числе закрытым. При охвате shape они будут меньше. Первый шаг craft: скрипт-замер по снапшоту 28.09 (только агрегаты по проверкам и консультантам, без id в чате), цифры записать в kpi-definitions.
- **Ссылок 10 на весь блок**: при ~100 лидах ссылки получат только самые старые, почти всегда один консультант. Так же, как d3; полный список в Excel.

## Открытые вопросы (к пользователю, до craft)

1. `not_taken` (Marketing Sofa) считаю «Fără responsabil», как в d2 и d3. Или отдельная строка «Marketing Sofa»?
2. Лист в w12 всегда или только при включённом w13? Сейчас: всегда (так проще, без передачи списка модулей в контекст).
3. Порог свежести 24 ч устраивает? Альтернатива 0: тогда воскресные лиды с сайта всегда попадают в блок.
4. Какие из четырёх кандидатов добавить.
5. Ofertat в Stand BY не проверяю («в работе» = ACTIVE, ACTIVE_FOLLOWUP). Согласны?

Вопросы клиенту записаны в `docs/owner-questions.md`: П5 (обязательность Data revenire и Ofertat), П6 (показ по именам в группе).

## Решение 29.09: w13 отложен

**Замер по снапшоту 28.09** в охвате shape (создан с 01.06, без PARTNERSHIP, старше 24 ч, открытые плюс Stand BY): 220 лидов. Без шоурума 0; без ответственного или на Marketing Sofa 0; Ofertat пустой у ACTIVE/ACTIVE_FOLLOWUP 0; статус Ofertat при поле не ✅DA 0; Stand BY или Revenire 1–3 без Data revenire 70. Пробелы из промпта были в закрытых лидах.

**w13 не делаем**, пока проверки не начнут давать ненулевые значения: недельный модуль, где пять проверок из шести нулевые, это шум. Shape остаётся как спроектированный вариант.

**Единственная реальная проблема переносится в d3.** Лиды без Data revenire в d3 не попадают никогда: d3 берёт `Data revenire < today`. Новая строка d3 «Fără Data revenire: N» итогом и по консультантам, правило в `docs/kpi-definitions.md` (d3), выключается параметром d3 `missing_followup_date`, если владелец ответит, что поле необязательно (П5).

**Открытые вопросы:** П6 снят; вопросы 1–4 сняты вместе с w13; вопрос 5: да, Ofertat проверяется только у ACTIVE/ACTIVE_FOLLOWUP.
