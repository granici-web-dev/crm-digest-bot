# MEFI API — Citire proforme (`estimates:read`)

> Источник: карточка «Citire proforme» на странице API mefi, текст вендора от 05.10.2026 (пересказ). Проверено 05.10.2026 запросами `GET /estimates/{id}` (разрешены правилом проекта). Ключ: `MEFI_ESTIMATES_API_KEY`, только `estimates:read`, без `estimates:read:links`.

## Статус для бота

Не используется: Sofabelle проформы не выставляет. Замер 05.10.2026: существуют только id 2–5 (id 1, 6–40 выборочно и 50…3000 выборочно → 404). Три черновика и одна принятая с счётом; даты 12.2025–03.2026; суммы 1 210–30 454 RON. Денег продаж здесь нет: они в офертах (proposals), договорах, счетах и оплатах, которых в API нет (письмо В3).

Ценность документа: это образец API, который нам нужен для оферт, договоров и счетов — суммы, статус, связь с лидом или клиентом, агент, строки, инкрементальная синхронизация. На него ссылаемся в письме в mefi.

## Эндпоинты

| Метод | Путь | Что отдаёт |
|---|---|---|
| GET | `/api/v1/estimates/health` | статус и `data.timestamp`, без ключа (ключ не проверяет) |
| GET | `/api/v1/estimates/{id}` | сводка и экспорт документа; параметры `profile` (minimal\|standard\|extended), `include`, `fields`; `ETag` |
| POST | `/api/v1/estimates/search` | поиск, фильтры в корне тела; опционально `export` для каждой проформы (тогда `per_page` ≤ 20) |

## Сводка (без ПД)

`id, number{text,prefix,sequence,formatted}, status{id,key,label}, issue_date, expiry_date, created_at, updated_at, sent_at, invoiced_at, customer{type: customer|lead, id, kind: pj|pf}, deleted_customer_name, company_id, currency{id,code}, totals{subtotal, discount_total, tax_total, adjustment, total, total_ron}, invoice_id, project_id, agent_id`.

Статусы: 1 draft, 2 sent, 3 declined, 4 accepted, 5 expired, 6 cancelled.

## Экспорт (с ПД)

`export.document` содержит имя клиента, организацию, CUI, адреса, строки `items[{line_no, code, description, long_description, qty, unit, rate, taxes}]`. Для бота допустимо только с `fields`, оставляющим строки и суммы (например `fields=id,items,grand_total`); имя, адреса и `deleted_customer_name` не читать и не хранить (инвариант 7). `hash` и `links.public_url` требуют `estimates:read:links` — не запрашивать: по публичной ссылке проформу можно принять или оплатить.

## Поиск

Ключи тела: `ids, id_after, status, customer{type,id,include_origin_lead}, issue_date{from,to}, expiry_date, created_at, number, company_id, reference, invoiced, project_id, agent_id, currency_id, updated_since, page, per_page (1–100, по умолчанию 25), sort (id|issue_date|expiry_date|created_at|updated_at), order, after, export`. Неизвестный ключ → 422. Полная синхронизация: `sort=id, order=asc, id_after=N`. Инкрементальная: `updated_since` с перекрытием 65 минут, курсор `after`. Удаления не объявляются.

## Значения аккаунта

- Фирм-эмитентов 10: 1 BELLE SOFA S.R.L., 2 S.C. STUDIO LUX S.R.L., остальные в списке карточки.
- Валюты: 1 USD, 2 EUR, 3 RON.
- Кастомных полей у проформ нет; перераспределение скидки выключено.

## Лимиты

IP 60/мин и 10 за 10 с; ключ 600/мин и 100 за 10 с; неверный ключ 30/мин. 429 с `Retry-After` (до 19 с). Счётчики отдельные от других API. Аудит: строка на запрос, без содержимого, 90 дней.
