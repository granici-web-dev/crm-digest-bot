import logging
from collections.abc import Collection

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

from digest.bot.settings_handlers import Clock, RescheduleReport, settings_router
from digest.delivery.ops import notify_ops
from digest.reports.runner import ReportDeps
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)

HANDLER_ERROR_TEXT = "Nu s-a putut aplica. Încercați din nou mai târziu."


async def report_handler_error(event: ErrorEvent, deps: ReportDeps) -> None:
    logger.error("settings handler failed", extra={"error": describe_error(event.exception)})
    await notify_ops(deps.ops, f"/settings: хендлер упал: {describe_error(event.exception)}.")
    callback = event.update.callback_query
    if callback is not None:
        await callback.answer(HANDLER_ERROR_TEXT, show_alert=True)


def bot_dispatcher(
    deps: ReportDeps, reschedule: RescheduleReport, admin_ids: Collection[int], clock: Clock
) -> Dispatcher:
    dispatcher = Dispatcher(deps=deps, reschedule=reschedule, clock=clock)
    dispatcher.errors.register(report_handler_error)
    dispatcher.include_router(settings_router(admin_ids))
    return dispatcher
