import pytest

from digest.contact_keys import email_contact_key, phone_contact_key

SECRET = b"k" * 32


@pytest.mark.parametrize(
    "phone", ["+40 712 345 678", "0712345678", "0712 345 678", "+40712345678", "0040-712-345-678"]
)
def test_phone_formats_of_one_number_give_one_key(phone: str) -> None:
    assert phone_contact_key(phone, SECRET) == phone_contact_key("712345678", SECRET)


def test_different_phone_numbers_give_different_keys() -> None:
    assert phone_contact_key("0712345678", SECRET) != phone_contact_key("0712345679", SECRET)


@pytest.mark.parametrize("phone", ["123", "", "   ", "71234567", None, 712345678])
def test_phone_shorter_than_nine_digits_or_not_text_has_no_key(phone: object) -> None:
    assert phone_contact_key(phone, SECRET) is None


def test_email_is_trimmed_and_lowercased() -> None:
    assert email_contact_key("  Client3@Example.COM ", SECRET) == email_contact_key(
        "client3@example.com", SECRET
    )


@pytest.mark.parametrize("email", ["", "   ", None, 5])
def test_empty_or_not_text_email_has_no_key(email: object) -> None:
    assert email_contact_key(email, SECRET) is None


def test_other_secret_gives_other_key() -> None:
    assert phone_contact_key("0712345678", SECRET) != phone_contact_key("0712345678", b"x" * 32)


def test_key_does_not_contain_the_contact() -> None:
    key = phone_contact_key("0712345678", SECRET)

    assert key is not None
    assert "712345678" not in key
    assert len(key) == 64
