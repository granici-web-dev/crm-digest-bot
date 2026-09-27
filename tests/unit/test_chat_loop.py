import json
from datetime import date, datetime

import anthropic
import pandas as pd
import pytest

from digest.chat.loop import (
    CANNOT_ANSWER_NOW_TEXT,
    MAX_ANSWER_TOKENS,
    UNVERIFIED_NUMBERS_TEXT,
    PreviousExchange,
    allowed_numbers,
    answer_question,
    first_unverified_number,
)
from digest.chat.tools import ToolData
from digest.config import AppConfig
from digest.metrics.frame import prepare_lead_frame
from digest.reports.render import render
from factories import BUCHAREST, make_lead_links, make_snapshot_row
from fakes import scripted_anthropic, text_message, tool_use_message

TODAY = date(2026, 9, 23)
MODEL = "claude-sonnet-5"
FUNNEL_THIS_WEEK = ("funnel", {"period": "saptamana_curenta", "showroom": "București"})


def lead_frame(config: AppConfig, lead_ids: tuple[int, ...] = (1, 2, 3)) -> pd.DataFrame:
    rows = [
        make_snapshot_row(
            lead_id=lead_id,
            created_at=datetime(2026, 9, 22, 11, tzinfo=BUCHAREST),
            last_contact_at=datetime(2026, 9, 22, 11, tzinfo=BUCHAREST),
            ofertat=lead_id == 1,
        )
        for lead_id in lead_ids
    ]
    return prepare_lead_frame(rows, config)


def tool_data(
    config: AppConfig,
    snapshot_dates: tuple[date, ...] = (TODAY,),
    lead_ids: tuple[int, ...] = (1, 2, 3),
    today: date = TODAY,
) -> ToolData:
    frame = lead_frame(config, lead_ids)

    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return frame

    return ToolData(
        today, snapshot_dates, load_frame, config, make_lead_links(config.status_mapping)
    )


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
    assert answer.unverified_number == "12%"


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
    assert answer.status == "no_tool"
    assert answer.text == "Nu am date pentru acest showroom."


async def test_numbers_after_only_failed_tool_calls_are_not_sent(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("funnel", {"period": "saptamana_curenta", "showroom": "Iași"})),
        text_message("În Iași au fost 3 lead-uri."),
    )

    answer = await answer_question("Iași?", api.client, MODEL, tool_data(app_config))

    assert answer.status == "blocked_numbers"
    assert answer.text == render("chat_refusal")


async def test_no_data_answer_may_repeat_the_dates_from_the_tool(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(FUNNEL_THIS_WEEK),
        text_message(
            "Nu există încă date pentru 21.09–23.09.2026, ultimul snapshot e din 20.09.2026."
        ),
    )

    answer = await answer_question(
        "Câte lead-uri?",
        api.client,
        MODEL,
        tool_data(app_config, snapshot_dates=(date(2026, 9, 20),)),
    )

    assert answer.status == "answered"


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


TWELVE_LEAD_IDS = tuple(range(900_001, 900_013))


async def test_links_line_follows_signature_and_is_capped_at_ten(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("untouched_leads", {})),
        text_message("Sunt 12 lead-uri neatinse."),
    )

    answer = await answer_question(
        "Ce lead-uri sunt neatinse?",
        api.client,
        MODEL,
        tool_data(app_config, lead_ids=TWELVE_LEAD_IDS),
    )

    assert answer.status == "answered"
    signature, links = answer.text.split("\n")[-2:]
    assert signature == "<i>Snapshot: 23.09.2026 · untouched_leads()</i>"
    assert links.startswith("Lead-uri: <a href=")
    assert links.count("<a href=") == 10
    assert links.endswith("#900010</a> și încă 2")


async def test_number_guard_ignores_lead_numbers_in_links_line(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("untouched_leads", {})), text_message("Sunt 12 lead-uri neatinse.")
    )

    answer = await answer_question(
        "?", api.client, MODEL, tool_data(app_config, lead_ids=TWELVE_LEAD_IDS)
    )

    assert answer.status == "answered"
    assert "#900001" in answer.text


async def test_lead_number_written_by_model_is_blocked(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("untouched_leads", {})), text_message("Cel mai vechi este #900001.")
    )

    answer = await answer_question(
        "?", api.client, MODEL, tool_data(app_config, lead_ids=TWELVE_LEAD_IDS)
    )

    assert answer.status == "unverified_numbers"
    assert answer.unverified_number == "900001"


async def test_model_request_never_contains_lead_ids(app_config: AppConfig) -> None:
    api = scripted_anthropic(
        tool_use_message(("untouched_leads", {}), ("overdue_followups", {"manager": "toti"})),
        text_message("Sunt 12 lead-uri neatinse."),
    )

    await answer_question("?", api.client, MODEL, tool_data(app_config, lead_ids=TWELVE_LEAD_IDS))

    sent = json.dumps(api.requests)
    assert not any(str(lead_id) in sent for lead_id in TWELVE_LEAD_IDS)


async def test_system_prompt_states_today_in_bucharest(app_config: AppConfig) -> None:
    api = scripted_anthropic(text_message("Bună ziua."))

    await answer_question("Salut", api.client, MODEL, tool_data(app_config))

    assert "Astăzi este 23.09.2026" in api.requests[0]["system"]


async def test_question_with_day_routes_to_funnel_with_that_day(app_config: AppConfig) -> None:
    funnel_on_day = ("funnel", {"period": {"day": "2026-09-22"}, "showroom": "toate"})
    api = scripted_anthropic(
        tool_use_message(funnel_on_day), text_message("Pe 22.09 au fost 3 lead-uri.")
    )

    answer = await answer_question(
        "Câte lead-uri am avut 22.09?",
        api.client,
        MODEL,
        tool_data(app_config, (date(2026, 9, 22), TODAY)),
    )

    assert answer.status == "answered"
    assert [(call.name, call.arguments) for call in answer.tool_calls] == [funnel_on_day]
    assert '"leads": 3' in api.requests[1]["messages"][-1]["content"][0]["content"]
    assert answer.text == "Pe 22.09 au fost 3 lead-uri.\n\n<i>Perioada: 22.09.2026 · funnel()</i>"


async def test_month_without_year_resolves_to_last_august(app_config: AppConfig) -> None:
    # Фейк не проверяет выбор года живой моделью: он проверяет, что дата в промпте, а месяц
    # 2026-08 даёт окно и подпись августа. Живая проверка в тестовой группе (deploy.md §4).
    august = ("funnel", {"period": {"year": 2026, "month": 8}, "showroom": "toate"})
    api = scripted_anthropic(tool_use_message(august), text_message("În august: 0 lead-uri."))

    answer = await answer_question(
        "Câte lead-uri în august?",
        api.client,
        MODEL,
        tool_data(app_config, (date(2026, 8, 31), date(2026, 9, 27)), today=date(2026, 9, 27)),
    )

    assert "Astăzi este 27.09.2026" in api.requests[0]["system"]
    tool_result = json.loads(api.requests[1]["messages"][-1]["content"][0]["content"])
    assert (tool_result["period"], tool_result["days"]) == ("august 2026", "01.08–31.08.2026")
    assert tool_result["snapshot_date"] == "31.08.2026"
    assert answer.text.endswith("<i>Perioada: august 2026 · funnel()</i>")


BUCURESTI_EARLIER = PreviousExchange(
    41,
    "câte lead-uri am avut săptămâna aceasta în București?",
    (FUNNEL_THIS_WEEK,),
)
FUNNEL_CLUJ = ("funnel", {"period": "saptamana_curenta", "showroom": "Cluj"})


async def test_previous_exchange_precedes_question_in_one_user_turn(app_config: AppConfig) -> None:
    api = scripted_anthropic(tool_use_message(FUNNEL_CLUJ), text_message("În Cluj: 0 lead-uri."))

    answer = await answer_question(
        "Dar Cluj?", api.client, MODEL, tool_data(app_config), BUCURESTI_EARLIER
    )

    [first_turn] = api.requests[0]["messages"]
    context, question = first_turn["content"]
    assert first_turn["role"] == "user"
    assert question == {"type": "text", "text": "Dar Cluj?"}
    assert "«câte lead-uri am avut săptămâna aceasta în București?»" in context["text"]
    assert 'funnel {"period": "saptamana_curenta", "showroom": "București"}' in context["text"]
    assert answer.status == "answered"
    assert [(call.name, call.arguments) for call in answer.tool_calls] == [FUNNEL_CLUJ]


async def test_number_from_previous_answer_is_blocked(app_config: AppConfig) -> None:
    # Прошлый ответ был «7 lead-uri»; текущий вызов по Cluj семёрку не содержит.
    api = scripted_anthropic(
        tool_use_message(FUNNEL_CLUJ), text_message("În Cluj 0 lead-uri, în București 7.")
    )

    answer = await answer_question(
        "Dar Cluj?", api.client, MODEL, tool_data(app_config), BUCURESTI_EARLIER
    )

    assert answer.status == "unverified_numbers"
    assert answer.unverified_number == "7"
    assert answer.text == UNVERIFIED_NUMBERS_TEXT


SIGNATURE = "<i>Perioada: 21.09–23.09.2026 (saptamana_curenta) · funnel()</i>"


@pytest.mark.parametrize(
    ("result", "answer_number"),
    [
        ('{"change": "(−15%)"}', "+15%"),
        ('{"change": "(−15%)"}', "15%"),
        ('{"change": "(+15%)"}', "−15%"),
        ('{"leads": 15}', "15%"),
        ('{"scr": "15%"}', "15"),
    ],
)
def test_sign_and_percent_are_part_of_the_number(result: str, answer_number: str) -> None:
    allowed = allowed_numbers(SIGNATURE, [result])

    assert first_unverified_number(f"Schimbarea este {answer_number}.", allowed) == answer_number


@pytest.mark.parametrize("answer_number", ["−15%", "-15%", "−15 %"])
def test_minus_sign_and_space_before_percent_are_equivalent(answer_number: str) -> None:
    allowed = allowed_numbers(SIGNATURE, ['{"change": "(−15%)"}'])

    assert first_unverified_number(f"Schimbarea este {answer_number}.", allowed) is None


def test_hyphen_inside_word_is_not_a_minus() -> None:
    allowed = allowed_numbers(SIGNATURE, ['{"leads": 3}'])

    assert first_unverified_number("Top-3 consultanți.", allowed) is None


def test_date_parts_are_allowed_only_from_signature_dates() -> None:
    allowed = allowed_numbers(SIGNATURE, ['{"snapshot_date": "25.09.2026"}'])

    assert first_unverified_number("Din 21 septembrie 2026.", allowed) is None
    assert first_unverified_number("Snapshot din 25.09.2026.", allowed) is None
    assert first_unverified_number("Snapshot din 25 septembrie.", allowed) == "25"


@pytest.mark.parametrize("numeral", ["cincisprezece", "Cincisprezece", "șase", "şase"])
def test_numeral_in_words_is_checked_like_a_number(numeral: str) -> None:
    allowed = allowed_numbers(SIGNATURE, ['{"leads": 3}'])

    assert first_unverified_number(f"Au fost {numeral} lead-uri.", allowed) == numeral


def test_numeral_in_words_found_in_results_is_allowed() -> None:
    allowed = allowed_numbers(SIGNATURE, ['{"leads": 15}'])

    assert first_unverified_number("Au fost cincisprezece lead-uri.", allowed) is None


def test_articles_and_nouă_are_not_numerals() -> None:
    allowed = allowed_numbers(SIGNATURE, [])

    assert first_unverified_number("Un lead are o ofertă nouă, una singură.", allowed) is None
