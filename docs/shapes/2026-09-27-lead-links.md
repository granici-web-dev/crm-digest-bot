# Shape: ссылки на лиды mefi в d2, d3, d4 и в ответах чата (27.09.2026)

## Goal

Под строкой консультанта в d2 (neatinse), d3 (reveniri restante), d4 (oferte blocate) и под ответом чата на `overdue_followups` и `untouched_leads` стоят кликабельные `#1234`, ведущие на карточку лида в mefi: самые старые первыми, не больше 10, остальное «și încă N».

## Approach

id лидов приходят из `metrics/` в уже упорядоченном виде (старые первыми), ссылки собирает один класс `LeadLinks` из `MEFI_BASE_URL` и шаблона пути в конфиге. Отчёты и чат получают готовую HTML-строку ссылок. Модель id не видит: инструмент кладёт их в отдельное поле `ToolOutcome`, не в `content`, а цикл дописывает строку ссылок после стража цифр, поэтому страж её не проверяет по построению.

## Решения

**Инвариант 7** в CLAUDE.md, новая редакция: «В группу — только агрегаты и имена сотрудников. id лидов допускаются только как ссылки в mefi вида `#1234`, не больше 10 на блок или ответ, без имён и контактов; остальное числом «și încă N». Телефоны, e-mail, тексты заметок клиентов не выводятся ни в отчётах, ни в ответах чата.» Про Excel-вложения см. вопрос 2.

**URL.** `web origin` = схема и хост `MEFI_BASE_URL` (`https://bellesofa.meficrm.com/api/v1` → `https://bellesofa.meficrm.com`), путь целиком отбрасывается, а не срезается строкой `/api/v1`. Шаблон пути и лимит в `config/status-mapping.yaml` (там уже живут факты mefi: поля, шоурумы):
```yaml
lead_links:
  path: "/admin/leads/index/{lead_id}"   # валидатор: ровно один {lead_id}, начинается с /
  limit: 10
```
Разметка: `<a href="https://bellesofa.meficrm.com/admin/leads/index/1234">#1234</a>`, через запятую; хвост ` și încă N`. В тексте ссылки только `#` и номер.

**Превью ссылок выключены** для всех сообщений бота отчётов: `DefaultBotProperties(link_preview_is_disabled=True)` в `create_bot`. Иначе Telegram подтянет превью страницы входа mefi под каждый отчёт.

**metrics/** (порядок «старые первыми» это часть метрики, не шаблона; при равенстве по `lead_id`):
- d2 `untouched_leads`: `UntouchedLeads.lead_ids` и `UntouchedGroup.lead_ids` по `created_at`.
- d3 `overdue_revenire_by_manager`: `OverdueRevenire.lead_ids` и `OverdueGroup.lead_ids` по `data_revenire` (самая просроченная первой).
- d4 `stale_offers`: `StaleOffers.lead_ids_by_showroom` по `last_contact_at`, из тех же флагов `active_offers_14`, что счётчик. d4 группирует по шоуруму, а не по консультанту (вопрос 3).
- Метрики отдают все id, обрезка до 10 это представление (`LeadLinks`).

**Вид d2 и d3** меняется с одной строки на заголовок и строку на группу, ссылки в конце строки группы (вопрос 1 про лимит):
```
⚠ Lead-uri neatinse sau nepreluate: 14 (cel mai vechi: 30h)
Dragoi Mihaela 3 (30h): #101, #102, #103
Nepreluate 11 (12h): #1, #2, #3, #4, #5, #6, #7 și încă 4
```
d4 так же по строке на шоурум. Строка «...: nu» без изменений.

**Чат.** `ToolOutcome.lead_ids: tuple[int, ...] = ()`, заполняют только `overdue_followups` и `untouched_leads` (при фильтре по консультанту только его лиды, в том же порядке). `content` не меняется. Цикл: страж проверяет текст модели как сейчас; к ответу после подписи добавляется строка `Lead-uri: #…, #… și încă N` из id всех успешных вызовов по порядку без повторов, не больше 10 на ответ. В `templates/chat_system.md` одна строка: «Linkurile către lead-uri se adaugă automat sub răspuns; nu le scrie și nu inventa numere de lead.»

**Доставка.** `ReportDeps` и `ReportContext` получают `lead_links: LeadLinks` (строится в `create_report_deps` из `Settings.mefi_base_url` и конфига); `ChatDeps` получает его же. Длина: строка из 10 ссылок около 800 символов, `split_message` режет по строкам, строка ссылок меньше лимита 4096.

## Files

- `CLAUDE.md`: инвариант 7.
- `config/status-mapping.yaml`, `src/digest/config.py`: `lead_links` (path, limit) с валидатором.
- `src/digest/reports/lead_links.py`: `LeadLinks` (`url(lead_id)`, `html(lead_ids)`), `web_origin(mefi_base_url)`.
- `src/digest/delivery/telegram.py`: превью выключены.
- `src/digest/metrics/daily_checks.py`: поля `lead_ids`, упорядочение.
- `src/digest/reports/context.py`, `runner.py`, `src/digest/app.py`: `lead_links` в зависимостях.
- `src/digest/reports/modules/daily_checks.py`, `templates/untouched_leads.j2`, `overdue_revenire.j2`, `stale_offers.j2`.
- `src/digest/chat/tools.py`, `src/digest/chat/loop.py`, `src/digest/bot/chat_handlers.py`, `templates/chat_system.md`.
- `docs/kpi-definitions.md` (порядок id в d2–d4, без изменения формул), `docs/PLAN.md`.

## Schema / API changes

Схема БД не меняется. Новый ключ конфига `lead_links`. `chat_questions.answer` будет содержать строку ссылок (это текст, ушедший в чат).

## Test plan

Unit, `tests/unit/test_lead_links.py` (шов `LeadLinks`):
- `test_url_is_web_origin_of_api_base_plus_configured_path`: `https://x.meficrm.com/api/v1` + `/admin/leads/index/{lead_id}` → `https://x.meficrm.com/admin/leads/index/1234`.
- `test_link_text_is_only_hash_and_number`: текст каждого `<a>` матчится `^#\d+$`.
- `test_more_than_limit_ids_end_with_si_inca_n`: 13 id → 10 ссылок и ` și încă 3`; ровно 10 → без хвоста; пусто → пустая строка.
- `test_path_without_lead_id_placeholder_fails_config_load` (в `test_config.py`).

Unit, `tests/unit/test_metrics_daily_checks.py`: `test_untouched_lead_ids_oldest_first`, `test_overdue_lead_ids_most_overdue_first`, `test_stale_offer_lead_ids_by_showroom_oldest_contact_first`; число id в группе равно её `lead_count`.

Рендер, syrupy для d2, d3, d4 с ссылками и хвостом; `test_templates_contain_no_cyrillic` как есть.

Unit, `tests/unit/test_chat_tools.py` и `test_chat_loop.py`:
- `test_tool_results_contain_no_lead_ids_or_client_data` остаётся зелёным (id не в `content`), плюс `test_overdue_and_untouched_carry_lead_ids_outside_content`: `outcome.lead_ids` непуст и равен порядку метрики.
- `test_links_line_follows_signature_and_is_capped_at_ten`.
- `test_number_guard_ignores_lead_numbers_in_links_line`: ответ модели без чисел не из результатов проходит, хотя строка ссылок полна номеров; ответ модели с `#1234`, которого нет в результатах, блокируется.
- `test_model_request_never_contains_lead_ids`: во всех записанных запросах к API нет ни одного id из кадра.

Integration: `test_bot_sends_messages_without_link_previews` (фейковая сессия видит `link_preview_options.is_disabled`); прогон d1..d6 как есть с новыми снапшотами.

## Tradeoffs / alternatives considered

- **Ссылки пишет модель по id из результата.** Отвергнуто условием задачи: модель видела бы id и могла бы их выдумать или переставить; страж пришлось бы учить отличать номер лида от цифры.
- **Срезать `/api/v1` строкой.** Хрупко при другом тенанте или версии API; origin из `urlsplit` не зависит от пути.
- **Обрезка до 10 в metrics/.** Метрика перестала бы быть полной, а лимит это решение представления из конфига.
- **Оставить d2/d3 одной строкой и ссылки в конце.** 10 ссылок разных консультантов в одной строке нельзя отнести к человеку без чтения URL.

## Ответы пользователя (27.09.2026)

1. Лимит 10 на весь блок: самые старые по всему блоку, каждая строка группы показывает свои из этих 10 и «și încă N» за свои остальные; строка группы без ссылок в десятке остаётся без ссылок.
2. В инвариант 7 исключение: «во вложениях Excel столбец id лида разрешён, без контактов клиента». В этом же craft отдельным коммитом столбец id в w12 и m19 становится гиперссылкой в mefi через тот же `LeadLinks`.
3. d4 по шоуруму, как сейчас.

Коммиты craft: fix(chat) подмена снапшота (отдельная правка чата); shape; `feat(reports)` ссылки в d2, d3, d4 и в ответах чата, превью выключены; `feat(reports)` столбец id гиперссылкой в Excel w12 и m19. В конце PLAN и инвариант 7 в CLAUDE.md.

## Open questions (закрыты, см. ответы)

1. **Лимит 10: на блок или на группу?** Буквально «до 10 на блок»: на весь d2 не больше 10 ссылок, самые старые по всему блоку, каждая группа показывает свои из этих 10 и «și încă N» за остальные. Рекомендую так: сообщение ограничено (~800 символов на блок), совпадает с формулировкой инварианта. Альтернатива «до 10 на строку консультанта» полезнее каждому консультанту, но d3 при шести консультантах даёт до 60 ссылок и отчёт на несколько сообщений.
2. **Excel-вложения w12 и m19 уже содержат столбец `lead_id`** (`LEAD_ROW_COLUMNS`), и новая формулировка «id только как ссылки» им противоречит. Рекомендую дописать в инвариант: «во вложениях Excel столбец id лида разрешён, без контактов клиента». Либо превратить столбец в гиперссылки, это отдельная фича.
3. **d4 группирует по шоуруму, не по консультанту.** Рекомендую строку ссылок на шоурум, как сейчас устроен блок. Разрез по консультанту это изменение метрики d4, отдельное решение.
