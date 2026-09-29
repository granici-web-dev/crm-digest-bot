from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from test_report_runner import REPORT_DATE, TENANT_ID, store_snapshot, todays_lead

from digest.acceptance.chat_eval import (
    ExpectedCall,
    GoldenCase,
    answer_text_lines,
    memoized_frame_loader,
    run_chat_eval,
    summarize,
)
from digest.chat.tools import ToolData
from digest.config import AppConfig
from digest.db.lead_frame import load_lead_frame, success_snapshot_dates
from digest.db.schema import chat_questions
from factories import make_lead_links
from fakes import scripted_anthropic, text_message, tool_use_message

YESTERDAY_FUNNEL = {"period": "ieri", "showroom": "toate"}


def yesterday_case(case_id: str) -> GoldenCase:
    return GoldenCase(
        id=case_id,
        tags=[],
        question="Câte lead-uri au fost ieri?",
        expect="answer",
        calls=[ExpectedCall(tool="funnel", arguments=YESTERDAY_FUNNEL, numbers=["counts.leads"])],
    )


async def test_runner_grades_against_numbers_recomputed_from_snapshot(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    await store_snapshot(
        engine,
        REPORT_DATE,
        [todays_lead(1, source_name="Telefon"), todays_lead(2, source_name="Telefon")],
    )

    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return await load_lead_frame(engine, TENANT_ID, snapshot_date, app_config)

    data = ToolData(
        date(2026, 9, 26),
        await success_snapshot_dates(engine, TENANT_ID),
        memoized_frame_loader(load_frame),
        app_config,
        make_lead_links(app_config.status_mapping),
    )
    anthropic = scripted_anthropic(
        tool_use_message(("funnel", YESTERDAY_FUNNEL)),
        text_message("Ieri au intrat 2 lead-uri."),
        tool_use_message(("funnel", YESTERDAY_FUNNEL)),
        text_message("Ieri au intrat 3 lead-uri."),
        text_message("Ieri au intrat 3 lead-uri noi."),
        tool_use_message(("funnel", YESTERDAY_FUNNEL)),
        text_message("Ieri au intrat 4 lead-uri."),
        text_message("Ieri au intrat 2 lead-uri."),
    )

    results = await run_chat_eval(
        [yesterday_case("right"), yesterday_case("wrong"), yesterday_case("retried")],
        anthropic.client,
        "claude-sonnet-5",
        data,
    )

    passed, failed, retried = results
    assert passed.grade.passed, passed.grade.reason
    assert not failed.grade.passed
    assert failed.grade.reason == "статус unverified_numbers, число «3»"
    assert retried.grade.passed, retried.grade.reason
    summary = summarize(results, "claude-sonnet-5")
    # Проваленный повтор (failed) идёт в guard_hits, в повторы только успешный (retried).
    assert (summary.passed, summary.guard_hits, summary.guard_retries) == (2, 1, 1)
    lines = answer_text_lines(results)
    assert lines.count("Текст модели до стража:") == 2
    assert "Ieri au intrat 4 lead-uri." in lines
    async with engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(chat_questions)) == 0
