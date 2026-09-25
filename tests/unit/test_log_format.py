import json
import logging
import sys

from digest.log_format import JsonLogFormatter


def record_with(**overrides: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "digest.test", logging.ERROR, __file__, 1, "report failed", None, None
    )
    record.__dict__.update(overrides)
    return record


def test_extra_fields_are_written_as_json_keys() -> None:
    line = JsonLogFormatter().format(record_with(level="daily", chat_id=-1001))

    entry = json.loads(line)
    assert entry["message"] == "report failed"
    assert (entry["severity"], entry["level"]) == ("ERROR", "daily")
    assert (entry["chat_id"], entry["logger"]) == (-1001, "digest.test")


def test_exception_is_written_without_its_message() -> None:
    client_phone = "+40700000001"
    try:
        raise ValueError(f"lead {client_phone}")
    except ValueError:
        record = record_with(exc_info=sys.exc_info())

    line = JsonLogFormatter().format(record)

    assert client_phone not in line
    entry = json.loads(line)
    assert entry["exception_type"] == "ValueError"
    assert "test_exception_is_written_without_its_message" in entry["traceback"]
