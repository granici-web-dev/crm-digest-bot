from datetime import date, timedelta
from typing import Literal, get_args

from digest.config import TimeSettings
from digest.metrics.daily import daily_window
from digest.metrics.kpi import Period
from digest.metrics.monthly import first_day_of_month

ChatPeriod = Literal[
    "azi",
    "ieri",
    "saptamana_curenta",
    "saptamana_trecuta",
    "luna_curenta",
    "luna_trecuta",
    "ultimele_30_zile",
]
CHAT_PERIODS: tuple[ChatPeriod, ...] = get_args(ChatPeriod)
CLOSED_PERIODS: frozenset[ChatPeriod] = frozenset({"ieri", "saptamana_trecuta", "luna_trecuta"})
LAST_DAYS_PERIOD_LENGTH = 30


def period_days(period: ChatPeriod, today: date) -> tuple[date, date]:
    this_monday = today - timedelta(days=today.weekday())
    if period == "azi":
        return today, today
    if period == "ieri":
        return today - timedelta(days=1), today - timedelta(days=1)
    if period == "saptamana_curenta":
        return this_monday, today
    if period == "saptamana_trecuta":
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if period == "luna_curenta":
        return first_day_of_month(today), today
    if period == "luna_trecuta":
        last_day = first_day_of_month(today) - timedelta(days=1)
        return first_day_of_month(last_day), last_day
    return today - timedelta(days=LAST_DAYS_PERIOD_LENGTH - 1), today


def named_period_window(period: ChatPeriod, today: date, time_settings: TimeSettings) -> Period:
    # Период чата = ежедневные окна его дней подряд, как недельное и месячное окна отчётов
    # (docs/kpi-definitions.md, «Недельные окна», «Месячное окно»): закрытая неделя равна
    # weekly_window её воскресенья, закрытый месяц равен month_window.
    first_day, last_day = period_days(period, today)
    return Period(
        daily_window(first_day, time_settings).start, daily_window(last_day, time_settings).end
    )


def period_snapshot_date(period: ChatPeriod, today: date, latest_snapshot_date: date) -> date:
    # Закрытый период читается из снапшота своего последнего дня, по которому ушёл отчёт: в
    # последнем снапшоте когорта прошлой недели уже дозрела, а лиды со сменой статуса выпали из
    # потерь прошлого месяца (status_changed_at хранит только последнее изменение, CLAUDE.md).
    if period in CLOSED_PERIODS:
        return period_days(period, today)[1]
    return latest_snapshot_date
