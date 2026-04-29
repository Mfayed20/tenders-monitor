import asyncio
import json

import main
from scrapers.base import Tender
from utils.dedup import get_db_path, is_seen, mark_seen, set_db_path


def test_settings_from_args_applies_cli_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TENDER_RUN_WINDOW_HOURS", "24")
    monkeypatch.setenv("TENDER_CLOSE_WINDOW_DAYS", "14")
    parser = main.build_arg_parser()
    args = parser.parse_args(
        [
            "--output-dir",
            str(tmp_path),
            "--disable-scraper",
            "Etimad,TenderSA",
            "--no-telegram",
        ]
    )

    settings = main.settings_from_args(args)

    assert settings.output_dir == tmp_path
    assert settings.seen_db_path == tmp_path / "seen_tenders.db"
    assert settings.run_window_hours == 24
    assert settings.close_window_days == 14
    assert settings.disabled_scrapers == {"Etimad", "TenderSA"}
    assert settings.telegram_enabled is False


def test_settings_rejects_unknown_scraper():
    parser = main.build_arg_parser()
    args = parser.parse_args(["--disable-scraper", "NoSuchScraper"])

    try:
        main.settings_from_args(args)
    except ValueError as exc:
        assert "unknown disabled scraper" in str(exc)
    else:
        raise AssertionError("settings_from_args should reject unknown scraper names")


def test_dedup_database_path_is_injectable(tmp_path):
    previous_path = get_db_path()
    db_path = tmp_path / "custom_seen.db"
    try:
        set_db_path(db_path)
        assert is_seen("Site", "EV charger tender", "REF-1") is False

        mark_seen("Site", "EV charger tender", "REF-1")

        assert db_path.exists()
        assert is_seen("Site", "EV charger tender", "REF-1") is True
    finally:
        set_db_path(previous_path)


def test_filter_dry_run_does_not_mark_seen(monkeypatch):
    marked = []
    monkeypatch.setattr(main, "is_seen", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "mark_seen", lambda *args, **kwargs: marked.append(args))

    tender = Tender(
        site="Fixture",
        title="Supply and installation of EV charging stations",
        ref_number="DRY-001",
        description="Includes charger installation.",
    )

    matched, diagnostics = main.filter_tenders([tender], dry_run=True)

    assert len(matched) == 1
    assert diagnostics.matched_counts["Fixture"] == 1
    assert marked == []


def test_run_scrapers_records_partial_failures():
    class GoodScraper:
        SITE_NAME = "Good"
        NEEDS_BROWSER = False

        async def scrape(self, browser=None):
            return [Tender(site="Good", title="EV charging station", ref_number="G-1")]

    class BadScraper:
        SITE_NAME = "Bad"
        NEEDS_BROWSER = False

        async def scrape(self, browser=None):
            raise RuntimeError("boom")

    tenders, stats = asyncio.run(main.run_scrapers([GoodScraper(), BadScraper()]))

    assert len(tenders) == 1
    assert {item.site: item.status for item in stats} == {"Good": "ok", "Bad": "failed"}
    assert "RuntimeError: boom" in next(item.error for item in stats if item.site == "Bad")


def test_execute_run_writes_summary_and_skips_telegram_when_disabled(monkeypatch, tmp_path):
    sent_messages = []

    async def fake_run_scrapers(scrapers):
        tender = Tender(
            site="Fixture",
            title="Supply and installation of EV charging stations",
            ref_number="RUN-001",
            description="Includes charger installation.",
        )
        stats = [main.ScraperRunStats(site="Fixture", needs_browser=False, raw_count=1)]
        return [tender], stats

    async def fake_send_telegram(*args, **kwargs):
        sent_messages.append((args, kwargs))
        return True

    monkeypatch.setattr(main, "run_scrapers", fake_run_scrapers)
    monkeypatch.setattr(main, "send_telegram_alert", fake_send_telegram)

    settings = main.RuntimeSettings(output_dir=tmp_path, telegram_enabled=False, dry_run=True)
    summary = asyncio.run(main.execute_run(settings))

    summary_path = tmp_path / "run_summary.json"
    daily_csvs = list(tmp_path.glob("tenders_*.csv"))

    assert sent_messages == []
    assert summary["telegram"] == {"enabled": False, "sent": False}
    assert summary["totals"]["matched"] == 1
    assert summary_path.exists()
    assert json.loads(summary_path.read_text(encoding="utf-8"))["totals"]["matched"] == 1
    assert len(daily_csvs) == 1
    assert not (tmp_path / "all_tenders.csv").exists()
