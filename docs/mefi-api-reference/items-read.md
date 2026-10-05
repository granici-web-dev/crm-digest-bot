# MEFI API — Citire produse și servicii (`items:read`)

> Источник: карточка «Citire produse și servicii» на странице API mefi, текст вендора от 05.10.2026 (пересказ). Живыми запросами не проверено: на аккаунте выключена опция «Cod de produs», запросы к данным дают 403. Ключ: `MEFI_ITEMS_API_KEY` (создан 05.10.2026, заработает после включения опции без перевыпуска).

## Статус для бота

Не используется. Номенклатура без продаж отчётам не нужна. Пригодится в двух случаях: (1) строки проформ или оферт со ссылкой на продукт — «топ продуктов» (m16); (2) будущий агент генерации оферт — названия, цены, категории, картинки. Включение опции «Cod de produs» — решение владельца и администратора mefi (меняет работу с номенклатурой), бот его не требует.

## Эндпоинты

| Метод | Путь | Что отдаёт |
|---|---|---|
| GET | `/api/v1/items/health` | статус и `data.timestamp`; без ключа и без лимита (ключ не проверяет) |
| GET | `/api/v1/items?code=…` | один продукт по коду, с `ETag` |
| POST | `/api/v1/items/search` | поиск с фильтрами прямо в корне тела: `codes, search, group_id, type, price_from, price_to, has_code, updated_since, page, per_page (1–100, по умолчанию 25), sort (code\|name\|price\|internal_id\|updated_at), order, after` |

Пока опция выключена: 403 «API-ul de produse și servicii nu este disponibil…». Неизвестный ключ тела → 422. Только HTTPS.

## Поля продукта

`code` (идентичность; "" у продуктов без кода, не уникален), `internal_id`, `name`, `description`, `type` (item \| service \| advance), `unit{label,code}`, `group{id,name}` \| null, `taxes[{id,name,rate}]`, `price` (в базовой валюте), `image_url`, `custom_fields[{field_id,name,type,value}]`, `flags{has_code,duplicate_code}`, `updated_at`.

Нет: остатков и складов, поставщиков, себестоимости и маржи, цен в других валютах.

## Значения аккаунта Sofabelle (по документации, 05.10.2026)

- Номенклатура: 183 позиции, 17 категорий (среди них Banchete 14, Canapea Belle Trend 19, Canapea Cloud 17, Canapea Freedom 10, Canapea Siena 20, Canapele 4, Custom 13, Fotoliu 7).
- Кастомное поле продукта: `field_id 11` «Model» (input).
- Ставки TVA: id 1 (21 %), 7 (19 %), 2 (11 %), 8 (9 %) и др.; у продукта 0–2 ставки.

## Синхронизация и лимиты

- Инкрементально: `updated_since` + `sort=updated_at`, `order=asc`, курсор `after = meta.next_after`; перекрытие 65 минут; удаления не объявляются, нужна полная сверка. Смена ставки TVA в настройках `updated_at` не двигает.
- Лимиты отдельные от других API: IP 60/мин (без отдельного лимита на 10 с), ключ 600/мин, неверный ключ 30/мин. 429 с `Retry-After`.
- Аудит: одна строка на запрос (ключ, IP, путь, код), содержимое не пишется; хранится 90 дней.
