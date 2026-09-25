import argparse
import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from digest.app import create_report_deps, run_app, seed_defaults
from digest.config import load_app_config
from digest.db.engine import create_database_engine
from digest.log_format import configure_logging
from digest.mefi.client import MefiClient, create_mefi_http_client
from digest.reports.periods import REPORT_LEVELS, ReportLevel
from digest.reports.runner import run_report
from digest.settings import Settings
from digest.snapshot import describe_error, run_daily_snapshot

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

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
    return parser.parse_args(argv)


async def run_manual_report(
    app_settings: Settings, level: ReportLevel, report_date: date, dry_run: bool
) -> int:
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    await seed_defaults(engine, app_settings.tenant_id)
    deps = create_report_deps(engine, config, app_settings, dry_run or app_settings.dry_run)
    time_settings = config.status_mapping.time
    now = datetime.combine(
        report_date, time_settings.daily_window_end, tzinfo=ZoneInfo(time_settings.timezone)
    )
    try:
        outcome = await run_report(level, now, deps)
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
    now = datetime.now(ZoneInfo(config.status_mapping.time.timezone))
    try:
        run_id = await run_daily_snapshot(
            engine, MefiClient(http_client), config.status_mapping, app_settings.tenant_id, now
        )
    except Exception as error:
        logger.error("manual snapshot failed", extra={"error": describe_error(error)})
        return 1
    finally:
        await http_client.aclose()
        await engine.dispose()
    print(f"snapshot_run {run_id}")
    return 0


async def run_service(app_settings: Settings) -> int:
    config = load_app_config(CONFIG_DIR)
    engine = create_database_engine(app_settings.database_url.get_secret_value())
    await run_app(engine, config, app_settings)
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
    return asyncio.run(run_service(app_settings))


if __name__ == "__main__":
    raise SystemExit(main())
