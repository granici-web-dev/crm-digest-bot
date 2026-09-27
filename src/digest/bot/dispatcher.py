import logging
from collections.abc import Collection

from aiogram import Dispatcher
from aiogram.enums import ChatType
from aiogram.types import ErrorEvent

from digest.bot.chat_handlers import ChatDeps, chat_router
from digest.bot.settings_handlers import Clock, RescheduleReport, settings_router
from digest.delivery.ops import notify_ops
from digest.reports.runner import ReportDeps
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)

HANDLER_ERROR_TEXT = "Nu s-a putut aplica. Încercați din nou mai târziu."


async def report_handler_error(event: ErrorEvent, deps: ReportDeps) -> None:
    message = event.update.message
    source = (
        "Chat" if message is not None and message.chat.type != ChatType.PRIVATE else "/settings"
    )
    logger.error(
        "bot handler failed", extra={"source": source, "error": describe_error(event.exception)}
    )
    await notify_ops(deps.ops, f"{source}: хендлер упал: {describe_error(event.exception)}.")
    callback = event.update.callback_query
    if callback is not None:
        await callback.answer(HANDLER_ERROR_TEXT, show_alert=True)


def bot_dispatcher(
    deps: ReportDeps,
    reschedule: RescheduleReport,
    admin_ids: Collection[int],
    clock: Clock,
    chat: ChatDeps,
) -> Dispatcher:
    dispatcher = Dispatcher(deps=deps, reschedule=reschedule, clock=clock, chat=chat)
    dispatcher.errors.register(report_handler_error)
    dispatcher.include_router(settings_router(admin_ids))
    dispatcher.include_router(chat_router(chat.chat_ids))
    return dispatcher
