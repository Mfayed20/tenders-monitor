import json
from datetime import datetime, timedelta

import main
from scrapers.base import Tender
from utils.dates import KSA_TZ
from utils.feed import merge_feed, read_feed, write_feed


def _tender(ref, title="توريد وتركيب شواحن سيارات كهربائية", days_to_close=20):
    now = datetime.now(KSA_TZ)
    return Tender(site="Etimad", title=title, ref_number=ref, publish_date=now - timedelta(hours=5),
                  close_date=now + timedelta(days=days_to_close), link=f"https://tenders.etimad.sa/x?{ref}",
                  raw_data={"agency": "وزارة الصحة"})


def test_feed_lists_matches_seen_before_and_keeps_first_seen(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "is_seen", lambda *args, **kwargs: True)  # Telegram already sent them
    rows, _ = main.filter_tenders([_tender("A"), _tender("B", title="Office cleaning")], use_dedup=False)
    now = datetime.now(KSA_TZ)
    first = [main.feed_entry(r, r.source, now) for r in rows]

    assert [item["key"] for item in first] == ["Etimad:A"]
    assert first[0]["agency"] == "وزارة الصحة"
    assert first[0]["company"] == "Climatech"
    assert first[0]["closes"].startswith((now + timedelta(days=20)).strftime("%Y-%m-%d"))

    path = tmp_path / "feed" / "tenders.json"
    write_feed(path, merge_feed([], first, now), [{"site": "Etimad", "status": "ok", "raw_count": 9}], now)
    later = now + timedelta(hours=8)
    second = [{**first[0], "first_seen": later.isoformat()}]
    merged = merge_feed(read_feed(path), second, later)

    assert merged[0]["first_seen"] == now.isoformat()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["status"] == "ok" and data["sources"][0]["raw_count"] == 9


def test_feed_drops_closed_and_old_items():
    now = datetime.now(KSA_TZ)
    items = [
        {"key": "open", "first_seen": now.isoformat(), "closes": (now + timedelta(days=3)).isoformat()},
        {"key": "closed", "first_seen": now.isoformat(), "closes": (now - timedelta(days=5)).isoformat()},
        {"key": "old", "first_seen": (now - timedelta(days=90)).isoformat(), "closes": ""},
        {"key": "no-close", "first_seen": now.isoformat(), "closes": ""},
    ]

    assert sorted(item["key"] for item in merge_feed(items, [], now)) == ["no-close", "open"]


def test_feed_failing_source_marks_status(tmp_path):
    path = write_feed(tmp_path / "t.json", [], [{"site": "Etimad", "status": "ok"}, {"site": "TenderSA", "status": "partial_failure", "error": "returned no tenders"}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "partial_failure"
