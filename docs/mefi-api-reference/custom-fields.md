# MEFI Custom Fields for Sofa Belle

> Tenant-specific custom fields configured in Sofa Belle's MEFI account.
>
> These are accessed via `form-cf-{id}` keys both when writing leads (POST) and reading them (the field appears in `custom_fields[]` array).

## Custom fields catalog

| Field key | Type | Display name | Description | Use in our project |
|---|---|---|---|---|
| `form-cf-14` | select | Showroom | Which showroom the lead is associated with | **Critical:** showroom dimension for analytics |
| `form-cf-5` | date_picker | Data revenire | Follow-up date (YYYY-MM-DD) | Activity tracking |
| `form-cf-20` | select | Ofertat | Has offer been sent? (✅DA / ❌NU) | **Critical:** offer-sent flag (alternative to status 3) |
| `form-cf-7` | textarea | Informatii | General info | — |
| `form-cf-8` | textarea | Revenire 1 | First follow-up details (Date + Info) | — |
| `form-cf-9` | textarea | Revenirea 2 | Second follow-up details (Date + Info) | — |
| `form-cf-10` | textarea | Revenirea 3 | Third follow-up details (Date + Info) | — |
| `form-cf-38` | input | UTM_Source | UTM source parameter | **Critical:** marketing attribution |
| `form-cf-39` | input | UTM_Campanie | UTM campaign parameter | **Critical:** marketing attribution |
| `form-cf-40` | input | UTM_Content | UTM content parameter | **Critical:** marketing attribution |
| `form-cf-41` | input | UTM_Medium | UTM medium parameter | **Critical:** marketing attribution |

## Showroom field (`form-cf-14`)

**Possible values (exact, case-sensitive):**
- `Brașov`
- `București`
- `Cluj`

### When parsing in our code

```python
SHOWROOM_VALUES = {"Brașov", "București", "Cluj"}

def extract_showroom(custom_fields: list[dict]) -> str | None:
    for field in custom_fields:
        if field.get("field_id") == 14:
            value = field.get("value")
            if value in SHOWROOM_VALUES:
                return value
            return None
    return None
```

Store as separate column in `mefi_leads.showroom` for indexing.

## Ofertat field (`form-cf-20`)

**Possible values:**
- `✅DA` (Yes — offer sent)
- `❌NU` (No — offer not sent)

This is an **alternative signal** to status_id = 3 (Ofertat). Some leads might:
- Have status `IN PROCES` but `form-cf-20 = "✅DA"` → offer sent but status not updated
- Have status `Ofertat` but `form-cf-20 = "❌NU"` → status changed but field not updated

**For accurate metrics, treat lead as "Offer Sent" if EITHER:**
- `status.id == 3` (Ofertat)
- OR custom field `form-cf-20` value is `"✅DA"`

This redundancy helps catch data quality issues — and we can report them in AI Insights ("12 leads have offer status but field shows 'NO' — data quality issue").

## UTM fields (`form-cf-38..41`)

| Field | UTM parameter |
|---|---|
| `form-cf-38` | `utm_source` |
| `form-cf-39` | `utm_campaign` |
| `form-cf-40` | `utm_content` |
| `form-cf-41` | `utm_medium` |

These come from URL parameters when a lead form is submitted on sofabelle.ro. Example:

```
https://sofabelle.ro/contact?utm_source=facebook&utm_campaign=spring_sale_2026&utm_medium=cpc
```

When this user fills the form, lead is created with:
- `form-cf-38` = `"facebook"`
- `form-cf-39` = `"spring_sale_2026"`
- `form-cf-41` = `"cpc"`

### Important: UTM data quality

**UTM tracking only works if:**
1. The form on sofabelle.ro reads UTM parameters from URL and pushes them in API request
2. Make.com / Zapier / other integrations don't strip them
3. Users come via UTM-tagged links (organic/direct traffic won't have UTMs)

**Expect a large fraction of leads to have empty UTM fields.** That's normal. We attribute by:
1. `source` field first (Meta ADS, Google, Site)
2. UTM details if available
3. Default to source name if UTM is empty

## Parsing custom_fields safely

The `custom_fields` array has variable shape. Defensive parsing:

```python
def get_custom_field_value(
    custom_fields: list[dict],
    field_id: int,
) -> Any:
    """Safely extract a custom field value by ID."""
    for field in custom_fields:
        if field.get("field_id") == field_id:
            return field.get("value")
    return None


# Usage
showroom = get_custom_field_value(lead["custom_fields"], 14)
utm_source = get_custom_field_value(lead["custom_fields"], 38)
ofertat = get_custom_field_value(lead["custom_fields"], 20)
```

## DB schema mapping

In our `mefi_leads` table, we **extract critical custom fields into dedicated columns** for indexing and query performance:

```sql
CREATE TABLE mefi_leads (
    -- ... standard fields ...
    
    -- Extracted from custom_fields for fast queries:
    showroom TEXT,                       -- from form-cf-14
    offer_sent_flag BOOLEAN,             -- from form-cf-20 ("✅DA" → true)
    utm_source TEXT,                     -- from form-cf-38
    utm_campaign TEXT,                   -- from form-cf-39
    utm_content TEXT,                    -- from form-cf-40
    utm_medium TEXT,                     -- from form-cf-41
    
    -- All custom fields preserved as-is for flexibility:
    custom_fields_raw JSONB,
    
    -- Full original payload for debugging:
    raw_payload JSONB
);
```

Indexes on extracted columns for filter/groupby performance:
```sql
CREATE INDEX ON mefi_leads (tenant_id, showroom);
CREATE INDEX ON mefi_leads (tenant_id, utm_source);
CREATE INDEX ON mefi_leads (tenant_id, utm_campaign);
```

## Schema evolution

If Sofa Belle adds new custom fields in MEFI:
1. They appear automatically in `custom_fields[]` array — no API change
2. Our sync continues working (stored in `custom_fields_raw` JSONB)
3. To add to dedicated column: migration + extract logic update
4. **Detection:** our sync should log unknown `field_id`s for review

## Last updated

This file: 2026-05-18

Source: BELLE SOFA S.R.L. API documentation PDF, lead create endpoint section.
