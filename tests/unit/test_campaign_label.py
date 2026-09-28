import pytest

from digest.reports.render import campaign_label

HIDDEN = "campanie ascunsă"


def test_line_breaks_and_control_characters_become_spaces() -> None:
    assert campaign_label("BZA\n03\r\tx\x00y z", 40, HIDDEN) == "BZA 03  x y z"


def test_long_campaign_is_cut_to_max_length_with_ellipsis() -> None:
    label = campaign_label("a" * 5000, 40, HIDDEN)

    assert label == "a" * 39 + "…"
    assert len(label) == 40


def test_campaign_at_max_length_is_kept_whole() -> None:
    assert campaign_label("a" * 40, 40, HIDDEN) == "a" * 40


@pytest.mark.parametrize(
    "value",
    [
        "Client 0712345678",
        "+40700000001",
        "client1@example.com",
        "x@y.ro promo",
        "0712 345 678",
        "+40 (712) 345-678",
        "07.12.345.678",
        "0712\u200b345\u200b678",
    ],
)
def test_phone_or_email_like_campaign_is_hidden(value: str) -> None:
    assert campaign_label(value, 40, HIDDEN) == HIDDEN


@pytest.mark.parametrize("value", ["BZA_Cluj_Website_Leads 03", "Promo 2026", "Vara@Sofa"])
def test_ordinary_campaign_is_shown_as_is(value: str) -> None:
    assert campaign_label(value, 40, HIDDEN) == value


def test_format_characters_are_removed() -> None:
    assert campaign_label("BZA\u200b_\u202eCluj\u2066", 40, HIDDEN) == "BZA_Cluj"
