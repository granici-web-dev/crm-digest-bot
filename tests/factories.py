import json
from pathlib import Path
from typing import Any

MEFI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mefi"


def recorded_search_leads() -> list[dict[str, Any]]:
    recorded = json.loads((MEFI_FIXTURES / "search_3_leads.json").read_text(encoding="utf-8"))
    leads: list[dict[str, Any]] = recorded["data"]
    return leads


def make_custom_fields(
    showroom: str | None = "București",
    ofertat: str | None = "❌NU",
    data_revenire: str | None = None,
) -> list[dict[str, Any]]:
    return [
        {"field_id": 14, "name": "Showroom", "type": "select", "value": showroom},
        {"field_id": 5, "name": "Data revenire", "type": "date_picker", "value": data_revenire},
        {"field_id": 20, "name": "Ofertat", "type": "select", "value": ofertat},
        {"field_id": 7, "name": "Informatii", "type": "textarea", "value": "REDACTED"},
    ]


def make_lead(**overrides: Any) -> dict[str, Any]:
    lead: dict[str, Any] = {
        "id": 1001,
        "client_type": "individual",
        "name": "CLIENT_TEST",
        "phone": "+40700000099",
        "email": "client@example.test",
        "identity": {"card_number": None, "personal_id": None},
        "business": None,
        "location": {
            "address_line": "Strada Test 1",
            "city": "Cluj",
            "county": None,
            "postal_code": None,
            "country": {"code": "RO", "name": "Romania"},
            "coordinates": None,
        },
        "status": {"id": 16, "name": "IN PROCES"},
        "source": {"id": 6, "name": "Site"},
        "groups": [],
        "estimated_value": None,
        "assigned_to": {"id": 8, "name": "Roibu Valeria"},
        "created_by": {"id": 8, "name": "Roibu Valeria"},
        "lifecycle": "active",
        "is_duplicate": False,
        "last_contact_at": "2026-09-23T08:00:00Z",
        "created_at": "2026-09-23T08:00:00Z",
        "status_changed_at": None,
        "converted_at": None,
        "custom_fields": make_custom_fields(),
    }
    lead.update(overrides)
    return lead


def make_search_page(
    leads: list[dict[str, Any]], page: int = 1, total_pages: int = 1, total: int | None = None
) -> dict[str, Any]:
    return {
        "success": True,
        "request_id": "test-request",
        "data": leads,
        "meta": {
            "page": page,
            "per_page": 100,
            "total": len(leads) if total is None else total,
            "total_pages": total_pages,
        },
    }
