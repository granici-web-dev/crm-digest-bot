from datetime import datetime, time, timedelta
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from digest.config import TimeSettings
from digest.metrics.daily import daily_window
from digest.metrics.kpi import Period

ReportLevel = Literal["daily", "weekly", "monthly", "yearly"]
REPORT_LEVELS: tuple[ReportLevel, ...] = get_args(ReportLevel)


def report_period(level: ReportLevel, now: datetime, time_settings: TimeSettings) -> Period:
    timezone = ZoneInfo(time_settings.timezone)
    local_now = now.astimezone(timezone)
    today = local_now.date()
    if level == "daily":
        after_window_end = local_now.time() >= time_settings.daily_window_end
        return daily_window(today if after_window_end else today - timedelta(days=1), time_settings)
    if level == "weekly":
        this_monday = today - timedelta(days=today.weekday())
        return Period(
            datetime.combine(this_monday - timedelta(days=7), time(), tzinfo=timezone),
            datetime.combine(this_monday, time(), tzinfo=timezone),
        )
    if level == "monthly":
        this_month = today.replace(day=1)
        previous_month = (this_month - timedelta(days=1)).replace(day=1)
        return Period(
            datetime.combine(previous_month, time(), tzinfo=timezone),
            datetime.combine(this_month, time(), tzinfo=timezone),
        )
    this_year = today.replace(month=1, day=1)
    return Period(
        datetime.combine(this_year.replace(year=this_year.year - 1), time(), tzinfo=timezone),
        datetime.combine(this_year, time(), tzinfo=timezone),
    )
