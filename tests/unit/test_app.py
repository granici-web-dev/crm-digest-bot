from digest.app import startup_announcement


def test_startup_announcement_contains_version_and_dry_run_flag() -> None:
    assert startup_announcement("abc1234", dry_run=True) == (
        "Бот запущен, версия abc1234, DRY_RUN=1."
    )
    assert startup_announcement("abc1234", dry_run=False).endswith("DRY_RUN=0.")
