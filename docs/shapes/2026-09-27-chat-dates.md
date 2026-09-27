# Shape: конкретные даты в чате (27.09.2026)

## Goal

«Câte lead-uri am avut 25.09?» и «ce motive de pierdere au fost în august?» получают ответ из инструментов, а не `blocked_numbers`: к именованным периодам добавляются конкретный день и конкретный календарный месяц.

## Approach

Параметр периода (`period`, `period_a`, `period_b`) в strict-схеме становится `anyOf` из трёх вариантов: прежний enum именованных периодов, объект `{"day": "2026-09-25"}` (`format: date`) и объект `{"year": 2026, "month": 8}` (`month` integer enum 1..12). По skill claude-api strict поддерживает `anyOf` и `format: date`, но не `pattern` и не `minimum`/`maximum`, поэтому месяц не строкой «2026-08», а двумя целыми; год и границы проверяет pydantic. Объектная форма даёт один тип периода для всех четырёх инструментов, включая два периода `compare_periods`, без параллельных полей `date_a`/`date_b`.

`metrics/chat_periods.py` получает frozen dataclass-ы `ChatDay(day)` и `ChatMonth(first_day)`; `period_days` отдаёт для них (day, day) и (первый, последний день месяца). Окно строится прежним кодом: ежедневные окна первого и последнего дня, то есть `daily_window(day)` и `month_window` по построению. `named_period_window` переименовывается в `chat_period_window` (контракт поменялся, это не чистый рефакторинг). Конкретный день и месяц закрытые: `period_snapshot_date` применяет к ним правило закрытых периодов (снапшот последнего дня, иначе первый более поздний с подписью, иначе «нет данных»).

## Решения

- **Проверка даты** (граница, pydantic, контекст валидации дополняется `today` и датой первого успешного снапшота): день позже `today` или месяц позже текущего: ошибка инструмента «data … este în viitor»; день раньше границы или месяц, начинающийся раньше месяца границы: «nu există date înainte de …». Граница = первый успешный снапшот минус 12 месяцев (день месяца обрезается до последнего). Снапшотов нет: граница не проверяется, инструмент отдаёт прежнее «Nu există încă niciun snapshot.».
- **Сегодня и текущий месяц** нормализуются в `azi` и `luna_curenta`: иначе «câte lead-uri în septembrie» при закрытом правиле упёрлось бы в отсутствие снапшота 30.09. Подпись тогда прежняя, с именем периода.
- **Подпись и `days`**: `Perioada: 25.09.2026`, `Perioada: august 2026` без скобки с именем (скобка только у именованных). Полное название месяца из `RO_MONTHS` в `reports/render.py` (рядом с `RO_WEEKDAYS`; в `charts.yaml` названия сокращённые). В `content` поле `period` несёт ту же подпись, `days` остаётся «01.08–31.08.2026»: год и числа дат уже разрешены стражем.
- **Системный промпт**: `templates/chat_system.md` становится шаблоном с датой «Astăzi este 27.09.2026 (Europe/Bucharest).» и правилом: дата или месяц без года означают самый недавний, не в будущем («august» 27.09.2026 это 2026-08, «octombrie» это 2025-10); день передаётся как `{"day"}`, месяц как `{"year","month"}`; «luna trecută» и прочие именованные остаются enum-ом. Промпт короче порога кэша, кэша нет, дата в system ничего не ломает.

## Files

- `src/digest/metrics/chat_periods.py`: `ChatDay`, `ChatMonth`, тип `ChatPeriodChoice`, `period_days`, `chat_period_window`, `period_snapshot_date`.
- `src/digest/chat/tools.py`: схема `anyOf`, pydantic-модели `DayArgument`/`MonthArgument` с проверкой границ и нормализацией, подпись и `period_header` для дня и месяца.
- `src/digest/chat/loop.py`: `system_prompt(today)`; контекст валидации.
- `templates/chat_system.md` → рендер с датой (Jinja, как остальные шаблоны).
- `docs/kpi-definitions.md` («Режим вопросов»), `docs/PLAN.md`.

## Schema / API changes

Нет миграций. Меняется схема инструментов для модели (свойства периода `anyOf`).

## Test plan

Unit `tests/unit/test_metrics_chat_periods.py` (шов `chat_period_window`, `period_snapshot_date`):
- `test_specific_day_window_equals_daily_window`, `test_specific_month_window_equals_month_window` (включая месяц с переходом на зимнее время, октябрь).
- `test_specific_day_reads_its_own_snapshot`, `test_specific_month_without_end_snapshot_takes_first_later`, `test_specific_month_without_any_later_snapshot_has_no_data`.

Unit `tests/unit/test_chat_tools.py` (шов `run_tool`):
- `test_future_day_is_tool_error`, `test_future_month_is_tool_error`, `test_day_before_allowed_range_is_tool_error`, `test_month_before_allowed_range_is_tool_error`, `test_month_value_out_of_range_is_tool_error` (13).
- `test_today_as_specific_day_is_azi`, `test_current_month_is_luna_curenta`.
- `test_funnel_for_specific_day_matches_metrics`, `test_compare_periods_accepts_day_and_month`.
- `test_period_schema_is_anyof_of_enum_day_and_month`: схема strict, у вариантов `additionalProperties: false` и `required`.

Unit `tests/unit/test_chat_loop.py` (шов `answer_question` с фейковым клиентом):
- `test_system_prompt_states_today_in_bucharest`: в запросе system содержит «27.09.2026».
- `test_question_with_day_routes_to_funnel_with_that_day`: вопрос «Câte lead-uri am avut 25.09?», скрипт отдаёт `funnel(period={"day":"2026-09-25"})`, подпись «Perioada: 25.09.2026 · funnel()», числа проходят стража.
- `test_month_without_year_resolves_to_2026_08`: при today 27.09.2026 скрипт отдаёт `{"year":2026,"month":8}`, окно равно `month_window` августа, подпись «august 2026». Фейк не проверяет, что живая модель выберет 2026: это проверяется в тестовой группе (deploy.md §4 дополняется двумя вопросами).

## Tradeoffs / alternatives considered

- **Строка `"2026-08"` или `"2026-09-25"` в одном поле через `pattern`.** Короче, но `pattern` не входит в поддерживаемое strict (skill), разбор строки кодом возвращал бы ошибки там, где схема могла бы их исключить.
- **Сентинелы `period: "zi" | "luna"` плюс nullable `date`.** Плоская схема, но в `compare_periods` нужны `date_a` и `date_b`, связь «сентинел ↔ дата» проверяется только кодом, и strict заставляет передавать `date: null` во всех вызовах.
- **Год резолвит код, модель шлёт только месяц.** Детерминированно, но «august 2025» тогда не выразить; решение пользователя: дата в промпте, год выбирает модель, код проверяет границы.

## Open questions

Нет. Решения пользователя в задаче; нормализация сегодняшнего дня и текущего месяца и форма месяца двумя целыми выбраны мной, см. «Решения».
