from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.frame import prepare_lead_frame
from factories import BUCHAREST, make_snapshot_row

VISIT_AT = datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)
OWN_WINDOW_START = datetime(2026, 9, 24, 19, 0, tzinfo=BUCHAREST)
PHONE_KEY, EMAIL_KEY = "a" * 64, "b" * 64
SHOWROOM = {"source_name": "Showroom", "showroom": "București"}


def visit_flags(app_config: AppConfig, *rows: dict[str, Any]) -> pd.DataFrame:
    lead_frame = prepare_lead_frame(list(rows), app_config)
    return lead_frame.set_index("lead_id")[
        ["is_showroom_source", "is_showroom_revenire", "is_showroom_visit"]
    ]


def visit(lead_id: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        **{"lead_id": lead_id, "created_at": VISIT_AT, **SHOWROOM, **overrides}
    )


def earlier(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        lead_id=lead_id, source_name="WhatsApp", created_at=created_at, **overrides
    )


def flags_of(frame: pd.DataFrame, lead_id: int) -> tuple[bool, bool, bool]:
    source, revenire, visit_flag = frame.loc[lead_id]
    return bool(source), bool(revenire), bool(visit_flag)


def test_showroom_lead_without_earlier_contact_is_visit(app_config: AppConfig) -> None:
    frame = visit_flags(app_config, visit(1, contact_phone_key=PHONE_KEY))

    assert flags_of(frame, 1) == (True, False, True)


def test_phone_seen_before_own_window_makes_revenire(app_config: AppConfig) -> None:
    frame = visit_flags(
        app_config,
        earlier(1, VISIT_AT - timedelta(days=30), contact_phone_key=PHONE_KEY),
        visit(2, contact_phone_key=PHONE_KEY, contact_email_key=EMAIL_KEY),
    )

    assert flags_of(frame, 2) == (True, True, False)


def test_email_seen_before_own_window_makes_revenire(app_config: AppConfig) -> None:
    frame = visit_flags(
        app_config,
        earlier(1, VISIT_AT - timedelta(days=3), contact_email_key=EMAIL_KEY),
        visit(2, contact_phone_key=PHONE_KEY, contact_email_key=EMAIL_KEY),
    )

    assert flags_of(frame, 2) == (True, True, False)


def test_contact_from_same_window_keeps_visit(app_config: AppConfig) -> None:
    # Утром WhatsApp, вечером приход в шоурум: это один визит (ADR-006).
    frame = visit_flags(
        app_config,
        earlier(1, VISIT_AT - timedelta(hours=2), contact_phone_key=PHONE_KEY),
        visit(2, contact_phone_key=PHONE_KEY),
    )

    assert flags_of(frame, 2) == (True, False, True)


# 25.10.2026 переход на зимнее время: окно 25.10 начинается 24.10 в 19:00 по EEST,
# окно 26.10 начинается 25.10 в 19:00 по EET.
@pytest.mark.parametrize(
    "window_start",
    [
        OWN_WINDOW_START,
        datetime(2026, 10, 24, 19, 0, tzinfo=BUCHAREST),
        datetime(2026, 10, 25, 19, 0, tzinfo=BUCHAREST),
    ],
)
@pytest.mark.parametrize(("seconds_before_window", "is_revenire"), [(0, False), (1, True)])
def test_revenire_boundary_is_start_of_own_daily_window(
    app_config: AppConfig, window_start: datetime, seconds_before_window: int, is_revenire: bool
) -> None:
    earlier_at = window_start - timedelta(seconds=seconds_before_window)
    frame = visit_flags(
        app_config,
        earlier(1, earlier_at, contact_phone_key=PHONE_KEY),
        visit(2, created_at=window_start + timedelta(hours=15), contact_phone_key=PHONE_KEY),
    )

    assert flags_of(frame, 2) == (True, is_revenire, not is_revenire)


def test_same_contact_across_19_00_is_revenire(app_config: AppConfig) -> None:
    # Лид в 18:59 и Showroom-лид в 19:01 с тем же телефоном: второй в следующем окне, это
    # revenire, даже если это дубль одного прихода. Решение зафиксировано, вопрос П4 в
    # docs/owner-questions.md.
    frame = visit_flags(
        app_config,
        earlier(1, OWN_WINDOW_START - timedelta(minutes=1), contact_phone_key=PHONE_KEY),
        visit(2, created_at=OWN_WINDOW_START + timedelta(minutes=1), contact_phone_key=PHONE_KEY),
    )

    assert flags_of(frame, 2) == (True, True, False)


def test_partnership_showroom_lead_is_neither_visit_nor_revenire(app_config: AppConfig) -> None:
    frame = visit_flags(
        app_config,
        earlier(1, VISIT_AT - timedelta(days=30), contact_phone_key=PHONE_KEY),
        visit(2, category="PARTNERSHIP", contact_phone_key=PHONE_KEY),
        visit(3, category="PARTNERSHIP"),
    )

    assert flags_of(frame, 2) == (True, False, False)
    assert flags_of(frame, 3) == (True, False, False)


def test_lead_without_contact_keys_is_visit(app_config: AppConfig) -> None:
    frame = visit_flags(
        app_config,
        earlier(1, VISIT_AT - timedelta(days=30)),
        visit(2),
    )

    assert flags_of(frame, 2) == (True, False, True)


def test_other_source_is_never_visit_or_revenire(app_config: AppConfig) -> None:
    frame = visit_flags(
        app_config,
        earlier(1, VISIT_AT - timedelta(days=30), contact_phone_key=PHONE_KEY),
        visit(2, source_name="Site", contact_phone_key=PHONE_KEY),
    )

    assert flags_of(frame, 1) == (False, False, False)
    assert flags_of(frame, 2) == (False, False, False)
