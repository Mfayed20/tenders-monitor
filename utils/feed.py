"""
The tender feed read by the Climatech CRM (crm.climch.com).

Each run writes feed/tenders.json: the status of every source and every tender that currently
matches (keywords and dates, without the Telegram dedup), merged with the previous feed so a
tender stays listed until it closes even if the CRM misses a run. The daily workflow commits
the file to the `tender-feed` branch; the CRM reads it from there and makes each new
Climatech tender a lead (it remembers which ones it has already made).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.dates import KSA_TZ

logger = logging.getLogger(__name__)

FEED_VERSION = 1
KEEP_DAYS = 60  # a tender found more than this long ago leaves the feed
CLOSED_GRACE_DAYS = 3  # ... and so does one that closed more than this long ago


def tender_key(site: str, ref_number: str, link: str) -> str:
    return f"{site}:{(ref_number or link or '').strip()}"[:300]


def feed_entry(row: Any, raw: Any, now: datetime) -> dict[str, Any]:
    """One feed item from a matched TenderRow and its source Tender."""
    extra = getattr(raw, "raw_data", None) or {}
    return {
        "key": tender_key(row.site, raw.ref_number if raw else row.ref_number, row.link),
        "site": row.site,
        "ref": raw.ref_number if raw and raw.ref_number else ("" if row.ref_number == "N/A" else row.ref_number),
        "title": " ".join((raw.title if raw else row.title).split())[:500],
        "link": row.link,
        "published": raw.publish_date.isoformat() if raw and raw.publish_date else "",
        "closes": raw.close_date.isoformat() if raw and raw.close_date else "",
        "company": row.company_match,
        "keywords": row.matched_keywords,
        "agency": str(extra.get("agency", ""))[:200],
        "activity": str(extra.get("activity", ""))[:200],
        "description": (raw.description if raw else row.description or "")[:500],
        "first_seen": now.isoformat(),
    }


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KSA_TZ)


def merge_feed(previous: list[dict], current: list[dict], now: datetime) -> list[dict]:
    """Current matches plus earlier ones still open; an item keeps its first_seen."""
    merged: dict[str, dict] = {}
    for item in previous:
        if isinstance(item, dict) and item.get("key"):
            merged[item["key"]] = item
    for item in current:
        old = merged.get(item["key"])
        merged[item["key"]] = {**item, "first_seen": old.get("first_seen", item["first_seen"])} if old else item

    kept = []
    for item in merged.values():
        first = _parse(item.get("first_seen", ""))
        closes = _parse(item.get("closes", ""))
        if first and first < now - timedelta(days=KEEP_DAYS):
            continue
        if closes and closes < now - timedelta(days=CLOSED_GRACE_DAYS):
            continue
        kept.append(item)
    return sorted(kept, key=lambda item: item.get("first_seen", ""), reverse=True)


def read_feed(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Previous tender feed unreadable (%s); starting a new one", exc)
        return []
    tenders = data.get("tenders") if isinstance(data, dict) else None
    return tenders if isinstance(tenders, list) else []


def write_feed(path: Path, tenders: list[dict], scrapers: list[dict[str, Any]], now: datetime | None = None) -> Path:
    now = now or datetime.now(KSA_TZ)
    failing = [s["site"] for s in scrapers if s.get("status") not in ("ok", "disabled")]
    payload = {
        "version": FEED_VERSION,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "status": "partial_failure" if failing else "ok",
        "sources": [
            {key: s.get(key) for key in ("site", "status", "raw_count", "matched_count", "error")}
            for s in scrapers
        ],
        "tenders": tenders,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    logger.info("Tender feed written: %s (%d tenders, failing sources: %s)", path, len(tenders), ", ".join(failing) or "none")
    return path
