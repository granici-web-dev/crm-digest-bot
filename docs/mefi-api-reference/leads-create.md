# MEFI API — Lead Creation

> `POST /api/v1/leads`
>
> **Note:** Our analytics project does NOT use this endpoint. It's documented here for reference only. Lead creation is handled by Sofa Belle's website forms, Make.com, Facebook Lead Ads, etc.

## Endpoint

```http
POST https://bellesofa.meficrm.com/api/v1/leads
Authorization: Bearer <write_key>
Content-Type: application/json
```

**Scope required:** `leads:create`

## Standard fields

| Field | Type | Description |
|---|---|---|
| `name` * | string | Full name (required) |
| `email` | string | Valid email |
| `phonenumber` | string | Phone |
| `company` | string | Company name |
| `title` | string | Position |
| `address` | string | Address |
| `city` | string | City |
| `state` | string | County (județ) |
| `country` | string | Country |
| `zip` | string | Postal code |
| `website` | string | URL |
| `description` | string | Notes |
| `coordonate` | string | GPS (e.g., "44.42,26.10") |
| `cui` | string | Tax ID (CUI) |
| `pj_ci` | string | Trade Registry No. / ID card |
| `pclienttype` | int | 0=Legal entity (PJ), 1=Individual (PF) |
| `assigned` | int | User ID. If >0, overrides Round-Robin and default assignee |

## Source values (`source`, int)

Mapped to integer IDs:

| ID | Name |
|---|---|
| 1 | Google |
| 2 | Meta ADS |
| 3 | Recomandare |
| 4 | Teren |
| 5 | Showroom |
| 6 | Site |
| 7 | Arhitect |
| 9 | WhatsApp |
| 10 | Telefon |
| 11 | Mail |
| 12 | Colaborare |
| 13 | Client Fidel |

## Custom fields prefix

```
form-cf-{id}
```

See [custom-fields.md](./custom-fields.md) for full list.

## Example payloads

### Minimal

```json
{ "name": "Ion Popescu" }
```

### Complete

```json
{
  "name": "Ion Popescu",
  "email": "ion@example.com",
  "phonenumber": "0722 123 456",
  "company": "Firma SRL",
  "cui": "RO12345678",
  "pclienttype": 0,
  "assigned": 1
}
```

## Example cURL

```bash
curl -X POST "https://bellesofa.meficrm.com/api/v1/leads" \
  -H "Authorization: Bearer lapi_XXXX_..." \
  -H "Content-Type: application/json" \
  -d '{"name":"Ion Popescu","email":"ion@example.com"}'
```

## Response codes

| Code | Status | Description |
|---|---|---|
| 201 | `created` | Lead created. Includes `lead_id` in response. |
| 200 | `duplicate_blocked` | Duplicate blocked. |
| 200 | `versioned` | Duplicate versioned. |
| 400 | — | Invalid JSON / empty / non-HTTPS |
| 401 | — | Token missing/invalid/revoked |
| 403 | — | Missing `leads:create` permission |
| 422 | — | Validation failed. Details in `errors`. |
| 429 | — | Rate limit: 300 req/min |
| 500 | — | Server error |
