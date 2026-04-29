from datetime import datetime, timedelta
from uuid import uuid4

import main
from scrapers.base import Tender
from utils.dates import KSA_TZ


def test_filter_rejects_unparsed_source_dates(monkeypatch):
    monkeypatch.setattr(main, "is_seen", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "mark_seen", lambda *args, **kwargs: None)

    tender = Tender(
        site="Etimad",
        title="Supply and installation of EV charging stations",
        ref_number="ETM-001",
        publish_date=None,
        close_date=datetime.now(KSA_TZ) + timedelta(days=7),
        publish_date_raw="04/05/2026",
        close_date_raw="2026-04-12",
        description="Includes charger installation and CPMS scope.",
    )

    matched, diagnostics = main.filter_tenders([tender])

    assert matched == []
    assert diagnostics.reject_counts["Etimad"]["unparsed_publish_date"] == 1


def test_filter_keeps_missing_dates_conservative(monkeypatch):
    monkeypatch.setattr(main, "is_seen", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "mark_seen", lambda *args, **kwargs: None)

    tender = Tender(
        site="KSATendersGate",
        title="Electric vehicle maintenance and diagnostics",
        ref_number="KSA-002",
        publish_date=None,
        close_date=None,
        description="Workshop support for electric bus fleet.",
    )

    matched, diagnostics = main.filter_tenders([tender])

    assert len(matched) == 1
    assert diagnostics.matched_total == 1


def test_write_csv_overwrites_daily_snapshot_and_preserves_master(monkeypatch):
    temp_path = main.LOG_DIR / f"pytest_csv_{uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "LOG_DIR", temp_path)

    tender = main.TenderRow(
        site="Etimad",
        title="EV charging station rollout",
        ref_number="ETM-003",
        publish_date="2026-04-05",
        close_date="2026-04-10",
        days_left=5,
        link="https://example.com/tender",
        company_match="Climatech",
        matched_keywords="charging station",
        description="Public charging deployment.",
    )

    main.write_csv([tender], "2026-04-05")
    daily_path = temp_path / "tenders_2026-04-05.csv"
    master_path = temp_path / "all_tenders.csv"

    assert daily_path.read_text(encoding="utf-8-sig").strip().count("\n") == 1
    assert master_path.read_text(encoding="utf-8-sig").strip().count("\n") == 1

    main.write_csv([], "2026-04-05")

    assert daily_path.read_text(encoding="utf-8-sig").strip().count("\n") == 0
    assert master_path.read_text(encoding="utf-8-sig").strip().count("\n") == 1
