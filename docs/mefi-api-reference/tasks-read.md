# MEFI API — Citire sarcini (`tasks:read`)

> Источник: карточка «Citire sarcini» на странице API mefi, текст вендора от 05.10.2026 (пересказ). Живыми запросами к данным не проверено (правило разрешённых вызовов не расширено). Ключ: `MEFI_TASKS_API_KEY` (создан 05.10.2026).

## Статус для бота

Не используется. Сколько задач в аккаунте и ведут ли их продавцы, неизвестно: нужна разведка одним `POST /tasks/search` (`per_page: 1` → `meta.total`). Смысл для проекта: (1) если продавцы ставят задачи на лиды, это второй след касаний помимо статусов Revenire; (2) будущий агент follow-up сможет ставить задачи через `tasks:create`, а чтение покажет, выполнена ли задача (`completed_at`, `completed_by`) — это журнал результата агента для сравнения с базовой линией.

## Эндпоинты

| Метод | Путь | Что отдаёт |
|---|---|---|
| GET | `/api/v1/tasks/health` | статус, без ключа (ключ не проверяет) |
| GET | `/api/v1/tasks/custom-fields` | определения кастомных полей задач (на аккаунте их нет) |
| POST | `/api/v1/tasks/search` | страница полных задач; фильтры в корне тела |
| GET | `/api/v1/tasks/{id}` | одна задача, с `ETag` |
| GET | `/api/v1/tasks/{id}/assignees`, `/followers`, `/checklist` | списки без пагинации |

`GET /api/v1/tasks` без id → 405. Только HTTPS.

## Поиск

Ключи тела: `page, per_page (1–100, по умолчанию 20), status[], priority[], assigned_to[], related_to{type,id}, start_from, start_to, due_from, due_to, updated_since, sort (id|start_at|due_at|created_at|updated_at), order`. Отличия от лидов: `null` на любом ключе → 422 (ключ надо опустить); пустой список → ноль результатов; даты в формате `YYYY-MM-DD HH:MM:SS` в поясе аккаунта, без `T` и `Z`. Без фильтра `status` в выдаче есть завершённые (5) и шаблоны (6). `updated_since` не видит изменений только в исполнителях, наблюдателях, чек-листе, метках, кастомных полях.

`related_to.type`: customer, lead, project, invoice, estimate, contract, ticket, expense, proposal, equipment, furnizor.

## Поля задачи

`id, name, description (HTML), status{id,name,color}, priority{id,slug,label}, start_at, due_at, related_to{type,id,name}, work_point, milestone, is_public, visible_to_client, show_on_calendar, billable, billed, invoice_id, hourly_rate, recurrence, recurring_from_task_id, created_by{id,name,type}, created_at, updated_at, completed_at, completed_by{id,name}, completion_coordinates, assignees[], followers[], tags[], checklist{total,finished}, logged_time{seconds,is_running}, custom_fields[]`.

ПД: `related_to.name` у типа `lead` и `customer` содержит имя клиента или фирмы и e-mail; `name` и `description` задачи — свободный текст. В отчёты и в raw не выводить и не хранить (инвариант 7); при использовании брать только id, статусы, даты и сотрудников.

## Значения аккаунта Sofabelle (по документации, 05.10.2026)

- Статусы: 1 Nu a început, 4 În lucru, 3 Verificare proprie, 2 Verificare suplimentară, 5 Completă, 6 Șablon.
- Приоритеты: 0 none, 1 low, 2 medium, 3 high, 4 urgent.
- Активный персонал: 11 человек; id 4 = Ciornii Maxim, владелец (в `config/managers.yaml` исправлено 05.10.2026).
- Кастомных полей у задач нет.

## Лимиты

IP 60/мин и 10 за 10 с (общий счётчик до аутентификации); ключ на чтение 600/мин и 100 за 10 с; неверный ключ 30/мин. 429 с `Retry-After`. Чтение ничего не пишет; ключ видит все задачи аккаунта независимо от прав пользователей.
