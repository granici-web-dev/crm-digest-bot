from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from digest.acceptance.baseline import (
    METRICS_WITHOUT_SOURCE,
    TABLE_HEADER,
    BaselineFileError,
    BaselineRow,
    Value,
    baseline_rows,
    comparison_lines,
    date_warning,
    last_sunday,
    markdown_lines,
    month_analysis_dates,
    parse_baseline_file,
    parse_baseline_lines,
)
from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.weekly import week_days
from factories import BUCHAREST, make_snapshot_row

REPORT_DATE = date(2026, 9, 29)
DRAGOI = "Dragoi Mihaela"


def at(year_month_day: tuple[int, int, int], hour: int = 12) -> datetime:
    return datetime(*year_month_day, hour, 0, tzinfo=BUCHAREST)


def lead(lead_id: int, created: tuple[int, int, int], **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"created_at": at(created), "last_contact_at": at(created)}
    fields.update(overrides)
    return make_snapshot_row(lead_id=lead_id, **fields)


def won(
    lead_id: int, created: tuple[int, int, int], converted: tuple[int, int, int] | None
) -> dict[str, Any]:
    return lead(
        lead_id,
        created,
        category="WON",
        status_name="Clienți",
        ofertat=True,
        converted_at=None if converted is None else at(converted),
    )


# Кадр на 29.09.2026 (вторник): сентябрь cur, август prev, консультант Dragoi Mihaela.
LEADS = [
    # Stand BY с июня: оферта, Data revenire 20.09 прошла, статус задан при создании (111 суток).
    lead(
        1,
        (2026, 6, 10),
        category="LOST",
        loss_reason="STAND_BY",
        status_name="Stand BY",
        ofertat=True,
        data_revenire=date(2026, 9, 20),
    ),
    lead(
        2,
        (2026, 9, 10),
        category="LOST",
        loss_reason="NU_RASPUNS",
        status_name="NU A RASPUNS",
        status_changed_at=at((2026, 9, 12)),
    ),
    lead(
        3,
        (2026, 8, 5),
        category="LOST",
        loss_reason="NU_RASPUNS",
        status_name="NU A RASPUNS",
        status_changed_at=at((2026, 8, 6)),
    ),
    # Не взят (Marketing Sofa), без шоурума, создан сегодня: старше 4 ч, но не суток.
    lead(4, (2026, 9, 29), assigned_to_id=7, assigned_to_name="Marketing Sofa", showroom=None),
    won(5, (2026, 8, 10), (2026, 8, 15)),
    won(6, (2026, 9, 2), (2026, 9, 5)),
    lead(7, (2026, 9, 15), category="UNMAPPED", status_name=None),
    won(8, (2026, 7, 1), None),
    # Оферта в работе, последний контакт 01.09: висит.
    lead(9, (2026, 8, 20), ofertat=True, last_contact_at=at((2026, 9, 1))),
]


def values(rows: list[BaselineRow]) -> dict[str, Value]:
    return {row.id: row.value for row in rows}


def rows_of(config: AppConfig, leads: list[dict[str, Any]] = LEADS) -> list[BaselineRow]:
    lead_frame = prepare_lead_frame(leads, config)
    return baseline_rows(lead_frame, {REPORT_DATE: lead_frame}, (), REPORT_DATE, config)


def test_stock_rows_match_metric_functions(app_config: AppConfig) -> None:
    result = values(rows_of(app_config))

    assert {row_id: result[row_id] for row_id in result if row_id.startswith("A")} == {
        "A1.stand_by": 1,
        "A1.stand_by.with_offer": 1,
        "A1.stand_by.revenire_due": 1,
        "A1.stand_by.older_30d": 1,
        "A1.stand_by.older_90d": 1,
        "A2.missing_followup_date": 0,
        "A2.unreadable": 0,
        "A3.overdue_revenire": 1,
        "A3.max_days_overdue": 9,
        "A4.stale_offers": 1,
        "A4.offers_in_work": 1,
        "A5.untouched": 1,
        "A5.untouched.cur_average": 1.0,
        "A6.nar": 2,
        "A6.nar.cur": 1,
        "A6.nar.prev": 1,
        "A7.not_taken": 1,
        "A7.not_taken.since": 1,
        "A7.not_taken.older_1d": 0,
    }


def test_untouched_average_names_days_with_snapshot(app_config: AppConfig) -> None:
    rows = {row.id: row for row in rows_of(app_config)}

    assert rows["A5.untouched.cur_average"].period == "1 из 29 дней, сентябрь 2026"


def test_flow_rows_for_current_and_previous_month(app_config: AppConfig) -> None:
    result = values(rows_of(app_config))

    # Сентябрь: лиды 2, 4, 6, 7, клиент 6. Август: лиды 3, 5, 9, клиент 5, оферты 5 и 9.
    assert result["B1.scr.cur"] == 25.0
    assert (result["B1.clienti.cur"], result["B1.useful.cur"]) == (1, 4)
    assert result["B1.scr.prev"] == 33.3
    assert (result["B2.l2o.prev"], result["B2.offers.prev"]) == (66.7, 2)
    assert (result["B3.o2c.cur"], result["B3.o2c.prev"]) == (100.0, 50.0)
    assert (result["B5.cycle_median.cur"], result["B5.cycle_median.prev"]) == (3.0, 5.0)
    assert (result["B5.fast_share.cur"], result["B5.contracts.cur"]) == (100.0, 1)
    # Сентябрьской когорте на 29.09 нет 30 суток.
    assert (result["B6.cohort_30d.cur"], result["B6.cohort_30d.prev"]) == (None, 33.3)
    assert (result["B7.repeat.cur"], result["B7.clients.prev"]) == (0, 1)
    # Окно d7 с 30.08 19:00: лиды 2, 4, 6, 7, договор 6.
    assert (result["B4.contracts"], result["B4.useful"], result["B4.contract_rate"]) == (1, 4, 25.0)


def test_consultant_rows_since_created_from_and_month(app_config: AppConfig) -> None:
    result = values(rows_of(app_config))

    # С 01.06 у Dragoi лиды 1, 2, 3, 5, 6, 7, 8, 9; клиенты 5, 6, 8; оферты 1, 5, 6, 8, 9.
    assert result[f"C.{DRAGOI}.since.leads"] == 8
    assert result[f"C.{DRAGOI}.since.scr"] == 37.5
    assert result[f"C.{DRAGOI}.since.o2c"] == 60.0
    assert result[f"C.{DRAGOI}.cur.leads"] == 3
    assert result[f"C.{DRAGOI}.since.stand_by"] == 1
    assert result[f"C.{DRAGOI}.since.nar"] == 2
    assert result[f"C.{DRAGOI}.missing_followup_date"] == 0
    assert result[f"C.{DRAGOI}.overdue_revenire"] == 1
    assert result["C.Godja Adina Maria.since.leads"] == 0
    assert result["C.Godja Adina Maria.since.scr"] is None


def test_source_rows_and_utm_share(app_config: AppConfig) -> None:
    site_leads = [lead(100 + index, (2026, 9, 1)) for index in range(7)]
    irelevant = [
        lead(
            200 + index,
            (2026, 9, 1),
            category="LOST",
            loss_reason="IRELEVANT",
            status_name="IRELEVANT",
        )
        for index in range(2)
    ]
    with_campaign = [lead(300 + index, (2026, 9, 1), utm_campanie="toamna") for index in range(3)]
    leads = [*site_leads, *irelevant, *with_campaign, won(400, (2026, 9, 1), (2026, 9, 3))]

    result = values(rows_of(app_config, leads))

    # 13 лидов Site: IRELEVANT 2 из 13, клиент 1 из 11 полезных, кампания у 3 из 13.
    assert result["D.leads.Site"] == 13
    assert result["D1.irr.Site"] == 15.4
    assert result["D2.scr.Site"] == 9.1
    assert (result["D3.utm_leads"], result["D3.utm_share"]) == (3, 23.1)
    assert result["D3.utm_share.cur"] == 23.1


def test_source_below_min_leads_has_no_row(app_config: AppConfig) -> None:
    result = values(rows_of(app_config))

    assert not [row_id for row_id in result if row_id.startswith(("D1.", "D2."))]


def test_data_quality_rows(app_config: AppConfig) -> None:
    result = values(rows_of(app_config))

    assert result["F.no_showroom"] == 1
    assert result["F.no_showroom.since"] == 1
    assert result["F.unmapped"] == 1
    assert result["F.won_without_converted_at"] == 1


def test_external_metrics_have_no_source(app_config: AppConfig) -> None:
    rows = {row.id: row for row in rows_of(app_config)}

    for row_id, _label in METRICS_WITHOUT_SOURCE:
        assert (rows[row_id].value, rows[row_id].unit) == (None, "нет источника")


@pytest.mark.parametrize(
    ("report_date", "sunday"),
    [
        (date(2026, 9, 29), date(2026, 9, 27)),
        # В воскресенье неделя кончается этой датой: снапшот 19:00 закрывает её.
        (date(2026, 9, 27), date(2026, 9, 27)),
        (date(2026, 9, 28), date(2026, 9, 27)),
    ],
)
def test_touch_week_is_last_full_monday_to_sunday(report_date: date, sunday: date) -> None:
    assert last_sunday(report_date) == sunday
    assert week_days(last_sunday(report_date))[0] == date(2026, 9, 21)


def test_touch_rows_report_partial_week(app_config: AppConfig) -> None:
    before = lead(500, (2026, 9, 1))
    after = {
        **before,
        "category": "ACTIVE_FOLLOWUP",
        "status_name": "Revenire 1",
        "status_changed_at": at((2026, 9, 26)),
    }
    snapshots = (
        PreviousSnapshot(date(2026, 9, 25), prepare_lead_frame([before], app_config)),
        PreviousSnapshot(date(2026, 9, 27), prepare_lead_frame([after], app_config)),
    )
    lead_frame = prepare_lead_frame(LEADS, app_config)

    rows = {
        row.id: row
        for row in baseline_rows(
            lead_frame, {REPORT_DATE: lead_frame}, snapshots, REPORT_DATE, app_config
        )
    }

    assert rows["C.touch.total"].value == 1
    assert rows[f"C.{DRAGOI}.touch"].value == 1
    # Как сноски w14: первый покрытый день и покрытые дни без своего снапшота.
    assert rows["C.touch.total"].period == (
        "21.09–27.09.2026, посчитано с 26.09, без снапшота: 26.09, неделя неполная"
    )


def test_touch_rows_without_snapshot_pair_have_no_value(app_config: AppConfig) -> None:
    rows = {row.id: row for row in rows_of(app_config)}

    assert rows["C.touch.total"].value is None
    assert rows["C.touch.total"].period == "21.09–27.09.2026, нет пары снапшотов"


def test_rows_contain_no_lead_ids_or_contacts(app_config: AppConfig) -> None:
    leads = [
        {**row, "lead_id": 987650 + index, "contact_phone_key": "k", "contact_email_key": "k"}
        for index, row in enumerate(LEADS)
    ]
    lead_frame = prepare_lead_frame(leads, app_config)

    text = "\n".join(
        markdown_lines(
            baseline_rows(lead_frame, {REPORT_DATE: lead_frame}, (), REPORT_DATE, app_config),
            REPORT_DATE,
            app_config,
        )
    )

    assert "98765" not in text
    assert "#" not in text.replace("# Baseline", "")
    assert "@" not in text


def test_markdown_round_trip(app_config: AppConfig) -> None:
    rows = rows_of(app_config)

    lines = markdown_lines(rows, REPORT_DATE, app_config)

    assert lines[0] == "# Baseline 29.09.2026"
    assert parse_baseline_lines(lines) == rows


BASE = [
    BaselineRow("A1.stand_by", "Stand BY", 150, "лидов", "на 29.09.2026"),
    BaselineRow("B1.scr.prev", "SCR месяца (m4)", 10.9, "%", "август 2026"),
    BaselineRow("B5.cycle_median.cur", "Цикл", 5.5, "дней", "сентябрь 2026"),
    BaselineRow("A9.gone", "Только в базе", 3, "лидов", "на 29.09.2026"),
    BaselineRow("D5.cpl", "CPL", None, "нет источника", "—"),
]
CURRENT = [
    BaselineRow("B1.scr.prev", "SCR месяца (m4)", 12.4, "%", "сентябрь 2026"),
    BaselineRow("A1.stand_by", "Stand BY", 120, "лидов", "на 31.10.2026"),
    BaselineRow("A0.new", "Только сейчас", 2, "лидов", "на 31.10.2026"),
    BaselineRow("B5.cycle_median.cur", "Цикл", 4.0, "дней", "октябрь 2026"),
    BaselineRow("D5.cpl", "CPL", None, "нет источника", "—"),
]


def test_compare_two_files_prints_delta_in_pp_for_percent() -> None:
    lines = comparison_lines(BASE, CURRENT)

    assert lines[2:5] == [
        "| A1.stand_by | Stand BY | 150 | 120 | -30 |",
        "| B1.scr.prev | SCR месяца (m4) | 10.9 | 12.4 | +1.5 п.п. |",
        "| B5.cycle_median.cur | Цикл | 5.5 | 4.0 | -1.5 |",
    ]


def test_compare_marks_ids_missing_on_one_side() -> None:
    lines = comparison_lines(BASE, CURRENT)

    assert lines[-2:] == [
        "| A9.gone | Только в базе | 3 | — | — |",
        "| A0.new | Только сейчас | — | 2 | — |",
    ]


def test_compare_keeps_no_source_rows_without_delta() -> None:
    lines = comparison_lines(BASE, CURRENT)

    assert "| D5.cpl | CPL | — | — | — |" in lines


def test_compare_reads_back_written_files(app_config: AppConfig) -> None:
    rows = rows_of(app_config)
    base = parse_baseline_lines(markdown_lines(rows, REPORT_DATE, app_config))

    lines = comparison_lines(base, base)

    assert "| A1.stand_by | Stand BY | 1 | 1 | +0 |" in lines
    assert len(lines) == len(rows) + 2


def test_source_lead_rows_add_up_to_all_leads(app_config: AppConfig) -> None:
    rare_source = [lead(600 + index, (2026, 9, 1), source_name="BIFE 2026") for index in range(2)]
    without_source = [lead(700, (2026, 9, 1), source_name=None)]
    leads = [*LEADS, *rare_source, *without_source]

    result = values(rows_of(app_config, leads))

    source_rows = {row_id: value for row_id, value in result.items() if row_id.startswith("D.")}
    assert source_rows["D.leads.none"] == 1
    assert sum(value for value in source_rows.values() if isinstance(value, int)) == len(leads)


@pytest.mark.parametrize(
    ("report_date", "previous_month_end"),
    [
        (date(2026, 10, 31), date(2026, 9, 30)),
        (date(2026, 12, 31), date(2026, 11, 30)),
        (date(2027, 1, 31), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2026, 12, 31)),
        # Понедельник: граница недели не двигает месяцы.
        (date(2026, 10, 5), date(2026, 9, 30)),
    ],
)
def test_current_and_previous_month_at_boundaries(
    report_date: date, previous_month_end: date
) -> None:
    assert month_analysis_dates(report_date) == (
        ("cur", report_date),
        ("prev", previous_month_end),
    )


def test_warning_unless_last_day_of_month() -> None:
    assert date_warning(date(2026, 9, 30)) is None
    assert date_warning(date(2027, 2, 28)) is None
    warning = date_warning(date(2026, 9, 29))
    assert warning is not None
    assert warning.startswith("Внимание: 29.09.2026 не последний день месяца.")


def test_compare_negative_percent_delta_and_value_missing_on_one_side() -> None:
    base = [
        BaselineRow("B1.scr.prev", "SCR месяца (m4)", 12.4, "%", "август 2026"),
        BaselineRow("B5.cycle_median.cur", "Цикл", 5.5, "дней", "сентябрь 2026"),
        BaselineRow("B6.cohort_30d.cur", "Когорта", None, "%", "сентябрь 2026"),
    ]
    current = [
        BaselineRow("B1.scr.prev", "SCR месяца (m4)", 10.9, "%", "сентябрь 2026"),
        BaselineRow("B5.cycle_median.cur", "Цикл", None, "дней", "октябрь 2026"),
        BaselineRow("B6.cohort_30d.cur", "Когорта", 8.0, "%", "октябрь 2026"),
    ]

    assert comparison_lines(base, current)[2:] == [
        "| B1.scr.prev | SCR месяца (m4) | 12.4 | 10.9 | -1.5 п.п. |",
        "| B5.cycle_median.cur | Цикл | 5.5 | — | — |",
        "| B6.cohort_30d.cur | Когорта | — | 8.0 | — |",
    ]


def test_compare_does_not_subtract_across_unit_change() -> None:
    base = [BaselineRow("A4.stale_offers", "Оферты", 2, "оферт", "на 29.09.2026")]
    current = [BaselineRow("A4.stale_offers", "Оферты", 3, "лидов", "на 31.10.2026")]

    assert comparison_lines(base, current)[2] == "| A4.stale_offers | Оферты | 2 | 3 | — |"


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (["# Чужой файл", "| a | b |"], "нет строки заголовка"),
        ([*TABLE_HEADER, "| A1.stand_by | Stand BY | 150 | лидов |"], "строка 3: ячеек 4"),
        ([*TABLE_HEADER, "| A1.stand_by | Stand BY | много | лидов | на 29.09 |"], "«много»"),
    ],
)
def test_foreign_or_broken_file_is_rejected(lines: list[str], message: str) -> None:
    with pytest.raises(BaselineFileError, match=message):
        parse_baseline_lines(lines)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BaselineFileError, match="файл не прочитан"):
        parse_baseline_file(tmp_path / "нет.md")
