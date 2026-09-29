# Shape: три новых инструмента чата (source_breakdown, manager_touches, repeat_clients)

Статус: подтверждён 29.09.2026 (раздел «Согласовано»).

## Goal

Чат отвечает про источники и кампании, касания консультантов и повторных клиентов теми же числами, что m7, w14 и m11, без новых формул.

## Approach

Три инструмента в `chat/tools.py` по образцу существующих: pydantic-модель аргументов (граница), strict-схема, функция над готовой функцией `metrics/`, результат с готовыми числами (урок сессии 39: всё, что модель могла бы вывести сама, считает код). Период берётся тем же `period_frame` (правило закрытых периодов, подписи, сноски о подмене снапшота). У `manager_touches` и `repeat_clients` свой допустимый набор периодов: схема получает подмножество enum, pydantic отклоняет остальное текстом для модели. Описания RO в `modules.yaml` `chat.tools`, как у шести существующих.

## Craft по шагам

0. `templates/chat_system.md`, правило compare: «Schimbarea o citezi exact din câmpul change, cu semnul ei; valorile perioadelor sunt deja în change, nu le repeta.» (убрать «cu perioadele»). Отдельный коммит `fix(chat)`, прогон `--only compare-leads-weeks,compare-offers-months,compare-scr-bucuresti`.
1. Шаг 0 craft (замер на живом снапшоте, только агрегаты): число различных `utm_campanie` и `source_name` за месяц и неделю; время `load_lead_frame` одного снапшота. От этого зависит отсечка строк (см. «Открытые вопросы», п. 1) и риск задержки `manager_touches`.
2. `source_breakdown`, `repeat_clients`, `manager_touches` с тестами, по одному коммиту `feat(chat)`.
3. Маска стража для подписей кампаний (п. 2 открытых вопросов).
4. Промпт, eval: 10 вопросов с тегом `i4`, ворота M6 считаются только по набору без тега.

## Files

- `templates/chat_system.md`: правка compare; в строке 3 перечень тем + «surse și campanii, atingerile consultanților, clienții care revin»; пример вопроса в правиле отказа не меняется.
- `config/modules.yaml` `chat.tools`: три описания; `chat.parameters`: `by`.
- `src/digest/config.py`: `ChatToolName` + три имени.
- `src/digest/chat/tools.py`: три модели аргументов, схемы в `tool_definitions`, три функции, `TOOL_FUNCTIONS`, `ARGUMENT_MODELS`.
- `src/digest/chat/loop.py`: маска стража дополняется именами из `ToolOutcome.masked_names` (п. 2).
- `templates/texts.j2`: тексты ошибок периода и «нет данных о касаниях».
- `src/digest/acceptance/chat_eval.py`: ворота M6 по кейсам без `i4`, строка «Набор I4: N/50».
- `tests/eval/chat-golden.yaml`: секция `# I4` в конце, 10 кейсов.
- `tests/unit/test_chat_tools.py`, `tests/unit/test_chat_loop.py`, `tests/unit/test_chat_eval.py`.
- `docs/PLAN.md`: убрать кандидата «инструмент чата для касаний».

`metrics/`: одна правка `refactor:` (`leads_with_key`/`key_share` на `LeadBreakdown`, `MonthlySourceConversion` делегирует), новых формул нет. Схема БД не меняется.

## 1. source_breakdown

Вызов: `lead_breakdown(frame, by, window, snapshot_date, config, min_leads)` над кадром `period_frame`. С шоурумом кадр сначала фильтруется по `frame["showroom"].eq(showroom)`: это отбор строк, не расчёт; тест доказывает, что `total` совпадает с `lead_counts_by_showroom[showroom]` (то есть с `funnel`).

Аргументы (`SourceBreakdownArguments`): `period: PeriodArgument` (все формы), `by: "source_name" | "utm_campanie"` (enum `BREAKDOWN_COLUMNS`), `showroom: toate | шоурум`.

Схема: `{period, by: enum, showroom: enum}`; описание параметра `by` в `chat.parameters`: «source_name: pe sursa lead-ului din CRM; utm_campanie: pe campania din UTM_Campanie».

Результат:
```
{ **period_header, "by": "source_name", "showroom": null,
  "row_count": 7,
  "rows": [ {"key": "Site", "leads": 41, "useful": 35, "offers": 12, "clienti": 3,
             "irr": "14,6%", "scr": "7,3%"}, ... ],        # порядок lead_breakdown
  "other": {"keys": [...], "key_count": 4, "leads": ..., ...} | null,
  "without_key": {"leads": ..., ...} | null,               # лиды без источника/кампании
  "total": {"leads": ..., "useful": ..., "offers": ..., "clienti": ..., "irr": ..., "scr": ...},
  "leads_with_key": 52, "key_share": "11,8%" }             # только для utm_campanie
```
`leads_with_key` и `key_share` считаются как `MonthlySourceConversion.leads_with_campaign`/`campaign_share`. Сам `monthly_source_conversion` жёстко месячный, поэтому в craft эти два свойства переносятся на `LeadBreakdown` (`refactor:` отдельным коммитом, m7 зовёт их оттуда, эталон зелёный до и после): формула одна, пользователей два. Подписи кампаний через `campaign_label(value, m7.campaign_label_max_length, hidden_campaign_label)`; сырое значение модели не уходит.

Описание RO (черновик):
> Lead-urile unei perioade pe sursă (source_name) sau pe campania din UTM_Campanie (utm_campanie): pentru fiecare rând lead-uri, utile, oferte, clienți, IRR și SCR. total este totalul perioadei, egal cu funnel; row_count dă numărul de rânduri; without_key sunt lead-urile fără sursă sau fără campanie; la campanii, key_share este ponderea lead-urilor cu campanie. Numele campaniilor se scriu exact ca în rezultat. Opțional pentru un singur showroom.

Ссылки `#id`: нет. Страж: имена источников уже в маске из конфига (`BIFE 2026`); подписи кампаний с цифрами из результата (п. 2).

## 2. manager_touches

Вызов: цепочка `touch_snapshot_dates(data.snapshot_dates, first_day, last_day)` → `data.load_frame` по каждой дате → `manager_touches(snapshots, days, config)`; для консультанта `.of_manager(name)`. Для `saptamana_trecuta` это та же цепочка и те же дни, что w14 воскресного отчёта: число совпадает по построению (тест).

Периоды: `azi`, `ieri`, `saptamana_curenta`, `saptamana_trecuta`, день `{"day"}`. Месяцы и `ultimele_30_zile` отклоняются: «Atingerile se numără pe zile și săptămâni; alege o zi sau o săptămână.» Причина: до 31 загрузки снапшота на вопрос и дедлайн ответа; w14 тоже недельный. Схема: `anyOf[enum подмножества, {day}]`, отдельная константа рядом с `PERIOD_PROPERTY`.

Аргументы (`ManagerTouchesArguments`): `period`, `manager: toti | консультант`.

Результат (toti):
```
{ "period": "saptamana_curenta", "days": "29.09–05.10.2026", "day_count": 7,
  "covered_from": "29.09.2026", "covered_until": "30.09.2026", "covered_day_count": 2,
  "days_without_snapshot": "…" | null, "days_without_snapshot_count": 0,
  "touch_count": 57,
  "by_level": [ {"status": "Revenire 1", "touch_count": 30}, ... ],   # полные имена статусов
  "manager_count": 6, "managers_without_touches": 1,
  "by_manager": [ {"manager": "...", "touch_count": 12, "by_level": [...]}, ... ] }
```
Для одного консультанта: `manager`, `touch_count`, `by_level` без `by_manager`. `covered_until` = последняя дата цепочки в периоде; `covered_day_count` = число дней от `covered_from` до `covered_until`; `days_without_snapshot` = `day_ranges_label` из w14 (импорт из `reports/modules/weekly.py`, второй пользователь). Сноски w14 становятся полями: «считаем с X», «нет снапшота за дни Y», и подпись под ответом получает `snapshot_notes` той же формулировкой, что сноска w14. `covered_from is None` (нет пары снапшотов, например `azi` до 19:00 или неделя без предыдущего снапшота) → `NoDataError` «Nu există încă două snapshoturi CRM pentru {days}; atingerile nu pot fi numărate.»

`by_level` списком с полным именем статуса, а не «R1» и не словарём: урок «Revenire 1/2/3» (сессия 39), и путь eval `by_level[status=Revenire 2].touch_count` работает только по списку.

Описание RO (черновик):
> Atingerile consultanților într-o zi sau săptămână: o atingere este trecerea unui lead în Revenire N înregistrată în CRM, nu apelul în sine. touch_count este totalul, by_level pe status (numele întregi, exact ca în rezultat), by_manager pe consultant; manager_count și managers_without_touches dau câți consultanți au, respectiv nu au atingeri. covered_from, covered_until și days_without_snapshot spun pentru ce zile există date; dacă lipsesc zile, o spui. Doar zile și săptămâni, nu luni. Opțional pentru un singur consultant.

Ссылки `#id`: нет (касание не проблема, которую надо открыть). Страж: имена статусов в маске уже есть.

## 3. repeat_clients

Вызов: `monthly_repeat_clients(frame.frame, frame.first_day, config)` над кадром `period_frame`. `report_date` = первый день месяца, а не `snapshot_date`: при подмене снапшотом следующего месяца окно иначе уехало бы.

Периоды: `luna_curenta`, `luna_trecuta`, `{"year","month"}`. Остальное: «Clienții care revin se numără pe lună; alege o lună.»

Аргументы (`RepeatClientsArguments`): только `period`. Шоурум не параметр: `by_showroom` три-четыре строки, уточнение «Dar Cluj?» читается из того же результата без второго вызова.

Результат:
```
{ **period_header,
  "clients": 42, "repeat": 5, "share": "11,9%",
  "by_reason": [ {"reason": "<text repeat_by_contact>", "client_count": 3},
                 {"reason": "Client Fidel", "client_count": 2} ],
  "by_showroom": [ {"showroom": "București", "clients": 20, "repeat": 3, "share": "15,0%",
                    "by_contact": 2, "by_source": 1}, ... ],   # только clients > 0
  "showroom_count": 3,
  "won_without_converted_at": 7 }
```
Имена источника причины из `sources.repeat_client` (в маске через конфиг). Предупреждение «до 2026 клиенты вне mefi не видны, доля занижена» в описании, не в результате.

Описание RO (черновик):
> Clienții care revin într-o lună: din clienții lunii (clients), câți sunt clienți anteriori (repeat, share), pe motiv (telefon sau e-mail al unui client anterior din CRM, ori sursa Client Fidel) și pe showroom. Clienții de dinainte de CRM (înainte de 2026) fără sursa Client Fidel nu se văd, deci procentul este subestimat. won_without_converted_at sunt lead-uri Clienți fără dată de conversie, neincluse. Doar pe lună.

Ссылки `#id`: нет. Только агрегаты; ключи контактов в результат не попадают (тест).

## Test plan

Швы: `run_tool(name, arguments, data)` (как у существующих), `tool_definitions(config)`, `checked_answer`, `summarize` eval.

- `test_source_breakdown_total_equals_funnel_leads` (компания и каждый шоурум, эталон `etalon-2026-05`).
- `test_source_breakdown_rows_match_m7_for_last_month` (строки ≥ порога m7 те же числа).
- `test_source_breakdown_campaign_label_hides_phone` (UTM с +40700000001 → «campanie ascunsă»).
- `test_manager_touches_last_week_matches_w14` (три снапшота, те же числа, что `manager_touches_report`).
- `test_manager_touches_single_consultant_uses_of_manager`.
- `test_manager_touches_rejects_month_period` (is_error, текст про дни и недели).
- `test_manager_touches_without_snapshot_pair_is_no_data`.
- `test_manager_touches_reports_days_without_snapshot` (поля и `snapshot_notes`).
- `test_repeat_clients_matches_m11_for_closed_month`.
- `test_repeat_clients_substituted_snapshot_keeps_period_month`.
- `test_repeat_clients_rejects_week_period`.
- `test_repeat_clients_result_has_no_contact_keys`.
- `test_tool_definitions_period_enums_per_tool` (подмножества enum в схемах).
- `test_guard_masks_campaign_label_digits` (п. 2): кампания «Promo 30» в результате не разрешает голую «30».
- `test_system_prompt_compare_rule_without_periods`.
- `test_m6_gate_ignores_i4_cases`, `test_golden_cases_load` (новые инструменты валидны в `ExpectedCall.tool`).

Реальный API: `eval chat --only` по 10 новым, затем полный прогон.

## Эталонный набор

30 вопросов M6 не трогаются. Секция `# I4` в конце файла, у каждого кейса тег `i4`. Ворота M6 считаются по кейсам без `i4` (как сейчас, ≥ 27/30); отдельной строкой «Набор I4: N/40, для ворот нужно 50». После этой фичи 40, ещё 10 вопросов до I4 добавим позже (кандидаты: compare по источникам, касания двух недель, вне темы).

Десять вопросов:

| id | вопрос | инструмент, аргументы | numbers |
|---|---|---|---|
| src-last-month | Câte lead-uri a adus fiecare sursă luna trecută? | source_breakdown luna_trecuta, source_name, toate | total.leads, row_count |
| src-site-scr-august | Ce SCR au avut lead-urile din Site în august? | source_breakdown {2026,8}, source_name, toate | rows[key=Site].scr |
| campaigns-this-month | Câte lead-uri au venit din campanii luna aceasta? | source_breakdown luna_curenta, utm_campanie, toate | leads_with_key |
| followup-src-cluj | Dar în Cluj? (after src-last-month) | source_breakdown luna_trecuta, source_name, Cluj | total.leads |
| touches-this-week | Câte atingeri au făcut consultanții săptămâna aceasta? | manager_touches saptamana_curenta, toti | touch_count |
| touches-moaca-day | Câte atingeri a avut Moaca Andreea pe 28.09? | manager_touches {day 2026-09-28}, Moaca Andreea | touch_count |
| touches-level-day | Câte lead-uri au trecut în Revenire 2 pe 28.09? | manager_touches {day 2026-09-28}, toti | by_level[status=Revenire 2].touch_count |
| repeat-last-month | Câți clienți care revin am avut luna trecută? | repeat_clients luna_trecuta | repeat, clients |
| repeat-august-cluj | Câți clienți care revin a avut showroomul din Cluj în august? | repeat_clients {2026,8} | by_showroom[showroom=Cluj].repeat |
| repeat-reason-this-month | Câți dintre clienții care revin luna aceasta au venit prin Client Fidel? | repeat_clients luna_curenta | by_reason[reason=Client Fidel].client_count |

Касания только на днях с парой снапшотов (27.09 → 28.09): «săptămâna trecută» 21–27.09 сейчас без пары и дала бы `no_data`. Кейс по прошлой неделе добавим, когда накопится неделя снапшотов.

Три примера:
```yaml
# I4: новые инструменты
- id: src-site-scr-august
  tags: [i4, source, specific_month]
  question: "Ce SCR au avut lead-urile din Site în august?"
  expect: answer
  calls:
    - tool: source_breakdown
      arguments: {period: {year: 2026, month: 8}, by: source_name, showroom: toate}
      numbers: ["rows[key=Site].scr"]

- id: touches-level-day
  tags: [i4, touches, specific_day]
  question: "Câte lead-uri au trecut în Revenire 2 pe 28.09?"
  expect: answer
  calls:
    - tool: manager_touches
      arguments: {period: {day: "2026-09-28"}, manager: toti}
      numbers: ["by_level[status=Revenire 2].touch_count"]

- id: followup-src-cluj
  tags: [i4, followup, source, showroom]
  after: src-last-month
  question: "Dar în Cluj?"
  expect: answer
  calls:
    - tool: source_breakdown
      arguments: {period: luna_trecuta, by: source_name, showroom: "Cluj"}
      numbers: [total.leads]
```

## Стоимость и токены

Оценка (замерить в первом прогоне по `usage`): описания трёх инструментов ≈ 3 × 130 токенов, схемы ≈ 3 × 120 (период `anyOf` повторяется), итого ≈ +750 токенов на каждый запрос к модели (сейчас ≈ 4,4k на запрос, +17 %). Кэширования промпта в `loop.py` нет, поэтому рост платится в каждом раунде.

- 30 вопросов M6: 265 883 → ≈ 310 000 входных токенов, ≈ $0,58 → ≈ $0,67.
- 10 новых: ≈ 11–13k входа на вопрос (результат `source_breakdown` по кампаниям до ≈ 1k токенов), ≈ $0,27.
- Полный прогон 40 вопросов: ≈ $0,95; `--only` по 10 новым ≈ $0,27.
- Прод: при лимите 30 вопросов в день ≈ +$0,05 в день к текущему.

Рычаг вне скоупа: `cache_control` на инструментах и системном промпте срезал бы вход раундов после первого примерно в 10 раз по цене чтения кэша; отдельный shape.

## Tradeoffs / alternatives considered

- **Параметр `by` у `funnel` вместо `source_breakdown`.** Отвергнуто: у `funnel` один набор чисел, у разбивки строки; одна схема с двумя формами результата путала бы модель и eval-пути.
- **Параметр `source` у `funnel` (воронка одного источника).** Отвергнуто: enum из ~20 источников в схеме, и вопросы «какой источник лучше» всё равно требуют всех строк.
- **Все периоды для касаний.** Отвергнуто: месяц это до 31 загрузки снапшота на вопрос (дедлайн ответа) и нет отчёта-двойника для сверки; w14 недельный.
- **Шоурум параметром у `repeat_clients`.** Отвергнуто: 3–4 строки дешевле лишнего параметра и второго вызова.
- **Ссылки `#id` у повторных клиентов.** Отвергнуто: это не проблема для действия, а список «вернувшихся покупателей» ближе к персональным данным, чем нужно агрегату.

## Риски

- Рост описаний: 9 инструментов, два пересечения по смыслу (`funnel` ↔ `source_breakdown`, `manager_kpi` ↔ `manager_touches`). Мера: первые фразы описаний различают вопрос («pe sursă», «atingeri»); провалы маршрутизации видны в eval по тегу.
- Задержка `manager_touches`: до 8 загрузок кадра на вопрос; медиана M6 ≤ 15 с. Замер в шаге 1.
- Сравнение касаний двух недель: разницы нет в результате, модель посчитает сама → страж. Не закрываем, видно в eval; если вопрос частый, отдельный shape.
- Две кампании с одинаковой подписью после обрезки до 40 символов: две строки с одним `key`. Числа верны, текст неоднозначен; принять.
- Фильтр кадра по шоуруму для разбивки: равенство с `lead_counts_by_showroom` держит тест на эталоне.

## Open questions

1. **Отсечка строк.** Предлагаю `min_leads = 1` (все строки, ничего не сворачивается) для обоих `by`: число в чате по ключу совпадает с m7 для строк выше порога, итог совпадает всегда. Если замер шага 1 даст > 20 кампаний за месяц, для `utm_campanie` берём порог m7 `min_campaign_leads` (5) с `other`. Согласны?
2. **Маска кампаний не из конфига.** Промпт просит новые имена с цифрами в маску из конфига, но UTM_Campanie это данные, в конфиге их нет. Без маски «Promo 30» в результате разрешает голую «30» во всём ответе (та же дыра, что «Revenire 2» до сессии 39). Предлагаю `ToolOutcome.masked_names` (подписи кампаний с цифрами из результата), страж объединяет их с маской конфига. Альтернатива: не маскировать и принять риск. Какой вариант?
3. **Период по умолчанию для касаний** при вопросе без периода («câte atingeri are Marc?»): промпт говорит «ближайший по смыслу»; предлагаю не добавлять правило, модель выберет `saptamana_curenta`. Нужна ли явная строка в промпте?

## Согласовано (29.09.2026)

1. **Отсечка строк.** `min_leads = 1` для обоих `by`. Если замер шага 1 даст > 20 кампаний за месяц, для `utm_campanie` порог m7 `min_campaign_leads` с `other`.
2. **Маска кампаний.** `ToolOutcome.masked_names`: подписи кампаний с цифрами из результата; страж объединяет их с маской конфига. Правила те же: с учётом регистра, исключение после «Data revenire». Тест: «Promo 30» в результате не разрешает голую «30».
3. **Период по умолчанию для касаний.** Явная строка в `chat_system.md`: вопрос о касаниях без периода → `saptamana_curenta`, и период назван в ответе.
4. **Правка compare** в `chat_system.md` (убрать «cu perioadele») первым коммитом.
5. Остальное по shape.
