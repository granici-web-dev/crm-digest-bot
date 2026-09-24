from datetime import date

from digest.config import AppConfig, LeadCategory
from digest.snapshot import (
    CustomFieldProblem,
    SnapshotCounters,
    categorize,
    completeness_failure,
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
    lead = make_lead(
        website="https://client1.example.com",
        title="Director",
        description="Sunati dupa ora 18",
    )

    stripped = strip_contacts(lead, app_config.status_mapping.raw_strip)

    for contact_key in ("name", "phone", "email", "identity", "website", "title", "description"):
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


def test_raw_strip_removes_company_contacts(app_config: AppConfig) -> None:
    lead = make_lead(
        client_type="company",
        name="SRL CLIENT_TEST",
        company={"name": "SRL CLIENT_TEST", "fiscal_code": "RO00000001"},
        business={"name": "SRL CLIENT_TEST", "phone": "+40700000002"},
    )

    stripped = strip_contacts(lead, app_config.status_mapping.raw_strip)

    for contact_key in ("name", "company", "business"):
        assert contact_key not in stripped
    assert stripped["client_type"] == "company"


def test_unknown_top_level_key_is_recorded_without_value(app_config: AppConfig) -> None:
    parsed, _ = parse_leads([make_lead(whatsapp_number="+40700000003")])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping
    )

    assert problems == [CustomFieldProblem(None, "whatsapp_number", "unknown_raw_key", None)]
    assert row["lead_id"] == 1001


def test_lead_without_is_duplicate_is_kept_with_null(app_config: AppConfig) -> None:
    lead = make_lead()
    del lead["is_duplicate"]

    parsed, skipped = parse_leads([lead])
    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping
    )

    assert skipped == []
    assert row["is_duplicate"] is None
    assert problems == []


def test_invalid_status_is_unmapped_and_recorded(app_config: AppConfig) -> None:
    parsed, skipped = parse_leads([make_lead(status={"id": "16", "name": None})])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping
    )

    assert skipped == []
    assert (row["category"], row["status_name"]) == ("UNMAPPED", None)
    assert problems == [CustomFieldProblem(None, "status", "invalid_shape", None)]


def test_invalid_secondary_fields_become_null_and_are_recorded(app_config: AppConfig) -> None:
    custom_fields = make_custom_fields(ofertat="✅DA")
    del custom_fields[2]["name"]
    parsed, skipped = parse_leads(
        [
            make_lead(
                is_duplicate="no",
                source={"id": "6", "name": "Site"},
                assigned_to={"id": 8, "name": None},
                last_contact_at="2026-09-23T08:00:00",
                custom_fields=custom_fields,
            )
        ]
    )

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping
    )

    assert skipped == []
    assert [row[key] for key in ("is_duplicate", "source_name", "assigned_to_id")] == [None] * 3
    assert (row["last_contact_at"], row["ofertat"], row["showroom"]) == (None, None, "București")
    assert problems == [
        CustomFieldProblem(20, "Ofertat", "missing", None),
        CustomFieldProblem(None, "is_duplicate", "invalid_shape", None),
        CustomFieldProblem(None, "source", "invalid_shape", None),
        CustomFieldProblem(None, "assigned_to", "invalid_shape", None),
        CustomFieldProblem(None, "last_contact_at", "invalid_shape", None),
        CustomFieldProblem(20, "custom_fields", "invalid_shape", None),
    ]


def test_null_custom_fields_is_not_a_shape_problem() -> None:
    parsed, skipped = parse_leads([make_lead(custom_fields=None)])

    assert skipped == []
    assert parsed[0].lead.custom_fields == []
    assert parsed[0].lead.invalid_shape_fields == []


def counters(api_total: int, leads_written: int, skipped_count: int) -> SnapshotCounters:
    return SnapshotCounters(
        api_total=api_total,
        leads_written=leads_written,
        unmapped_count=0,
        skipped_count=skipped_count,
        is_duplicate_missing=0,
        skipped_leads=[],
        rate_limited_count=0,
        previous_snapshot_date=None,
        missing_since_previous=None,
        new_unmapped_lead_ids=[],
        won_converted_mismatch_ids=[],
        custom_field_mismatches=[],
    )


def test_zero_written_leads_is_incomplete(app_config: AppConfig) -> None:
    thresholds = app_config.status_mapping.snapshot.completeness

    assert completeness_failure(counters(3, 0, 3), thresholds) == "записано 0 лидов из 3"
    assert completeness_failure(counters(0, 0, 0), thresholds) is None


def test_received_short_of_api_total_beyond_threshold_is_incomplete(
    app_config: AppConfig,
) -> None:
    thresholds = app_config.status_mapping.snapshot.completeness

    assert completeness_failure(counters(20, 15, 0), thresholds) is None
    assert completeness_failure(counters(20, 14, 0), thresholds) == "получено 14 лидов из 20"
    assert completeness_failure(counters(3000, 2985, 0), thresholds) is None
    assert completeness_failure(counters(3000, 2984, 0), thresholds) == (
        "получено 2984 лидов из 3000"
    )


def test_skipped_leads_beyond_threshold_is_incomplete(app_config: AppConfig) -> None:
    thresholds = app_config.status_mapping.snapshot.completeness

    assert completeness_failure(counters(100, 90, 10), thresholds) is None
    assert completeness_failure(counters(100, 89, 11), thresholds) == (
        "пропущено 11 битых лидов из 100"
    )
    assert completeness_failure(counters(3000, 2970, 30), thresholds) is None
    assert completeness_failure(counters(3000, 2969, 31), thresholds) == (
        "пропущено 31 битых лидов из 3000"
    )
