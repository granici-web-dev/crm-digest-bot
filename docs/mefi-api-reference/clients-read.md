# MEFI API — Citire clienți finali (`clients:read`)

> Источник: страница «API» в интерфейсе mefi, карточка «Citire clienți finali», скопирована 28.09.2026. Текст вендора без изменений по сути, форматирование приведено к markdown. Проверка живыми запросами: `docs/mefi-clients-notes.md` (после разведки).

Documentatia pentru cele 3 endpoint-uri de citire. Toate necesita autentificare Bearer cu o cheie `clients:read`.

## Scopes disponibile

| Scope | Permisiune |
|---|---|
| `clients:create` | Creare clienti (POST /api/v1/clients) |
| `clients:update` | Actualizare clienti (PATCH /api/v1/clients/{id}) |
| `clients:read` | Citire clienti: search, profil, contacte |

Бот использует только `clients:read`.

## POST /api/v1/clients/search

Cautare si filtrare paginata. Body gol returneaza primii 20 clienti din toate starile (active, lost, junk, inactive). Foloseste `filters.state` pentru restrangere.

```json
{
  "query": "Firma SRL",
  "filters": {
    "client_type":     "company",
    "state":           ["active"],
    "status_ids":      [1, 2],
    "source_ids":      [3],
    "group_ids":       [5],
    "responsible_ids": [2],
    "date_from":       "2025-01-01",
    "date_to":         "2025-12-31",
    "date_field":      "created_at"
  },
  "page":     1,
  "per_page": 20,
  "sort":     "created_at",
  "order":    "desc"
}
```

- `query`: cauta in company, vat, j_ci
- `sort`: created_at | name | last_contact_at
- `date_field`: created_at | last_contact_at
- `client_type`: company | individual
- `state`: lista din active | lost | junk | inactive. Omis = toate starile
- `per_page`: max 100

Отличие от `/leads/search`: пустой body отдаёт все состояния, а не только active.

## GET /api/v1/clients/{id}

Profil complet al unui client final + campuri custom active. Returneaza 404 daca ID-ul nu exista. Returneaza si clientii lost/junk/inactive — starea efectiva e in campul `state`.

```
GET https://bellesofa.meficrm.com/api/v1/clients/{id}
Authorization: Bearer <cheie_api>
```

| Поле | Тип |
|---|---|
| `id` | int |
| `client_type` | "company" \| "individual" |
| `name` | string |
| `currency` | string \| null (ex: "EUR") |
| `status` | {id, name} \| null |
| `source` | {id, name} \| null |
| `groups` | [{id, name}] |
| `responsibles` | [{id, name}] — utilizatorii responsabili (customer admins) |
| `state` | "active" \| "lost" \| "junk" \| "inactive" |
| `last_status_change` | ISO 8601 UTC \| null |
| `elimination` | prezent doar cand state ∈ {lost, junk}: {type: "lost"\|"junk", reason: {id,name}\|null, detailed_reason: string\|null, marked_at: ISO 8601 UTC\|null, marked_by: {id,name}\|null} |
| `business` | {tax_id, registration_number} — doar PJ |
| `identity` | {card_number, personal_id} — doar PF |
| `banking` | {bank_name, iban, swift, vat_payer: {code,label}\|null} |
| `business_details` | {industry, caen_code, company_age, activity_status} — doar PJ |
| `billing`, `shipping` | {address, city, county, postal_code, country: {id,name}\|null} |
| `custom_fields[]` | {field_id, name, type, value} |
| `created_at`, `last_contact_at` | ISO 8601 UTC \| null |

В документации нет ни суммы договора, ни оплаченных счетов, ни ссылки на исходный лид.

### Campuri personalizate (custom_fields[])

| field_id | Тип | Имя | Значения |
|---|---|---|---|
| 12 | select | Modalitate contact | Mail, Telefon, Whatsapp, SmS |
| 15 | select | Showroom | Brașov, București, Cluj |
| 43 | date_picker | Data revenire | Y-m-d |
| 13 | textarea | Informatii | text simplu, fara HTML |
| 33 | select | Ofertat | ✅DA, ❌NU |
| 44 | textarea | Revenire 1 (Data+Info) | text simplu |
| 45 | textarea | Revenirea 2 (Data+info) | text simplu |
| 46 | textarea | Revenirea 3 (Data+info) | text simplu |
| 47 | input | UTM_Source | |
| 48 | input | UTM_Campanie | |
| 49 | input | UTM_Content | |
| 50 | input | UTM_Medium | |

Id полей клиента отличаются от id тех же полей у лида (Showroom: лид 14, клиент 15).

## GET /api/v1/clients/{id}/contacts

Lista contactelor active ale unui client, cu paginare. Returneaza 404 daca clientul nu exista.

```
GET https://bellesofa.meficrm.com/api/v1/clients/{id}/contacts?page=1&per_page=25
```

`page` default 1, `per_page` default 25, max 100. Campuri per contact: id, client_id, is_primary, firstname, lastname, email, phone, title, active, myaccount_enabled, notifications, custom_fields[], last_login_at, created_at. Raspunsul include `pagination: {page, per_page, total, total_pages}`.

Персональные данные: бот этот endpoint не использует для отчётов.

## GET /api/v1/clients/{id}/notes

Lista notitelor unui client, paginate si ordonate dupa data adaugarii (DESC). Returneaza 404 daca clientul nu exista.

```
GET https://bellesofa.meficrm.com/api/v1/clients/{id}/notes?page=1&per_page=25
```

Campuri per notita: id, client_id, description (text plain), description_html (HTML original), date_contacted (ISO 8601 | null), added_by: {id, name} | null, created_at. Raspunsul include `pagination`.

Тексты заметок о клиентах в отчёты не идут (инвариант 7).

## Rate limiting

| Tip | Limita / min |
|---|---|
| IP (global, excl. /health) | 60 cereri |
| Token clients:read | 600 cereri |
| Token clients:update | 300 cereri |
| Token clients:create | 300 cereri |

Headerele X-RateLimit-* reflecta limita token-ului. La depasire: 429 + header Retry-After.

IP-лимит общий с `leads:read`: пауза 1.2 с между любыми запросами процесса действует и здесь.
