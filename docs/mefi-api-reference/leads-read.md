# MEFI API — Lead Reading (Primary endpoint for our project)

> **This is the core endpoint for our analytics platform.**
>
> All MVP1 functionality is built on these three endpoints.

## Authentication

```http
Authorization: Bearer <read_key>
```

Read keys have prefix `lrd_*` and are generated in MEFI: `Panou de control → API → Citire clienți potențiali`.

**Scope required:** `leads:read`

---

## 1. POST `/api/v1/leads/search` — Search/list leads

Paginated search and filtering. All parameters optional — empty body returns active leads (page 1, 20 results).

### Endpoint

```http
POST https://bellesofa.meficrm.com/api/v1/leads/search
Authorization: Bearer lrd_XXXX_...
Content-Type: application/json
```

### Request body parameters

| Param | Type | Description |
|---|---|---|
| `query` | string | Search in `name`, `email`, `company`, `phone` (LIKE %val%) |
| `filters.status` | int[] | Filter by status IDs (match `status.id` in response) |
| `filters.source` | int[] | Filter by source IDs (match `source.id` in response) |
| `filters.priority` | string[] | Priority filter. Values: `"low"`, `"medium"`, `"high"` |
| `filters.assigned_to` | int[] | Filter by assigned user IDs |
| `filters.client_type` | string | `"company"` or `"individual"` |
| `filters.lifecycle` | string[] | Lead lifecycle: `"active"`, `"lost"`, `"junk"`. Can combine. Default: `["active"]` |
| `filters.location` | object | `{ city: "...", county: "..." }` — exact match |
| `filters.date_from` / `date_to` | YYYY-MM-DD | Date range |
| `filters.date_field` | string | `"created_at"` \| `"last_contact_at"` \| `"status_changed_at"` |
| `filters.exclude_duplicates` | bool | `true` = exclude leads marked as duplicate |
| `page` | int | Default: `1` |
| `per_page` | int | Default: `20`. Max: `100` |
| `sort` | string | `"created_at"` \| `"last_contact_at"` \| `"name"` \| `"estimated_value"` \| `"status"` |
| `order` | string | `"asc"` \| `"desc"` |

### Example request

```json
{
  "query": "ion",
  "filters": {
    "status": [39, 5],
    "priority": ["high"],
    "client_type": "company",
    "lifecycle": ["active"],
    "location": { "city": "Bacau", "county": "Bacau" },
    "date_from": "2026-01-01",
    "date_field": "created_at"
  },
  "page": 1,
  "per_page": 20,
  "sort": "created_at",
  "order": "desc"
}
```

### Example cURL

```bash
curl -X POST "https://bellesofa.meficrm.com/api/v1/leads/search" \
  -H "Authorization: Bearer lrd_XXXX_..." \
  -H "Content-Type: application/json" \
  -d '{"filters":{"lifecycle":["active"]},"sort":"created_at","order":"desc"}'
```

### Example response

> ⚠️ **The example below is ABRIDGED — it does not list every field the
> endpoint returns.** It was copied from MEFI's PDF and shows a subset.
>
> Reading it as the full field list produces a wrong conclusion. It omits
> `estimated_value`, and the omission was taken (2026-08-25) as evidence
> that search does not return the deal value and that a `GET /leads/{id}`
> call would be needed to obtain it. Verified against the live API: it is
> returned by search. See "Fields actually returned" below for the measured
> list.

```json
{
  "success": true,
  "request_id": "abc123",
  "data": [
    {
      "id": 42,
      "client_type": "company",
      "name": "Ion Popescu",
      "phone": "+40700000000",
      "status": { "id": 39, "name": "Calificare" },
      "source": { "id": 1, "name": "Google" },
      "priority": { "slug": "high", "label": "Ridicată" },
      "lifecycle": "active",
      "assigned_to": { "id": 5, "name": "Popescu Ion" },
      "location": {
        "city": "Bacau",
        "county": "Bacau",
        "country": { "code": "RO", "name": "Romania" }
      },
      "created_at": "2026-04-23T06:19:45Z",
      "custom_fields": [...]
    }
  ],
  "meta": {
    "page": 1,
    "per_page": 20,
    "total": 543,
    "total_pages": 28
  }
}
```

### Fields actually returned by `/leads/search`

Measured 2026-08-25 against `bellesofa.meficrm.com` with a live `lrd_*` key,
over a sample of 20 leads with `status_id=1` (contract). 26 keys per record:

```
assigned_to, business, client_type, converted_at, created_at, created_by,
custom_fields, description, email, estimated_value, gclid, id, identity,
is_duplicate, is_public, last_contact_at, lifecycle, location, name, phone,
priority, source, status, status_changed_at, title, website
```

**`estimated_value` IS among them.** Search and the detail endpoint differ by
exactly one key: `company`, present only on `GET /leads/{id}` (and only for
`client_type: "company"`).

Practical consequence: **there is nothing to gain, for any metric we compute,
by adding a per-lead detail fetch.** It costs one request per lead against a
rate limit of ~1 req/s (see README) and returns one extra field we do not use.

### Note on `estimated_value` — the field is returned, but it is empty

Same measurement: `estimated_value` was `null` for **20 of 20 closed
contracts**. Per the field description below, MEFI returns null when the value
is zero or was never filled in — so this is a data-entry fact about Sofa Belle,
not an API limitation and not a bug in our extraction.

The same run enumerated every custom field present on those leads: 11 fields,
all of them already documented in [custom-fields.md](./custom-fields.md), none
of type `number`, none holding a monetary value. The deal amount is not hiding
under another key either.

Consequence: revenue, average deal size, CAC and ROAS cannot be derived from
MEFI at all. The monthly spreadsheet is not a secondary source for them — it is
the only one. See
[`.planning/phases/03-metrics-engine/03-MARKETING-SHEET-INVESTIGATION.md`](../../../.planning/phases/03-metrics-engine/03-MARKETING-SHEET-INVESTIGATION.md).

---

## 2. GET `/api/v1/leads/{id}` — Full lead profile

Returns full lead profile + all active custom fields. Returns 404 if lead not found.

### Endpoint

```http
GET https://bellesofa.meficrm.com/api/v1/leads/{id}
Authorization: Bearer lrd_XXXX_...
```

### Example cURL

```bash
curl -X GET "https://bellesofa.meficrm.com/api/v1/leads/42" \
  -H "Authorization: Bearer lrd_XXXX_..."
```

### Response fields

| Field | Description |
|---|---|
| `id`, `client_type`, `name`, `title` | `id`: int; `client_type`: `"company"` \| `"individual"` (never null); `name`: string (never null, min `""`); `title`: string \| null |
| `phone`, `email`, `website` | string \| null. `website` is null even if saved as `-` |
| `company` | string \| null. **Key absent entirely** for `client_type: "individual"` (not even as null) |
| `identity` | `{ card_number: string\|null, personal_id: string\|null }` for `"individual"`; null for `"company"`. Both keys (`identity` + `business`) are always present in response |
| `business` | `{ tax_id: string\|null, registration_number: string\|null }` for `"company"`; null for `"individual"` |
| `gclid`, `description` | string \| null. `description`: HTML stripped, entities decoded |
| `location` | **Always present** (never null). `{ address_line, city, county, postal_code, country: {code, name}\|null, coordinates: {lat, lng}\|null }`. Fields inside can be null |
| `status` | `{ id: int, name: string }` \| null — current CRM status |
| `source` | `{ id: int, name: string }` \| null — lead source |
| `estimated_value` | `{ amount: float, currency: string\|null }` \| null. **Null if value is 0 or not filled.** `currency` is null if not configured |
| `priority` | `{ slug: "low"\|"medium"\|"high", label: "Scăzută"\|"Medie"\|"Ridicată" }` \| null |
| `is_public` | bool — visible to all staff users |
| `assigned_to` | `{ id: int, name: string }` \| null — responsible user |
| `created_by` | `{ id: int, name: string }` \| null — user who created lead |
| `lifecycle` | `"active"` \| `"lost"` \| `"junk"` (never null) |
| `is_duplicate` | bool |
| `last_contact_at`, `created_at`, `status_changed_at`, `converted_at` | ISO 8601 UTC \| null (e.g., `"2026-04-23T06:19:45Z"`) |
| `custom_fields[]` | `{ field_id: int, name: string, type: string, value: mixed }` |

### Custom field value types

| Custom field type | Value type |
|---|---|
| `input` \| `select` \| `colorpicker` \| `date_picker` | string \| null |
| `number` | float \| null |
| `textarea` | string \| null (HTML stripped) |
| `multiselect` \| `checkbox` | string[] \| null |
| `date_picker_time` | ISO 8601 UTC string \| null |
| `link` | `{ url, label }` \| null |

---

## 3. GET `/api/v1/leads/{id}/contacts` — Lead's contacts

Returns list of lead's contacts + their active custom fields. Returns 404 if lead not found.

### Endpoint

```http
GET https://bellesofa.meficrm.com/api/v1/leads/{id}/contacts
Authorization: Bearer lrd_XXXX_...
```

### Example cURL

```bash
curl -X GET "https://bellesofa.meficrm.com/api/v1/leads/42/contacts" \
  -H "Authorization: Bearer lrd_XXXX_..."
```

### Response fields

| Field | Description |
|---|---|
| `id`, `lead_id` | `id`: int — contact ID; `lead_id`: int — parent lead ID |
| `is_primary` | bool — primary contact |
| `lastname`, `firstname`, `title` | string \| null — last name, first name, position |
| `email`, `phone` | string \| null — contact email/phone |
| `is_active` | bool — contact is active |
| `created_at` | ISO 8601 UTC \| null |
| `custom_fields[]` | `{ field_id, name, type, value }` — same structure as lead custom fields |

---

## Rate limits

| Type | Per minute | Burst (10s) |
|---|---|---|
| IP (global, excl. `/health`) | 60 | 10 |
| Per `leads:read` token | **600** | **100** |

Tracked via response headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

On exceeded → `429` with `Retry-After`.

## Response codes

| Code | Description |
|---|---|
| 200 | Success |
| 400 | Invalid JSON or invalid parameters |
| 401 | Token missing/invalid/revoked |
| 403 | Token missing `leads:read` permission |
| 404 | Lead or contacts not found |
| 422 | Invalid filter value |
| 429 | Rate limit exceeded |
| 500 | Server error |

---

## Implementation guidance for our project

### Sync strategy

**Initial sync (one-time):**
1. Call `/leads/search` with `lifecycle: ["active", "lost", "junk"]` and `date_from` = 12 months ago
2. Paginate through results (per_page=100)
3. For each lead, call `/leads/{id}` to get full profile with custom_fields
4. Optionally call `/leads/{id}/contacts` if needed

**Incremental sync (nightly):**
1. `/leads/search` with `date_field: "status_changed_at"`, `date_from` = `last_sync_at`
2. Paginate, fetch profiles for changed leads only
3. UPSERT by `external_id` (= MEFI `id`)

### Pagination

```python
async def fetch_all_leads(client, since: datetime):
    page = 1
    while True:
        response = await client.post("/leads/search", json={
            "filters": {
                "lifecycle": ["active", "lost", "junk"],
                "date_from": since.strftime("%Y-%m-%d"),
                "date_field": "status_changed_at",
            },
            "page": page,
            "per_page": 100,
            "sort": "status_changed_at",
            "order": "asc",
        })
        data = response.json()
        for lead in data["data"]:
            yield lead
        if page >= data["meta"]["total_pages"]:
            break
        page += 1
```

### Rate limit handling

With 600 req/min limit:
- Conservative: sleep 0.1s between requests (600/min max)
- Smart: read `X-RateLimit-Remaining` header, slow down when below 50
- On 429: respect `Retry-After` header, exponential backoff via Celery

### What to store in our DB

For each lead from `/leads/{id}` response, store:
- All standard fields → `mefi_leads` table columns
- `custom_fields[]` → JSONB column for flexibility + extract critical ones (Showroom from `form-cf-14`, UTM from `form-cf-38..41`) into dedicated columns for indexing
- Original `raw_payload` → JSONB for debugging and future field additions

### Idempotency

UPSERT by `(tenant_id, external_id)` where `external_id = lead["id"]`.

```sql
INSERT INTO mefi_leads (tenant_id, external_id, ...)
VALUES (...)
ON CONFLICT (tenant_id, external_id)
DO UPDATE SET
    status = EXCLUDED.status,
    last_contact_at = EXCLUDED.last_contact_at,
    raw_payload = EXCLUDED.raw_payload,
    synced_at = NOW();
```
