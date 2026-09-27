from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, get_args

from digest.config import TimeSettings
from digest.metrics.daily import daily_window
from digest.metrics.kpi import Period
from digest.metrics.monthly import first_day_of_month, last_day_of_month

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


@dataclass(frozen=True)
class ChatDay:
    day: date


@dataclass(frozen=True)
class ChatMonth:
    first_day: date


ChatPeriodChoice = ChatPeriod | ChatDay | ChatMonth


def earliest_specific_day(first_snapshot_date: date, history_years: int) -> date:
    # Снапшот хранит и старые лиды, поэтому конкретная дата допустима и до первого снапшота.
    year = first_snapshot_date.year - history_years
    month = first_snapshot_date.month
    return date(year, month, min(first_snapshot_date.day, monthrange(year, month)[1]))


def period_days(period: ChatPeriodChoice, today: date) -> tuple[date, date]:
    if isinstance(period, ChatDay):
        return period.day, period.day
    if isinstance(period, ChatMonth):
        return period.first_day, last_day_of_month(period.first_day)
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


def chat_period_window(
    period: ChatPeriodChoice, today: date, time_settings: TimeSettings
) -> Period:
    # Период чата = ежедневные окна его дней подряд, как недельное и месячное окна отчётов
    # (docs/kpi-definitions.md, «Недельные окна», «Месячное окно»): закрытая неделя равна
    # weekly_window её воскресенья, закрытый и конкретный месяц равны month_window,
    # конкретный день равен daily_window.
    first_day, last_day = period_days(period, today)
    return Period(
        daily_window(first_day, time_settings).start, daily_window(last_day, time_settings).end
    )


def period_snapshot_date(
    period: ChatPeriodChoice, today: date, snapshot_dates: Sequence[date]
) -> date | None:
    # Закрытый период читается из снапшота своего последнего дня, по которому ушёл отчёт: в
    # последнем снапшоте когорта прошлой недели уже дозрела, а лиды со сменой статуса выпали из
    # потерь прошлого месяца (status_changed_at хранит только последнее изменение, CLAUDE.md).
    # Снапшота за этот день нет: первый успешный после него, подпись говорит о подмене
    # (docs/kpi-definitions.md, «Режим вопросов»). Конкретные день и месяц закрытые: сегодняшний
    # день и текущий месяц приходят сюда уже как azi и luna_curenta.
    if isinstance(period, str) and period not in CLOSED_PERIODS:
        return max(snapshot_dates)
    last_day = period_days(period, today)[1]
    return min((day for day in snapshot_dates if day >= last_day), default=None)
