# Shape: m11 «Clienți care revin» и белый список верхнего уровня лида (28.09.2026)

## Goal

A: в месячном отчёте строка «Clienți care revin: 3 din 38 (7,9%)» с разбивкой по шоурумам и колонкой «Revine» в Excel m19. B: незнакомый ключ верхнего уровня лида не пишется в `lead_snapshots.raw`; `elimination` пишется без `detailed_reason`.

## Approach

A. `metrics/monthly.py`: `repeat_client_flags(lead_frame)` помечает у каждого лида WON с `converted_at` признак «есть другой лид WON с тем же непустым ключом телефона или e-mail и `converted_at` строго раньше» (сортировка по `converted_at`, накопленные множества ключей; равные `converted_at` друг друга не делают повторными). `monthly_repeat_clients(lead_frame, report_date, config)` → `MonthlyRepeatClients`: `company` и `by_showroom` (`RepeatClientCounts`: `clients`, `repeat`, свойство `share` через `ratio`, 0/0 → None) по клиентам месяца = WON с `converted_at` в `month_window`; строки шоурумов через `showroom_keys`, как `converted_count_by_showroom`; плюс `won_without_converted_at` по всему снапшоту (такой лид нельзя отнести к месяцу). Сравнение внутри одного снапшота, как revenire d1 (ADR-006).
Модуль `repeat_clients_report` в `reports/modules/monthly.py`, шаблон `templates/repeat_clients.j2`: строка итога, строки шоурумов (без шоурума только при ненулевом числе), сноска про телефон/e-mail и клиентов до mefi; при `won_without_converted_at > 0` вторая сноска «N lead-uri Clienți fără dată de conversie nu sunt incluse.»; отдельного алерта нет, расхождение WON и `converted_at` уже алертит снапшот. m11 `enabled: true`, label «Clienți care revin».

B. `lead_to_snapshot_row`: raw = только ключи из `raw_known_keys`, затем `keep_known_nested_keys` (новое `raw_known_nested_keys: {elimination: [type, reason, detailed_reason, marked_at, marked_by]}`, как у клиентов), затем `raw_strip` (плюс `elimination.detailed_reason`), затем `keep_custom_fields`. Незнакомый вложенный ключ `elimination.x` — та же проблема `unknown_raw_key`. Алерт при первом появлении уже есть в раннере; текст меняется на «в raw не записан». Валидаторы `StatusMapping` как у `ClientSettings`: `raw_strip` и `raw_known_nested_keys` ⊂ `raw_known_keys`.

## Files

- `src/digest/metrics/monthly.py`: `RepeatClientCounts`, `MonthlyRepeatClients`, `repeat_client_flags`, `monthly_repeat_clients`.
- `src/digest/reports/modules/monthly.py`, `modules/__init__.py`: m11.
- `src/digest/reports/modules/monthly_excel.py`, `weekly_excel.py`: `write_lead_sheet` получает необязательные дополнительные колонки; новый лист «Clienți luna»: клиенты месяца m11, «Zi» = день `converted_at`, колонка «Revine».
- `templates/repeat_clients.j2`, подписи в `templates/texts.j2`.
- `config/modules.yaml`: m11 on; `config/status-mapping.yaml`: `elimination`, `raw_known_nested_keys`, `raw_strip`.
- `src/digest/config.py`, `src/digest/snapshot.py`, `src/digest/reports/runner.py` (текст алерта).
- `docs/kpi-definitions.md`: раздел «Месячное окно», m11 и ограничения; `docs/PLAN.md`.

## Schema / API changes

Нет. Миграции нет: до сих пор незнакомые ключи алертились с «сохранён в raw»; если такой алерт приходил, вычистить отдельно.

## Test plan

Unit `tests/unit/test_monthly.py` (seam `monthly_repeat_clients`): повторный по телефону; по e-mail; первая покупка не повторная; совпадение с лидом не WON не считается; совпадение с WON с более поздним `converted_at` не считается; равный `converted_at` не повторный; шоурум по лиду месяца (прошлая покупка в другом шоуруме); WON без `converted_at` в счётчике и не в клиентах; 0 клиентов → share None.
Рендер m11 syrupy; m19: лист «Clienți luna», число строк = клиенты m11, «Revine» DA/NU.
Unit снапшота (seam `lead_to_snapshot_row`): незнакомый верхний ключ не в raw и даёт `unknown_raw_key`; `elimination.detailed_reason` не в raw, остальное `elimination` в raw. Интеграция раннера: незнакомый ключ алертится в первый день, на второй нет. Конфиг: `raw_strip` вне `raw_known_keys` → ошибка загрузки.

## Tradeoffs / alternatives considered

- Повторный = «есть другой WON с тем же контактом» без сравнения дат: первая покупка тоже стала бы повторной, доля удвоилась бы. Отвергнуто решением.
- Метка по клиентам mefi (`client_snapshots`): у клиента нет ссылки на лид, контакты клиента мы не храним. Отвергнуто.
- Отдельный helper whitelisting для лидов и клиентов: пока два вызова одной `keep_known_nested_keys` плюс фильтр ключей, правило трёх не сработало.

## Open questions

1. Лист «Lead-uri luna» содержит лиды, созданные в месяце, а клиент месяца определяется по `converted_at`: клиент, чей лид создан в прошлом месяце, в листе отсутствует, и число DA в Excel будет меньше, чем в m11. Варианты: (а) колонка «Revine» только у строк листа, которые клиенты месяца, пусто у остальных, расхождение записать сноской в шапке листа; (б) отдельный лист «Clienți luna» с клиентами месяца и колонкой «Revine», число строк = «din N» m11. Рекомендую (б).

## Ответы (28.09.2026)

1. Вариант (б): отдельный лист «Clienți luna».
