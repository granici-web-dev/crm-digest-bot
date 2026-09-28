from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from digest.config import TimeSettings
from digest.metrics.kpi import Period


def late_snapshot_due(now: datetime, time_settings: TimeSettings) -> bool:
    # До штатного повтора снапшот снимет он сам; две выгрузки за дату гасят друг друга.
    local_now = now.astimezone(ZoneInfo(time_settings.timezone))
    return local_now >= time_settings.snapshot_retry_at(local_now.date())


def report_catch_up_due(
    cron: str, period: Period, now: datetime, catch_up_days: int, timezone: ZoneInfo
) -> bool:
    # Срабатывание, которое выпустило бы отчёт за этот период, идёт первым после его конца.
    scheduled_at: datetime | None = CronTrigger.from_crontab(
        cron, timezone=timezone
    ).get_next_fire_time(None, period.end)
    if scheduled_at is None or scheduled_at > now:
        return False
    return (now.astimezone(timezone).date() - scheduled_at.date()).days <= catch_up_days


def missed_snapshot_alert(missed_dates: list[date]) -> str:
    if len(missed_dates) == 1:
        return f"Снапшот за {missed_dates[0]:%d.%m.%Y} пропущен, данные дня не восстановить."
    listed = ", ".join(f"{missed_date:%d.%m.%Y}" for missed_date in missed_dates)
    return f"Снапшоты за {listed} пропущены, данные этих дней не восстановить."
