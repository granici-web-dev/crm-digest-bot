from datetime import UTC, datetime

from digest.config import AppConfig
from digest.metrics.frame import prepare_lead_frame, unknown_manager_ids
from digest.snapshot import categorize
from factories import make_snapshot_row

TEST_ACCOUNT_ID = 4


def test_test_account_leads_are_excluded(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1),
        make_snapshot_row(lead_id=2, assigned_to_id=TEST_ACCOUNT_ID),
    ]

    lead_frame = prepare_lead_frame(rows, app_config)

    assert lead_frame["lead_id"].tolist() == [1]


def test_unassigned_lead_is_kept(app_config: AppConfig) -> None:
    lead_frame = prepare_lead_frame([make_snapshot_row(assigned_to_id=None)], app_config)

    assert len(lead_frame) == 1


def test_null_ofertat_is_not_an_offer(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(lead_id=1, ofertat=None), make_snapshot_row(lead_id=2, ofertat=True)]

    lead_frame = prepare_lead_frame(rows, app_config)

    assert lead_frame["is_ofertat"].tolist() == [False, True]


def test_spam_and_irelevant_are_both_irrelevant(app_config: AppConfig) -> None:
    rows = []
    for lead_id, status in enumerate(["SPAM", "IRELEVANT", "BUGET"]):
        category = categorize(status, app_config.status_mapping)
        rows.append(
            make_snapshot_row(
                lead_id=lead_id,
                category=category.category,
                loss_reason=category.loss_reason,
                status_name=status,
            )
        )

    lead_frame = prepare_lead_frame(rows, app_config)

    assert lead_frame["is_irelevant"].tolist() == [True, True, False]


def test_showroom_visit_comes_from_source_name(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(lead_id=1, source_name="Showroom"), make_snapshot_row(lead_id=2)]

    lead_frame = prepare_lead_frame(rows, app_config)

    assert lead_frame["is_showroom_visit"].tolist() == [True, False]


def test_timestamps_are_converted_to_bucharest(app_config: AppConfig) -> None:
    row = make_snapshot_row(created_at=datetime(2026, 9, 23, 16, 30, tzinfo=UTC))

    lead_frame = prepare_lead_frame([row], app_config)

    assert lead_frame["created_at"][0].hour == 19


def test_unknown_manager_id_is_reported(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1, assigned_to_id=12),
        make_snapshot_row(lead_id=2, assigned_to_id=77),
    ]

    assert unknown_manager_ids(prepare_lead_frame(rows, app_config), app_config) == {77}


def test_unassigned_lead_is_not_an_unknown_manager(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(assigned_to_id=None)]

    assert unknown_manager_ids(prepare_lead_frame(rows, app_config), app_config) == set()
