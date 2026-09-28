import hashlib
import hmac
import re

PHONE_KEY_DIGITS = 9
NON_DIGITS = re.compile(r"[^0-9]")


def contact_key(normalized_contact: str, secret: bytes) -> str:
    # HMAC, а не голый SHA-256: 10⁹ номеров перебираются за минуты, без секрета ключ бесполезен.
    return hmac.new(secret, normalized_contact.encode(), hashlib.sha256).hexdigest()


def phone_contact_key(phone: str | None, secret: bytes) -> str | None:
    if phone is None:
        return None
    digits = NON_DIGITS.sub("", phone)
    # +40 712 345 678, 0712 345 678 и 712345678 это один номер: общие у них последние 9 цифр.
    # Короче 9 цифр это не номер, а мусор, который совпал бы с другим таким же мусором.
    if len(digits) < PHONE_KEY_DIGITS:
        return None
    return contact_key(digits[-PHONE_KEY_DIGITS:], secret)


def email_contact_key(email: str | None, secret: bytes) -> str | None:
    if email is None:
        return None
    normalized_email = email.strip().lower()
    if not normalized_email:
        return None
    return contact_key(normalized_email, secret)
