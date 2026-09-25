import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from digest.config import StatusMapping, read_yaml
from digest.snapshot import categorize

BUCHAREST = ZoneInfo("Europe/Bucharest")

MEFI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mefi"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def raw_repository_config() -> dict[str, Any]:
    return {
        "status_mapping": read_yaml(CONFIG_DIR / "status-mapping.yaml"),
        "modules": read_yaml(CONFIG_DIR / "modules.yaml"),
        "managers": read_yaml(CONFIG_DIR / "managers.yaml"),
        "kpi": read_yaml(CONFIG_DIR / "kpi.yaml"),
    }


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


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


ETALON_PATH = Path(__file__).resolve().parent / "fixtures" / "etalon-2026-05.json"


def load_etalon() -> dict[str, Any]:
    etalon: dict[str, Any] = json.loads(ETALON_PATH.read_text(encoding="utf-8"))
    return etalon


def etalon_lead_rows(etalon: dict[str, Any], status_mapping: StatusMapping) -> list[dict[str, Any]]:
    manager_ids: dict[str, int] = etalon["managers"]
    ofertat_field = status_mapping.custom_fields.ofertat
    ofertat_values = {ofertat_field.ofertat_yes: True, ofertat_field.ofertat_no: False}
    rows = []
    for lead in etalon["leads"]:
        category = categorize(lead["status"], status_mapping)
        assignee = " ".join(lead["assigned_to"].split()) if lead["assigned_to"] else None
        rows.append(
            make_snapshot_row(
                lead_id=lead["row"],
                category=category.category,
                loss_reason=category.loss_reason,
                status_name=lead["status"],
                source_name=lead["source"],
                showroom=lead["showroom"],
                ofertat=None if lead["ofertat"] is None else ofertat_values[lead["ofertat"]],
                data_revenire=date.fromisoformat(lead["data_revenire"])
                if lead["data_revenire"]
                else None,
                is_duplicate=None,
                assigned_to_id=None if assignee is None else manager_ids[assignee],
                assigned_to_name=lead["assigned_to"],
                created_at=datetime.fromisoformat(lead["created_at"]),
                last_contact_at=datetime.fromisoformat(lead["last_contact_at"])
                if lead["last_contact_at"]
                else None,
            )
        )
    return rows


def make_snapshot_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "lead_id": 1001,
        "category": "ACTIVE",
        "loss_reason": None,
        "status_name": "IN PROCES",
        "source_name": "Site",
        "showroom": "București",
        "ofertat": False,
        "data_revenire": None,
        "is_duplicate": False,
        "assigned_to_id": 12,
        "assigned_to_name": "Dragoi Mihaela",
        "created_at": datetime(2026, 9, 23, 11, 0, tzinfo=BUCHAREST),
        "status_changed_at": None,
        "last_contact_at": datetime(2026, 9, 23, 11, 0, tzinfo=BUCHAREST),
        "converted_at": None,
    }
    row.update(overrides)
    return row
