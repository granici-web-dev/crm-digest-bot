import pytest

from digest.delivery.telegram import (
    TELEGRAM_MESSAGE_LIMIT,
    MessageLineTooLong,
    split_message,
    telegram_length,
)


def test_text_at_limit_stays_one_message() -> None:
    text = "a" * 4000 + "\n" + "b" * 95

    assert split_message(text) == [text]


def test_text_over_limit_splits_on_line_boundary() -> None:
    first_line, second_line = "a" * 4000, "b" * 96

    parts = split_message(f"{first_line}\n{second_line}")

    assert parts == [first_line, second_line]


def test_parts_join_back_to_original_text() -> None:
    text = "\n".join(f"<b>Sofabelle {index}:</b> linia" for index in range(600))

    parts = split_message(text)

    assert len(parts) > 1
    assert all(telegram_length(part) <= TELEGRAM_MESSAGE_LIMIT for part in parts)
    assert "\n".join(parts) == text


def test_emoji_counts_as_two_utf16_units() -> None:
    text = "😀" * 2048 + "\n" + "x"

    assert split_message(text) == ["😀" * 2048, "x"]


def test_line_longer_than_limit_is_an_error() -> None:
    with pytest.raises(MessageLineTooLong):
        split_message("a" * 4097)
