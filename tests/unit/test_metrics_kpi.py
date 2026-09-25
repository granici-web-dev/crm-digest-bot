from dataclasses import asdict
from datetime import date, datetime

import pytest

from digest.config import AppConfig, Manager, ManagerRoster
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period, kpis_from, lead_counts_by_manager
from factories import BUCHAREST, etalon_lead_rows, load_etalon

ETALON_PERIOD = Period(
    start=datetime(2026, 5, 1, tzinfo=BUCHAREST), end=datetime(2026, 6, 17, tzinfo=BUCHAREST)
)
ETALON_ANALYSIS_DATE = date(2026, 6, 27)


def test_kpi_matches_etalon(app_config: AppConfig) -> None:
    etalon = load_etalon()
    roster = ManagerRoster(
        managers=[
            Manager(id=manager_id, name=name, showroom=None, active=True)
            for name, manager_id in etalon["managers"].items()
        ]
    )
    config = app_config.model_copy(update={"managers": roster})
    lead_frame = prepare_lead_frame(etalon_lead_rows(etalon, config.status_mapping), config)

    counts_by_manager = lead_counts_by_manager(
        lead_frame, ETALON_PERIOD, ETALON_ANALYSIS_DATE, config
    )

    for name, manager_id in etalon["managers"].items():
        expected = etalon["expected_by_agent"][name]
        assert asdict(counts_by_manager[manager_id]) == expected["counts"], name
        assert asdict(kpis_from(counts_by_manager[manager_id])) == pytest.approx(
            expected["kpi"], abs=1e-9
        ), name
