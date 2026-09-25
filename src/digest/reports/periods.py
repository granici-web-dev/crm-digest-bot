from datetime import datetime, time, timedelta
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from digest.metrics.kpi import Period

ReportLevel = Literal["daily", "weekly", "monthly", "yearly"]
REPORT_LEVELS: tuple[ReportLevel, ...] = get_args(ReportLevel)


def report_period(level: ReportLevel, now: datetime, timezone: ZoneInfo, daily_end: time) -> Period:
    local_now = now.astimezone(timezone)
    today = local_now.date()
    if level == "daily":
        end_date = today if local_now.time() >= daily_end else today - timedelta(days=1)
        end = datetime.combine(end_date, daily_end, tzinfo=timezone)
        start = datetime.combine(end_date - timedelta(days=1), daily_end, tzinfo=timezone)
        return Period(start, end)
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
