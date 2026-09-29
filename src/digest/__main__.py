import argparse
import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.acceptance.chat_eval import (
    answer_text_lines,
    eval_table_lines,
    load_golden_cases,
    memoized_frame_loader,
    run_chat_eval,
    summarize,
    with_dependencies,
)
from digest.acceptance.privacy import audit_lines, audit_privacy
from digest.app import create_anthropic_client, create_report_deps, run_app, seed_defaults
from digest.chat.tools import ToolData
from digest.config import AppConfig, load_app_config
from digest.db.engine import create_database_engine
from digest.db.lead_frame import load_lead_frame, success_snapshot_dates
from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import create_bot
from digest.log_format import configure_logging
from digest.mefi.client import MefiClient, create_mefi_http_client
from digest.reports.lead_links import LeadLinks
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import REPORT_LEVELS, ReportLevel
from digest.reports.runner import ReportReader, run_report
from digest.settings import Settings
from digest.snapshot import SnapshotSources, describe_error, run_daily_snapshot

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
GOLDEN_FILE = Path(__file__).resolve().parents[2] / "tests" / "eval" / "chat-golden.yaml"

logger = logging.getLogger(__name__)


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m digest")
    commands = parser.add_subparsers(dest="command")
    report = commands.add_parser("report", help="собрать и отправить отчёт за дату вручную")
    report.add_argument("level", choices=REPORT_LEVELS)
    report.add_argument("--date", type=date.fromisoformat, required=True)
    report.add_argument("--dry-run", action="store_true", help="отправить в тестовую группу")
    # Без --date: mefi отдаёт только текущее состояние, снапшот под прошлой датой исказил бы
    # разницу снапшотов (CLAUDE.md, инвариант 3).
    commands.add_parser("snapshot", help="снять снапшот лидов mefi за сегодня вручную")
    audit = commands.add_parser("audit", help="проверки приёмки по данным базы")
    audit_targets = audit.add_subparsers(dest="target", required=True)
    privacy = audit_targets.add_parser(
        "privacy", help="перерисовать отчёты за дни и проверить тексты на контакты (M8)"
    )
    privacy.add_argument("--days", type=int, default=14)
    privacy.add_argument(
        "--end", type=date.fromisoformat, help="последний день, по умолчанию сегодня"
    )
    privacy.add_argument("--out", type=Path, help="записать таблицу и итог в файл")
    evaluation = commands.add_parser("eval", help="эталонные прогоны приёмки")
    eval_targets = evaluation.add_subparsers(dest="target", required=True)
    chat = eval_targets.add_parser(
        "chat", help="эталонный набор вопросов чата через живой Anthropic API (M6)"
    )
    chat.add_argument("--file", type=Path, default=GOLDEN_FILE)
    chat.add_argument("--only", help="id вопросов через запятую; уточнения тянут свой вопрос")
    chat.add_argument("--out", type=Path, help="записать таблицу и итог в файл, без ответов")
    return parser.parse_args(argv)


def create_report_reader(
    engine: AsyncEngine, config: AppConfig, app_settings: Settings
) -> ReportReader:
    return ReportReader(
        engine,
        config,
        app_settings.tenant_id,
        IMPLEMENTED_MODULES,
        LeadLinks.from_mefi_base_url(app_settings.mefi_base_url, config.status_mapping.lead_links),
    )


def print_and_save(lines: list[str], out: Path | None) -> None:
    for line in lines:
        print(line)
    if out is not None:
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_privacy_audit(
    app_settings: Settings, days: int, end: date | None, out: Path | None
) -> int:
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    end = end or datetime.now(ZoneInfo(config.status_mapping.time.timezone)).date()
    try:
        audit = await audit_privacy(create_report_reader(engine, config, app_settings), end, days)
    finally:
        await engine.dispose()
    print_and_save(audit_lines(audit), out)
    return 1 if audit.findings or audit.raw_findings else 0


async def run_manual_report(
    app_settings: Settings, level: ReportLevel, report_date: date, dry_run: bool
) -> int:
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    await seed_defaults(engine, config, app_settings.tenant_id)
    deps = create_report_deps(engine, config, app_settings, dry_run or app_settings.dry_run)
    time_settings = config.status_mapping.time
    now = datetime.combine(
        report_date, time_settings.daily_window_end, tzinfo=ZoneInfo(time_settings.timezone)
    )
    try:
        outcome = await run_report(level, now, deps, late=False)
    finally:
        await deps.report_bot.session.close()
        await deps.ops.bot.session.close()
        await engine.dispose()
    print(outcome)
    return 1 if outcome == "failed" else 0


async def run_manual_snapshot(app_settings: Settings) -> int:
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    http_client = create_mefi_http_client(app_settings.mefi_base_url, app_settings.mefi_api_key)
    clients_http_client = create_mefi_http_client(
        app_settings.mefi_base_url, app_settings.mefi_clients_api_key
    )
    ops = OpsChannel(
        create_bot(app_settings.telegram_ops_bot_token.get_secret_value()),
        app_settings.telegram_ops_chat_id,
    )
    now = datetime.now(ZoneInfo(config.status_mapping.time.timezone))
    try:
        sources = SnapshotSources(
            leads_client=MefiClient(http_client),
            clients_client=MefiClient(clients_http_client),
            contact_hash_key=app_settings.contact_hash_key,
        )
        outcome = await run_daily_snapshot(
            engine, sources, config.status_mapping, app_settings.tenant_id, now, "manual"
        )
        # Ручной прогон алертит как плановый: сбой клиентов не должен остаться только в консоли.
        if outcome.clients_alert is not None:
            await notify_ops(ops, outcome.clients_alert)
    except Exception as error:
        logger.error("manual snapshot failed", extra={"error": describe_error(error)})
        await notify_ops(
            ops, f"Ручной снапшот за {now:%d.%m.%Y %H:%M} не удался: {describe_error(error)}."
        )
        return 1
    finally:
        await http_client.aclose()
        await clients_http_client.aclose()
        await ops.bot.session.close()
        await engine.dispose()
    print(f"snapshot_run {outcome.run_id} {outcome.status}")
    if outcome.status == "preview":
        window_end = config.status_mapping.time.daily_window_end
        print(
            f"До {window_end:%H:%M} снапшот пишется как preview: отчёты и чат его не читают, "
            f"снапшот дня снимет плановая джоба в {window_end:%H:%M}."
        )
    if outcome.clients_alert is not None:
        print(outcome.clients_alert)
    return 0


async def run_chat_eval_command(
    app_settings: Settings, golden_file: Path, only: str | None, out: Path | None
) -> int:
    client = create_anthropic_client(app_settings)
    if client is None:
        print("ANTHROPIC_API_KEY не задан: eval chat ходит в живой Anthropic API.")
        return 2
    cases = load_golden_cases(golden_file)
    if only:
        cases = with_dependencies(cases, set(only.split(",")))
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    reader = create_report_reader(engine, config, app_settings)

    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return await load_lead_frame(engine, app_settings.tenant_id, snapshot_date, config)

    # Telegram, ops и chat_questions не трогаем: прогон не должен попасть в журнал чата (V4).
    try:
        data = ToolData(
            datetime.now(ZoneInfo(config.status_mapping.time.timezone)).date(),
            await success_snapshot_dates(engine, app_settings.tenant_id),
            memoized_frame_loader(load_frame),
            config,
            reader.lead_links,
        )
        results = await run_chat_eval(cases, client, app_settings.anthropic_model, data)
    finally:
        await client.close()
        await engine.dispose()
    summary = summarize(results, app_settings.anthropic_model)
    table = eval_table_lines(results, summary)
    print_and_save(table, out)
    for line in answer_text_lines(results):
        print(line)
    return 0 if summary.meets_gate else 1


async def run_service(app_settings: Settings) -> int:
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    try:
        await run_app(engine, config, app_settings)
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    app_settings = Settings()
    configure_logging(app_settings.log_level)
    if arguments.command == "report":
        return asyncio.run(
            run_manual_report(app_settings, arguments.level, arguments.date, arguments.dry_run)
        )
    if arguments.command == "snapshot":
        return asyncio.run(run_manual_snapshot(app_settings))
    if arguments.command == "eval":
        return asyncio.run(
            run_chat_eval_command(app_settings, arguments.file, arguments.only, arguments.out)
        )
    if arguments.command == "audit":
        return asyncio.run(
            run_privacy_audit(app_settings, arguments.days, arguments.end, arguments.out)
        )
    return asyncio.run(run_service(app_settings))


if __name__ == "__main__":
    raise SystemExit(main())
