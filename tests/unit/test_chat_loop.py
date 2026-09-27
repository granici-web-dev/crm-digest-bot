from datetime import date, datetime

import anthropic
import pandas as pd
import pytest

from digest.chat.loop import (
    CANNOT_ANSWER_NOW_TEXT,
    MAX_ANSWER_TOKENS,
    UNVERIFIED_NUMBERS_TEXT,
    answer_question,
)
from digest.chat.tools import ToolData
from digest.config import AppConfig
from digest.metrics.frame import prepare_lead_frame
from digest.reports.render import render
from factories import BUCHAREST, make_snapshot_row
from fakes import scripted_anthropic, text_message, tool_use_message

TODAY = date(2026, 9, 23)
MODEL = "claude-sonnet-5"
FUNNEL_THIS_WEEK = ("funnel", {"period": "saptamana_curenta", "showroom": "București"})


def lead_frame(config: AppConfig) -> pd.DataFrame:
    rows = [
        make_snapshot_row(
            lead_id=lead_id,
            created_at=datetime(2026, 9, 22, 11, tzinfo=BUCHAREST),
            last_contact_at=datetime(2026, 9, 22, 11, tzinfo=BUCHAREST),
            ofertat=lead_id == 1,
        )
        for lead_id in (1, 2, 3)
    ]
    return prepare_lead_frame(rows, config)


def tool_data(config: AppConfig, snapshot_dates: tuple[date, ...] = (TODAY,)) -> ToolData:
    frame = lead_frame(config)

    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return frame

    return ToolData(TODAY, snapshot_dates, load_frame, config)


async def test_question_routes_to_tool_with_arguments(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK),
        text_message("Săptămâna aceasta în București: 3 lead-uri și 1 ofertă."),
    )

    answer = await answer_question(
        "Câte lead-uri în București săptămâna asta?", api.client, MODEL, tool_data(app_config)
    )

    assert answer.status == "answered"
    assert [(call.name, call.arguments) for call in answer.tool_calls] == [FUNNEL_THIS_WEEK]
    first_request = api.requests[0]
    assert first_request["model"] == MODEL
    assert first_request["max_tokens"] == MAX_ANSWER_TOKENS
    assert first_request["tool_choice"] == {"type": "auto"}
    assert first_request["thinking"] == {"type": "disabled"}
    tool_result = api.requests[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_0"
    assert '"leads": 3' in tool_result["content"]
    assert (answer.input_tokens, answer.output_tokens) == (200, 40)
    assert answer.snapshot_dates == (TODAY,)


async def test_signature_names_period_and_function(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK), text_message("Au fost 3 lead-uri.")
    )

    answer = await answer_question("Câte lead-uri?", api.client, MODEL, tool_data(app_config))

    assert answer.text == (
        "Au fost 3 lead-uri.\n\n"
        "<i>Perioada: 21.09–23.09.2026 (saptamana_curenta) · funnel(showroom=București)</i>"
    )


async def test_stale_snapshot_is_stated_in_signature(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK), text_message("Au fost 3 lead-uri.")
    )

    answer = await answer_question(
        "Câte lead-uri?", api.client, MODEL, tool_data(app_config, (date(2026, 9, 22),))
    )

    assert answer.text.endswith("<i>Date din snapshotul din 22.09.2026</i>")


async def test_numbers_without_tool_call_are_not_sent(app_config: AppConfig) -> None:
    api = scripted_anthropic(text_message("Cred că ați avut vreo 40 de lead-uri."))

    answer = await answer_question("Câte lead-uri?", api.client, MODEL, tool_data(app_config))

    assert answer.status == "blocked_numbers"
    assert answer.text == render("chat_refusal")
    assert "40" not in answer.text


async def test_out_of_scope_answer_without_numbers_is_sent(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        text_message("Nu pot face prognoze. Vă pot spune câte lead-uri ați avut săptămâna asta.")
    )

    answer = await answer_question(
        "Câte vânzări vom avea luna viitoare?", api.client, MODEL, tool_data(app_config)
    )

    assert answer.status == "no_tool"
    assert answer.text.startswith("Nu pot face prognoze.")
    assert "<i>" not in answer.text


async def test_number_absent_from_tool_results_is_blocked(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK), text_message("Au fost 3 lead-uri, cu 12% mai mult.")
    )

    answer = await answer_question("Câte lead-uri?", api.client, MODEL, tool_data(app_config))

    assert answer.status == "unverified_numbers"
    assert answer.text == UNVERIFIED_NUMBERS_TEXT
    assert answer.unverified_number == "12"


async def test_numbers_from_question_and_period_dates_are_allowed(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK),
        text_message("Între 21 și 23 septembrie 2026 au fost 3 lead-uri, nu 5."),
    )

    answer = await answer_question("Au fost 5 lead-uri?", api.client, MODEL, tool_data(app_config))

    assert answer.status == "answered"


async def test_fourth_tool_call_is_refused(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK, FUNNEL_THIS_WEEK),
        tool_use_message(FUNNEL_THIS_WEEK, FUNNEL_THIS_WEEK),
        text_message("Au fost 3 lead-uri."),
    )

    answer = await answer_question("Câte lead-uri?", api.client, MODEL, tool_data(app_config))

    assert len(answer.tool_calls) == 3
    refused = api.requests[2]["messages"][-1]["content"][1]
    assert refused["is_error"] is True
    assert api.requests[2]["tool_choice"] == {"type": "none"}


async def test_invalid_tool_arguments_go_back_to_model_as_error(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("funnel", {"period": "saptamana_curenta", "showroom": "Iași"})),
        text_message("Nu am date pentru acest showroom."),
    )

    answer = await answer_question("Iași?", api.client, MODEL, tool_data(app_config))

    assert api.requests[1]["messages"][-1]["content"][0]["is_error"] is True
    assert answer.status == "answered"
    assert answer.text == "Nu am date pentru acest showroom."


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
async def test_truncated_or_refused_response_is_failure(
    app_config: AppConfig, stop_reason: str
) -> None:
    api = scripted_anthropic(text_message("Au fost", stop_reason=stop_reason))

    answer = await answer_question("Câte lead-uri?", api.client, MODEL, tool_data(app_config))

    assert answer.status == stop_reason
    assert answer.text == CANNOT_ANSWER_NOW_TEXT


async def test_api_error_propagates(app_config: AppConfig) -> None:
    api = scripted_anthropic(529)

    with pytest.raises(anthropic.APIError):
        await answer_question("Câte lead-uri?", api.client, MODEL, tool_data(app_config))


async def test_model_text_is_html_escaped(app_config: AppConfig) -> None:
    api = scripted_anthropic(text_message("Nu știu <b>asta</b>."))

    answer = await answer_question("?", api.client, MODEL, tool_data(app_config))

    assert answer.text == "Nu știu &lt;b&gt;asta&lt;/b&gt;."


async def test_substituted_snapshot_of_closed_period_is_disclosed(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("funnel", {"period": "ieri", "showroom": "toate"})),
        text_message("Ieri au fost 3 lead-uri."),
    )

    answer = await answer_question(
        "Câte lead-uri ieri?", api.client, MODEL, tool_data(app_config, (TODAY,))
    )

    assert answer.status == "answered"
    assert answer.text.endswith(
        "<i>Date din snapshotul din 23.09.2026 (nu există snapshot pentru 22.09.2026)</i>"
    )
