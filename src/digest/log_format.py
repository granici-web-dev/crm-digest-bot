import json
import logging
import traceback
from datetime import UTC, datetime

STANDARD_RECORD_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Ключ level в extra раннера это уровень отчёта, поэтому уровень лога пишем как severity.
        entry = {
            **{
                key: value
                for key, value in vars(record).items()
                if key not in STANDARD_RECORD_FIELDS
            },
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        exception_type, _exception, exception_traceback = record.exc_info or (None, None, None)
        if exception_type is not None:
            # Текст исключения может содержать строки raw с данными клиента (см.
            # snapshot.describe_error): пишем тип и стек без сообщения.
            entry["exception_type"] = exception_type.__name__
            entry["traceback"] = "".join(traceback.format_tb(exception_traceback))
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
