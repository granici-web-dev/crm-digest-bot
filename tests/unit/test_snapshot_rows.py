from datetime import date

from digest.config import AppConfig, LeadCategory
from digest.snapshot import (
    CustomFieldProblem,
    categorize,
    lead_to_snapshot_row,
    parse_leads,
    strip_contacts,
)
from factories import make_custom_fields, make_lead, recorded_search_leads

SNAPSHOT_DATE = date(2026, 9, 24)


def test_null_status_is_unmapped(app_config: AppConfig) -> None:
    assert categorize(None, app_config.status_mapping) == LeadCategory("UNMAPPED")


def test_unknown_status_is_counted_as_unmapped(app_config: AppConfig) -> None:
    parsed, skipped = parse_leads([make_lead(status={"id": 99, "name": "STATUS NOU"})])

    row, _ = lead_to_snapshot_row(parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping)

    assert skipped == []
    assert row["category"] == "UNMAPPED"
    assert row["status_name"] == "STATUS NOU"


def test_status_name_matched_after_trim_without_case_folding(app_config: AppConfig) -> None:
    status_mapping = app_config.status_mapping

    assert categorize(" NU A RASPUNS ", status_mapping) == LeadCategory("LOST", "NU_RASPUNS")
    assert categorize("clienți", status_mapping) == LeadCategory("UNMAPPED")
    assert categorize("Clienti", status_mapping) == LeadCategory("UNMAPPED")


def test_lead_without_created_at_is_skipped_and_counted() -> None:
    broken = make_lead(id=2002)
    del broken["created_at"]

    parsed, skipped = parse_leads([make_lead(id=2001), broken, make_lead(id="x")])

    assert [parsed_lead.lead.id for parsed_lead in parsed] == [2001]
    assert [(lead.lead_id, lead.reason) for lead in skipped] == [
        (2002, "created_at: missing"),
        (None, "id: int_type"),
    ]


def test_showroom_name_mismatch_nulls_value_and_records_mismatch(app_config: AppConfig) -> None:
    custom_fields = make_custom_fields(showroom="Cluj")
    custom_fields[0]["name"] = "Oras"
    parsed, _ = parse_leads([make_lead(custom_fields=custom_fields)])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping
    )

    assert row["showroom"] is None
    assert problems == [CustomFieldProblem(14, "Showroom", "name_mismatch", "Oras")]


def test_unknown_ofertat_value_is_null(app_config: AppConfig) -> None:
    parsed, _ = parse_leads(
        [
            make_lead(id=1, custom_fields=make_custom_fields(ofertat="✅DA")),
            make_lead(id=2, custom_fields=make_custom_fields(ofertat="❌NU")),
            make_lead(id=3, custom_fields=make_custom_fields(ofertat=None)),
            make_lead(id=4, custom_fields=make_custom_fields(ofertat="DA")),
        ]
    )

    rows_and_problems = [
        lead_to_snapshot_row(lead, "sofabelle", SNAPSHOT_DATE, app_config.status_mapping)
        for lead in parsed
    ]

    assert [row["ofertat"] for row, _ in rows_and_problems] == [True, False, None, None]
    assert rows_and_problems[3][1] == [CustomFieldProblem(20, "Ofertat", "unexpected_value", "DA")]


def test_recorded_leads_map_to_expected_rows(app_config: AppConfig) -> None:
    parsed, skipped = parse_leads(recorded_search_leads())

    rows = [
        lead_to_snapshot_row(lead, "sofabelle", SNAPSHOT_DATE, app_config.status_mapping)
        for lead in parsed
    ]

    assert skipped == []
    first_row, first_problems = rows[0]
    assert first_problems == []
    assert (first_row["lead_id"], first_row["category"], first_row["showroom"]) == (
        3028,
        "ACTIVE",
        "Brașov",
    )
    assert (first_row["ofertat"], first_row["source_name"], first_row["assigned_to_id"]) == (
        True,
        "WhatsApp",
        8,
    )


def test_raw_strip_removes_contact_fields_keeps_textareas(app_config: AppConfig) -> None:
    lead = make_lead(company={"name": "SRL Test"})

    stripped = strip_contacts(lead, app_config.status_mapping.raw_strip)

    for contact_key in ("name", "phone", "email", "identity", "business", "company"):
        assert contact_key not in stripped
    assert "address_line" not in stripped["location"]
    assert "coordinates" not in stripped["location"]
    assert stripped["location"]["city"] == "Cluj"
    assert stripped["custom_fields"][3] == {
        "field_id": 7,
        "name": "Informatii",
        "type": "textarea",
        "value": "REDACTED",
    }
    assert lead["phone"] == "+40700000099"
