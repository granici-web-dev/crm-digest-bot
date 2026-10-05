from datetime import date
from typing import Any

from digest.config import AppConfig, LeadCategory
from digest.snapshot import (
    ClientsCounters,
    ClientsFindings,
    CustomFieldProblem,
    SkippedClient,
    SnapshotCounters,
    categorize,
    client_to_snapshot_row,
    clients_alert_text,
    clients_completeness_failure,
    completeness_failure,
    lead_to_snapshot_row,
    parse_clients,
    parse_leads,
    strip_contacts,
)
from factories import make_client, make_custom_fields, make_lead, recorded_search_leads

SNAPSHOT_DATE = date(2026, 9, 24)
CONTACT_SECRET = b"k" * 32


def test_null_status_is_unmapped(app_config: AppConfig) -> None:
    assert categorize(None, app_config.status_mapping) == LeadCategory("UNMAPPED")


def test_unknown_status_is_counted_as_unmapped(app_config: AppConfig) -> None:
    parsed, skipped = parse_leads([make_lead(status={"id": 99, "name": "STATUS NOU"})])

    row, _ = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

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
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert row["showroom"] is None
    assert problems == [CustomFieldProblem(14, "Showroom", "name_mismatch", "Oras")]


def data_revenire_rows(
    app_config: AppConfig, custom_fields_by_lead: list[list[dict[str, Any]]]
) -> list[tuple[dict[str, Any], list[CustomFieldProblem]]]:
    parsed, _ = parse_leads(
        [
            make_lead(id=lead_id, custom_fields=custom_fields)
            for lead_id, custom_fields in enumerate(custom_fields_by_lead, start=1)
        ]
    )
    return [
        lead_to_snapshot_row(
            lead, "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
        )
        for lead in parsed
    ]


def test_data_revenire_problem_distinguishes_empty_invalid_missing_and_renamed(
    app_config: AppConfig,
) -> None:
    renamed = make_custom_fields(data_revenire="2026-10-01")
    renamed[1]["name"] = "Data revenirii"
    without_field = [field for field in make_custom_fields() if field["field_id"] != 5]

    rows_and_problems = data_revenire_rows(
        app_config,
        [
            make_custom_fields(data_revenire="2026-10-01"),
            make_custom_fields(data_revenire=None),
            make_custom_fields(data_revenire=""),
            make_custom_fields(data_revenire="01.10.2026"),
            without_field,
            renamed,
        ],
    )

    assert [
        (row["data_revenire"], row["data_revenire_problem"]) for row, _ in rows_and_problems
    ] == [
        (date(2026, 10, 1), None),
        (None, None),
        (None, None),
        (None, "unexpected_value"),
        (None, "missing"),
        (None, "name_mismatch"),
    ]
    assert [problems for _, problems in rows_and_problems[3:]] == [
        [CustomFieldProblem(5, "Data revenire", "unexpected_value", "01.10.2026")],
        [CustomFieldProblem(5, "Data revenire", "missing", None)],
        [CustomFieldProblem(5, "Data revenire", "name_mismatch", "Data revenirii")],
    ]


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
        lead_to_snapshot_row(
            lead, "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
        )
        for lead in parsed
    ]

    assert [row["ofertat"] for row, _ in rows_and_problems] == [True, False, None, None]
    assert rows_and_problems[3][1] == [CustomFieldProblem(20, "Ofertat", "unexpected_value", "DA")]


def test_recorded_leads_map_to_expected_rows(app_config: AppConfig) -> None:
    parsed, skipped = parse_leads(recorded_search_leads())

    rows = [
        lead_to_snapshot_row(
            lead, "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
        )
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


def test_raw_strip_removes_contact_fields(app_config: AppConfig) -> None:
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
    assert lead["phone"] == "+40700000099"


def test_lead_raw_keeps_only_whitelisted_custom_fields(app_config: AppConfig) -> None:
    textarea_phone = "Revine, sunati pe +40711111177"
    custom_fields = [
        *make_custom_fields(),
        {
            "field_id": 8,
            "name": "Revenire 1 (Data+Info)",
            "type": "textarea",
            "value": textarea_phone,
        },
        {"field_id": 51, "name": "Mesaj", "type": "textarea", "value": textarea_phone},
        {"field_id": 38, "name": "UTM_Source", "type": "input", "value": "facebook"},
    ]
    parsed, _ = parse_leads([make_lead(custom_fields=custom_fields)])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == []
    assert [field["field_id"] for field in row["raw"]["custom_fields"]] == [14, 5, 20, 38]
    assert "+40711111177" not in str(row["raw"])


def test_modalitate_contact_is_kept_in_raw_without_problem(app_config: AppConfig) -> None:
    custom_fields = [
        *make_custom_fields(),
        {"field_id": 4, "name": "Modalitate contact", "type": "select", "value": "Whatsapp"},
    ]
    parsed, _ = parse_leads([make_lead(custom_fields=custom_fields)])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == []
    assert {"field_id": 4, "name": "Modalitate contact", "type": "select", "value": "Whatsapp"} in (
        row["raw"]["custom_fields"]
    )


def test_unknown_lead_custom_field_is_recorded_and_not_stored(app_config: AppConfig) -> None:
    custom_fields = [
        *make_custom_fields(),
        {"field_id": 60, "name": "Telefon secundar", "type": "input", "value": "+40711111166"},
    ]
    parsed, _ = parse_leads([make_lead(custom_fields=custom_fields)])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == [CustomFieldProblem(60, "Telefon secundar", "unknown_custom_field", None)]
    assert "+40711111166" not in str(row["raw"])


def test_custom_fields_of_invalid_shape_are_not_stored(app_config: AppConfig) -> None:
    parsed, _ = parse_leads([make_lead(custom_fields="Informatii: +40711111155")])

    row, _ = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert row["raw"]["custom_fields"] is None


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
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == [CustomFieldProblem(None, "whatsapp_number", "unknown_raw_key", None)]
    assert row["lead_id"] == 1001
    assert "whatsapp_number" not in row["raw"]


def test_keys_added_by_mefi_in_october_are_stored_with_staff_followers_only(
    app_config: AppConfig,
) -> None:
    score = {
        "band": {"key": "cold", "label": "Rece"},
        "calculated_at": "2026-10-05T06:00:00Z",
        "disqualified": False,
        "is_stale": False,
        "rules_version": 1,
        "value": 0,
    }
    lead = make_lead(
        updated_at="2026-10-04T10:00:00Z",
        score=score,
        followers=[{"id": 8, "name": "Roibu Valeria", "email": "staff@example.test"}],
        awareness=None,
    )
    parsed, _ = parse_leads([lead])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == [CustomFieldProblem(None, "followers.email", "unknown_raw_key", None)]
    assert row["raw"]["updated_at"] == "2026-10-04T10:00:00Z"
    assert row["raw"]["score"] == score
    assert row["raw"]["followers"] == [{"id": 8, "name": "Roibu Valeria"}]
    assert row["raw"]["awareness"] is None


def test_awareness_is_stored_with_known_nested_keys_only(app_config: AppConfig) -> None:
    awareness = {"level": 3, "key": "solution_aware", "label": "Conștient de soluție"}
    parsed, _ = parse_leads([make_lead(awareness={**awareness, "note": "NOTA_CLIENT_TEST"})])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == [CustomFieldProblem(None, "awareness.note", "unknown_raw_key", None)]
    assert row["raw"]["awareness"] == awareness


def test_elimination_is_kept_without_detailed_reason(app_config: AppConfig) -> None:
    elimination = {
        "type": "lost",
        "reason": {"id": 3, "name": "BUGET"},
        "detailed_reason": "NOTA_CLIENT_TEST",
        "marked_at": "2026-09-23T09:00:00Z",
        "marked_by": {"id": 8, "name": "Roibu Valeria"},
    }
    parsed, _ = parse_leads([make_lead(elimination=elimination)])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == []
    assert row["raw"]["elimination"] == {
        key: value for key, value in elimination.items() if key != "detailed_reason"
    }


def test_unknown_elimination_key_is_recorded_and_not_stored(app_config: AppConfig) -> None:
    parsed, _ = parse_leads(
        [make_lead(elimination={"type": "lost", "client_comment": "NOTA_CLIENT_TEST"})]
    )

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert problems == [
        CustomFieldProblem(None, "elimination.client_comment", "unknown_raw_key", None)
    ]
    assert row["raw"]["elimination"] == {"type": "lost"}


def test_lead_without_is_duplicate_is_kept_with_null(app_config: AppConfig) -> None:
    lead = make_lead()
    del lead["is_duplicate"]

    parsed, skipped = parse_leads([lead])
    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert skipped == []
    assert row["is_duplicate"] is None
    assert problems == []


def test_invalid_status_is_unmapped_and_recorded(app_config: AppConfig) -> None:
    parsed, skipped = parse_leads([make_lead(status={"id": "16", "name": None})])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
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
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
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


def test_contact_that_is_not_text_gets_no_key_and_is_recorded(app_config: AppConfig) -> None:
    parsed, _ = parse_leads([make_lead(phone=712345678, email=5)])

    row, problems = lead_to_snapshot_row(
        parsed[0], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping, CONTACT_SECRET
    )

    assert (row["contact_phone_key"], row["contact_email_key"]) == (None, None)
    assert problems == [
        CustomFieldProblem(None, "phone", "invalid_shape", None),
        CustomFieldProblem(None, "email", "invalid_shape", None),
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


def test_client_row_keeps_only_known_keys_without_personal_data(app_config: AppConfig) -> None:
    raw = make_client(
        elimination={
            "type": "lost",
            "reason": {"id": 3, "name": "NU  A RASPUNS"},
            "detailed_reason": "Clientul CLIENT_TEST a sunat de pe +40700000099",
            "marked_at": "2026-09-23T12:00:00Z",
            "marked_by": {"id": 12, "name": "Dragoi Mihaela"},
        },
        passport_scan="https://example.test/scan.pdf",
    )
    parsed, skipped = parse_clients([raw])

    row, problem, unknown_keys = client_to_snapshot_row(
        parsed[0][0], parsed[0][1], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping.clients
    )

    assert (skipped, problem, unknown_keys) == ([], None, {"passport_scan"})
    assert (row["client_id"], row["showroom"], row["state"], row["source_name"]) == (
        2001,
        "București",
        "active",
        "Showroom",
    )
    for personal_key in (
        "name",
        "identity",
        "business",
        "business_details",
        "banking",
        "billing",
        "shipping",
        "website",
        "passport_scan",
    ):
        assert personal_key not in row["raw"]
    assert "detailed_reason" not in row["raw"]["elimination"]
    assert row["raw"]["elimination"]["reason"] == {"id": 3, "name": "NU  A RASPUNS"}
    assert "CLIENT_TEST" not in str(row["raw"])
    assert raw["name"] == "CLIENT_TEST"


def test_client_showroom_name_mismatch_nulls_value(app_config: AppConfig) -> None:
    raw = make_client(
        custom_fields=[{"field_id": 15, "name": "Oras", "type": "select", "value": "Cluj"}]
    )
    parsed, _ = parse_clients([raw])

    row, problem, _ = client_to_snapshot_row(
        parsed[0][0], parsed[0][1], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping.clients
    )

    assert row["showroom"] is None
    assert problem == CustomFieldProblem(15, "Showroom", "name_mismatch", "Oras")


def test_client_raw_drops_textareas_unknown_custom_fields_and_nested_keys(
    app_config: AppConfig,
) -> None:
    textarea_phone = "Montaj luni, sunati pe +40711111144"
    raw = make_client(
        custom_fields=[
            {"field_id": 15, "name": "Showroom", "type": "select", "value": "Cluj"},
            {"field_id": 13, "name": "Informatii", "type": "textarea", "value": textarea_phone},
            {"field_id": 44, "name": "Revenire 1 (Data+Info)", "type": "textarea", "value": "x"},
            {"field_id": 33, "name": "Ofertat", "type": "select", "value": "✅DA"},
            {"field_id": 60, "name": "Telefon livrare", "type": "input", "value": "+40711111133"},
        ],
        responsibles=[{"id": 12, "name": "Dragoi Mihaela", "phone": "+40711111122"}],
        elimination={"type": "lost", "reason": {"id": 3, "name": "BUGET"}, "note": textarea_phone},
    )
    parsed, _ = parse_clients([raw])

    row, _, unknown_keys = client_to_snapshot_row(
        parsed[0][0], parsed[0][1], "sofabelle", SNAPSHOT_DATE, app_config.status_mapping.clients
    )

    assert unknown_keys == {
        "custom_fields.60 «Telefon livrare»",
        "responsibles.phone",
        "elimination.note",
    }
    assert [field["field_id"] for field in row["raw"]["custom_fields"]] == [15, 33]
    assert row["raw"]["responsibles"] == [{"id": 12, "name": "Dragoi Mihaela"}]
    assert row["raw"]["elimination"] == {"type": "lost", "reason": {"id": 3, "name": "BUGET"}}
    for synthetic_phone in ("+40711111144", "+40711111133", "+40711111122"):
        assert synthetic_phone not in str(row["raw"])


def test_client_without_created_at_is_skipped_with_id_and_reason() -> None:
    broken = make_client()
    del broken["created_at"]

    parsed, skipped = parse_clients([broken, make_client(id=2002, source="Showroom")])

    assert skipped == [SkippedClient(2001, "created_at: missing")]
    assert [client.id for client, _ in parsed] == [2002]
    assert parsed[0][0].source is None


def test_clients_short_of_api_total_beyond_threshold_is_incomplete(app_config: AppConfig) -> None:
    thresholds = app_config.status_mapping.snapshot.completeness

    assert clients_completeness_failure(ClientsCounters(800, 792, 3, []), thresholds) is None
    assert clients_completeness_failure(ClientsCounters(800, 790, 0, []), thresholds) == (
        "получено 790 клиентов из 800"
    )
    assert clients_completeness_failure(ClientsCounters(800, 0, 0, []), thresholds) == (
        "записано 0 клиентов из 800"
    )


def test_skipped_clients_alert_names_ten_ids_with_reasons_and_counts_the_rest() -> None:
    skipped = [SkippedClient(client_id, "created_at: missing") for client_id in range(1, 13)]
    skipped.append(SkippedClient(None, "id: missing"))

    alert = clients_alert_text(date(2026, 9, 24), None, [], ClientsFindings([], skipped, []))

    assert alert == (
        "Клиенты mefi пропущены из-за битой формы: 13 ("
        + "; ".join(f"id {client_id}: created_at: missing" for client_id in range(1, 11))
        + "; и ещё 3). В Contract Cantitate они не посчитаны."
    )
