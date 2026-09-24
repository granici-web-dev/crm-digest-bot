# MEFI Enumerations for Sofa Belle

> These are the actual ID mappings for Sofa Belle's MEFI account. **They are tenant-specific** — other MEFI customers will have different IDs.
>
> When onboarding a new client, these enums must be re-discovered for that tenant.

## Status IDs

| ID | Name | Lifecycle bucket | Funnel stage | Notes |
|---|---|---|---|---|
| 16 | IN PROCES | active | Engagement | Default for API-created leads |
| 14 | Revenire 1 | active | Engagement | First follow-up needed |
| 15 | Revenire 2 | active | Engagement | Second follow-up needed |
| 2 | Revenire 3 | active | Engagement | Third follow-up needed |
| 17 | SHOWROOM | active | **Visit (V)** | Booked/completed showroom visit |
| 24 | DESIGNER | active | Engagement | Designer consultation |
| 25 | INFLUENCER | active | Engagement | Influencer-sourced |
| 3 | Ofertat | active | **Offer (O)** | Offer sent |
| 12 | Stand BY | active | Engagement | Paused |
| 1 | Clienți | active | **Contract (C) — WON** | Closed-won deal |
| 18 | NU A RASPUNS | lost | — | Loss: didn't respond |
| 19 | A REFUZAT | lost | — | Loss: refused |
| 20 | PRODUS NEPOTRIVIT | lost | — | Loss: wrong product |
| 21 | BUGET | lost | — | Loss: budget |
| 22 | CONCURENTA | lost | — | Loss: chose competitor |
| 23 | IRELEVANT | junk | — | Junk: irrelevant |
| 26 | TIMP | lost | — | Loss: timing |

### Funnel stage mapping (for Sales Dashboard)

```
Lead → Vizita → Oferta → Contract
 │       │        │         │
 │       │        │         └─ status_id = 1 (Clienți)
 │       │        └─ status_id = 3 (Ofertat) OR form-cf-20 = "✅DA"
 │       └─ status_id = 17 (SHOWROOM)
 └─ Any lead with lifecycle="active" except those above
```

### Loss reasons (for AI Insights)

When `lifecycle = "lost"`, the specific status tells us **why**:
- 18 NU A RASPUNS → "Didn't respond to outreach"
- 19 A REFUZAT → "Refused after offer"
- 20 PRODUS NEPOTRIVIT → "Product wasn't right"
- 21 BUGET → "Budget mismatch"
- 22 CONCURENTA → "Lost to competitor"
- 26 TIMP → "Bad timing"

Aggregating these reveals patterns ("40% of losses are to competitors → competitive analysis needed").

## Source IDs

**Verified from raw_mefi_leads (2026-05-28, 1000 leads ingested):**

| ID | Name | Leads (actual) | Metric category | Notes |
|---|---|---|---|---|
| 5 | Showroom | 266 | `showroom` | Walk-in to physical showroom — **this is "Vizita" in Sofa Belle's Excel** |
| 11 | Mail | 326 | `mail` | Email leads (forms, direct email) |
| 10 | Telefon | 164 | `telefon` | Inbound phone calls |
| 9 | WhatsApp | 123 | `whatsapp` | WhatsApp Business leads |
| 6 | Site | 91 | `site` | Website contact forms |
| 12 | Colaborare | 12 | `colaborare` | Partner/collaboration referrals |
| 3 | Recomandare | 6 | `recomandare` | Word-of-mouth referrals |
| 2 | Meta ADS | 6 | `meta` | Facebook/Instagram paid ads |
| 7 | Arhitect | 3 | `arhitect` | Interior architect referrals |
| 13 | Client Fidel | 2 | `client_fidel` | Repeat customers |
| null | (unknown) | 1 | `other` | Source not captured |

**Source IDs NOT present in Sofa Belle's data:**
- `1 = Google` — zero leads. Google traffic comes in via `site` (id=6) or direct. No separate Google source.
- `4 = Teren` — zero leads in current dataset.

### ⚠️ Funnel model clarification (2026-05-28)

**"Vizita" in Sofa Belle's Excel ≠ status SHOWROOM (id=17).**

Sofa Belle's "Vizita" = **leads with source_id=5 (Showroom walk-ins)**. These are people who walk into a physical showroom and become a lead there and then. They do NOT flow through a "booking" step — they are walk-ins by definition.

This means the funnel is NOT `Lead → Visit → Offer → Contract` in the traditional sense. It is:

```
All leads (from any source)
  ├── Showroom walk-ins (source_id=5)  → naturally higher offer/contract rates
  ├── Phone / WhatsApp / Email / Web   → need outreach to get to offer stage
  └── ...
      ↓
   Offer (status=3 OR offer_sent_flag=true)
      ↓
   Contract (status=1)
```

Metrics we compute:
- `visits_count` = COUNT(leads with source_id=5) — matches Sofa Belle's Excel "Vizita"
- `conversion_l_to_v` = visits / total leads (showroom walk-in rate)
- `conversion_v_to_o` = offers from showroom leads / showroom leads (showroom → offer rate)
- `conversion_l_to_o`, `conversion_o_to_c`, `conversion_l_to_c` — all leads (source-agnostic)

**Open question for Sofa Belle:** In their Excel "Conversie vizita" column — do they mean (showroom leads / total leads) or (total leads that ever visited the showroom / total leads)? If the latter, some phone/web leads may also visit the showroom before getting an offer, and that visit is not tracked in MEFI source. **IT2-01** tracks this for Iteration 2.

### ⚠️ Designer as status, not source

`DESIGNER` (status_id=24) is a MEFI status indicating a designer consultation was requested. It is tracked separately in `daily_kpi.leads_designer` via `mefi_lead_history` joins. It is NOT a source in the source_daily_kpi breakdown.

## Salesperson IDs (assigned_to / created_by)

| ID | Name | Role (assumed) |
|---|---|---|
| 2 | Palega Andrei | Salesperson |
| 4 | Potinga Dima | Salesperson |
| 6 | Iordache Razvan | Salesperson |
| 7 | Marketing Sofa | **Marketing (not salesperson)** |
| 8 | Roibu Valeria | Salesperson |
| 9 | Moaca Andreea | Salesperson |
| 10 | Godja Adina Maria | Salesperson |
| 11 | Zagrian Emilia | Salesperson |
| 12 | Dragoi Mihaela | Salesperson |
| 13 | Raileanu Leon | Salesperson |
| 14 | Elena Ureche | Salesperson |

**Total: 11 active users, ~10 actual salespeople** (assuming `Marketing Sofa` is a marketing account, not a salesperson).

**⚠️ Confirm with Sofa Belle:**
- Sofa Belle stated "6 salespeople" — but MEFI shows 11 users. Possibly:
  - Some are part-time / former employees still in the system
  - Some are managers, not direct sellers
  - Need to identify the actual 6 active sellers and their showrooms

## Showroom assignment (custom field)

Showroom is **not a separate entity** — it's a custom field on the lead:
- Field key: `form-cf-14`
- Type: `select`
- Values (exact, case-sensitive): `"Brașov"`, `"București"`, `"Cluj"`

When building the Salespeople dashboard, we need a separate mapping `salesperson_id → showroom`, which is **not available via API**. Options:
1. Manual mapping in our DB (`mefi_salespeople.showroom` column)
2. Inferred from their leads' Showroom distribution (heuristic)
3. Ask Sofa Belle to provide the list

**Recommended:** Manual mapping table maintained by us, sourced from Sofa Belle once.

## Lifecycle values

| Value | Meaning | Counts as |
|---|---|---|
| `active` | Lead is in progress | Lead in funnel |
| `lost` | Lead is lost (specific status explains why) | Lost lead |
| `junk` | Lead is junk/spam/invalid | Excluded from metrics |

## Priority values

| Slug | Label (Romanian) | Use |
|---|---|---|
| `low` | Scăzută | Low priority lead |
| `medium` | Medie | Medium |
| `high` | Ridicată | High — likely valuable, fast response needed |

Could be used for SLA rules in AI Insights: "High-priority leads with response time > 1h" as alert.

## Last updated

This file: 2026-05-18

When new statuses, sources, or users are added in MEFI, update this file.

**Detection strategy:** Our sync task should log any new `status.id`, `source.id`, or `assigned_to.id` it sees that isn't in this enum file. We then update the file manually after verification.
