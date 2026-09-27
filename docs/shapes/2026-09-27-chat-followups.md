# Shape: уточняющие вопросы в чате (27.09.2026)

## Goal

После «câte lead-uri am avut săptămâna aceasta în București?» вопрос «Dar Cluj?» получает ответ по Cluj за ту же неделю, а не просьбу уточнить.

## Approach

Перед вызовом модели обработчик ищет в `chat_questions` предыдущий обмен и передаёт модели его вопрос и успешные вызовы инструментов с аргументами (JSON, как их прислала модель) отдельным текстовым блоком в начале того же сообщения пользователя, перед текущим вопросом. Текст прошлого ответа и результаты инструментов не передаются: модель вызывает инструмент заново, страж цифр по-прежнему сверяет ответ только с результатами текущих вызовов, вопросом и подписью. Цепочка «Dar Cluj?» → «Dar Brașov?» работает без истории глубже одного обмена: аргументы прошлого вызова уже несут и период, и шоурум.

## Решения

- **Поиск контекста** (`chat/log.py` `previous_exchange`):
  1. Сообщение это reply на сообщение бота: строка того же чата с `reply_message_id` = id сообщения, на которое ответили, и `status = answered`, без ограничения по времени и по пользователю. Не нашлась (reply на отчёт, на отказ): переход к п. 2.
  2. Последняя строка того же `chat_id` и `user_id` со `status = answered` и `created_at >= now() − context_minutes`. Время считает Postgres (`now()`), как пишет `created_at`: часы процесса и БД не смешиваются.
- `context_minutes: 10` в `modules.yaml` `chat` (`ChatSettings`), не константа в коде (CLAUDE.md, пороги не хардкодить).
- **Какие вызовы в контексте**: только успешные (`ok: true`), не больше трёх (лимит цикла). У строки `answered` без успешных вызовов контекста нет.
- **Текст контекста** (RO) в `templates/chat_context.j2`: «Întrebarea anterioară: «…». Pentru ea s-au apelat: funnel {"period": "saptamana_curenta", "showroom": "București"}. Dacă întrebarea de mai jos o continuă, apelezi din nou instrumentul și schimbi doar ce cere întrebarea nouă.» Системный промпт не меняется, его кэш-префикс тоже.
- **`reply_message_id`**: id сообщения бота, которым он ответил (`message.reply` возвращает `Message`), пишется для всех статусов, включая `rate_limited` и `api_error`.
- **`context_question_id`**: id строки, взятой как контекст, или NULL; также в лог-строке «chat question answered».

## Files

- `alembic/versions/0004_chat_followups.py`, `src/digest/db/schema.py`: две колонки.
- `src/digest/chat/log.py`: `PreviousExchange`, `previous_exchange(...)`, `record_question(..., reply_message_id, context_question_id)`.
- `src/digest/chat/loop.py`: `answer_question(..., previous: PreviousExchange | None)`, блок контекста в первом сообщении.
- `src/digest/bot/chat_handlers.py`: поиск контекста, запись id ответа и контекста.
- `src/digest/config.py`, `config/modules.yaml`: `context_minutes`.
- `templates/chat_context.j2`; `docs/kpi-definitions.md` («Режим вопросов»), `docs/deploy.md` §4, `docs/PLAN.md`.

## Schema / API changes

`0004`: `chat_questions.reply_message_id bigint NULL`, `chat_questions.context_question_id bigint NULL REFERENCES chat_questions(id)`. Индекса нет: строк десятки в день, поиск идёт по существующему `(tenant_id, chat_id, created_at)`. Старые строки получают NULL; reply на ответ, отправленный до миграции, контекста не даёт (переход к правилу 10 минут).

## Test plan

Unit `tests/unit/test_chat_loop.py` (шов `answer_question` с фейковым клиентом):
- `test_previous_exchange_precedes_question_in_one_user_turn`: первый запрос содержит блок с прошлым вопросом и `funnel {…}`, затем текущий вопрос; текста прошлого ответа нет.
- `test_number_from_previous_answer_is_blocked`: контекст про București (7 лидов в прошлом ответе), текущий вызов даёт 3, модель пишет 7 → `unverified_numbers`.

Integration `tests/integration/test_chat_bot.py` (шов: апдейт в диспетчер → запросы к фейковому API и строки `chat_questions`):
- `test_follow_up_within_ten_minutes_reuses_period_and_changes_showroom`: вопрос про București, `created_at` сдвинут на −2 минуты, «Dar Cluj?»; в запросе контекст с `saptamana_curenta` и București, скрипт вызывает `funnel(showroom=Cluj, saptamana_curenta)`, ответ `answered`, у второй строки `context_question_id` = id первой.
- `test_follow_up_after_eleven_minutes_has_no_context`.
- `test_question_of_another_user_is_not_context`.
- `test_reply_to_bot_answer_uses_that_question_regardless_of_age`: сдвиг −3 часа, reply на `reply_message_id` первой строки.
- `test_reply_to_bot_report_falls_back_to_recent_question`.
- `test_reply_message_id_is_logged`.
- Миграция 0004 с нуля (существующий тест upgrade).

## Tradeoffs / alternatives considered

- **Полная история чата сообщениями user/assistant.** Естественнее для модели, но прошлый ответ с цифрами попал бы в контекст: модель переписывала бы их, страж блокировал бы, а без стража вернулась бы цифра из другого снапшота. Решение пользователя: только вопрос и вызовы.
- **Контекст в системном промпте.** Меняет system на каждый вопрос; блок в сообщении пользователя оставляет system стабильным.
- **Время по часам процесса (`clock`) с явным `created_at` при записи.** Тестируемо без сдвига строк, но меняет запись всех 11 вызовов `record_question` и смешивает часы там, где `created_at` уже пишет Postgres. Сдвиг `created_at` в тестах уже используется для дневного лимита.

## Open questions

Нет. Выбраны мной: reply на сообщение бота без строки `answered` переходит к правилу 10 минут; контекст только из успешных вызовов; окно в конфиге.
