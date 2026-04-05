"""
KSA EV Tender Monitor — Daily scraper for Saudi Arabia EV-related tenders.

Scrapes 5 tender sites, filters by EV keywords (English + Arabic),
deduplicates, outputs CSV, and sends Telegram alerts.

Usage:
    python main.py         # Full run: scrape + filter + CSV + Telegram
    python main.py --purge # Purge old dedup records (90 days)

Companies:
    Climatech Charger — chargers, installation, infrastructure, CPO
    EVS — fleet maintenance, service, repair, management
"""

import argparse
import asyncio
import csv
import html
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from scrapers.etimad import EtimadScraper
from scrapers.ksagate import KSAGateScraper
from scrapers.tendersa import TendersaScraper
from scrapers.tendersinfo import TendersInfoScraper
from scrapers.metenders import METendersScraper
from scrapers.tendersontime import TendersOnTimeScraper
from scrapers.base import Tender
from utils.keywords import match_tender
from utils.dates import is_new_tender, is_closing_soon, format_date, KSA_TZ, has_date_text
from utils.dedup import is_seen, mark_seen, purge_old
from utils.telegram_notifier import send_telegram_alert

@dataclass
class TenderRow:
    site: str
    title: str
    ref_number: str
    publish_date: str
    close_date: str
    days_left: int | None  # days until closing
    link: str
    company_match: str
    matched_keywords: str  # comma-separated keywords that triggered the match
    description: str  # tender description/scope (truncated)


@dataclass
class FilterDiagnostics:
    raw_total: int = 0
    matched_total: int = 0
    reject_counts: dict[str, Counter[str]] = field(default_factory=dict)

    def record_reject(self, site: str, reason: str) -> None:
        self.reject_counts.setdefault(site, Counter())[reason] += 1


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent / "output"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "tender_monitor.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

CSV_HEADER = [
    "Site", "Title", "Ref#", "Published", "Deadline",
    "Days Left", "Link", "Match (Company)", "Keywords", "Description",
]

# All scrapers to run
SCRAPERS = [
    EtimadScraper(),
    KSAGateScraper(),
    TendersaScraper(),
    TendersInfoScraper(),
    METendersScraper(),
    TendersOnTimeScraper(),
]


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

async def run_scrapers() -> list[Tender]:
    """Run all scrapers, using a shared Playwright browser for JS-heavy sites."""
    all_tenders: list[Tender] = []
    browser = None

    # Check if any scraper needs a browser
    needs_browser = any(s.NEEDS_BROWSER for s in SCRAPERS)

    try:
        if needs_browser:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            logger.info("Playwright browser launched")

        # Run httpx-based scrapers concurrently
        http_scrapers = [s for s in SCRAPERS if not s.NEEDS_BROWSER]
        http_tasks = [s.scrape() for s in http_scrapers]

        if http_tasks:
            results = await asyncio.gather(*http_tasks, return_exceptions=True)
            for scraper, result in zip(http_scrapers, results):
                if isinstance(result, Exception):
                    logger.error(
                        "Scraper %s failed: %s", scraper.SITE_NAME, result
                    )
                else:
                    all_tenders.extend(result)

        # Run browser-based scrapers concurrently (separate contexts)
        browser_scrapers = [s for s in SCRAPERS if s.NEEDS_BROWSER]
        if browser_scrapers:
            browser_tasks = [s.scrape(browser=browser) for s in browser_scrapers]
            browser_results = await asyncio.gather(*browser_tasks, return_exceptions=True)
            for scraper, result in zip(browser_scrapers, browser_results):
                if isinstance(result, Exception):
                    logger.error("Scraper %s failed: %s", scraper.SITE_NAME, result)
                else:
                    all_tenders.extend(result)

    finally:
        if browser:
            await browser.close()
            await pw.stop()
            logger.info("Playwright browser closed")

    return all_tenders


def _clean_title(raw: str, max_len: int = 120) -> str:
    """Clean up a raw tender title for display."""
    text = html.unescape(raw)                      # &lt;/br&gt; -> </br>
    text = re.sub(r"<[^>]+>", " ", text)            # strip HTML tags
    text = re.sub(r"##[^#]*##", "", text)           # strip ##quantity: 9##
    text = re.sub(r"\s*,\s*,+", ",", text)          # collapse repeated commas
    text = re.sub(r"\s+", " ", text).strip()        # collapse whitespace
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def _log_filter_diagnostics(diagnostics: FilterDiagnostics) -> None:
    rejected_total = sum(sum(counter.values()) for counter in diagnostics.reject_counts.values())
    logger.info(
        "Filtering diagnostics: raw=%d matched=%d rejected=%d",
        diagnostics.raw_total,
        diagnostics.matched_total,
        rejected_total,
    )

    for site in sorted(diagnostics.reject_counts):
        summary = ", ".join(
            f"{reason}={count}"
            for reason, count in diagnostics.reject_counts[site].most_common(3)
        )
        if summary:
            logger.info("Reject summary [%s]: %s", site, summary)


def filter_tenders(tenders: list[Tender]) -> tuple[list[TenderRow], FilterDiagnostics]:
    """
    Filter tenders by:
    1. EV keyword match (English + Arabic)
    2. Published within last 7 days — if publish date available
       (EV tenders are rare; 24h is too aggressive)
    3. Closing within 30 days — if close date available
    4. Not previously seen (dedup)
    """
    matched = []
    diagnostics = FilterDiagnostics(raw_total=len(tenders))
    now = datetime.now(KSA_TZ)

    for t in tenders:
        # Keyword match
        result = match_tender(t.title, t.description)
        if not result.matched:
            diagnostics.record_reject(t.site, result.reject_reason or "no_business_match")
            continue

        # Time filters (conservative only when the source exposes no date at all)
        if t.publish_date is None and has_date_text(t.publish_date_raw):
            diagnostics.record_reject(t.site, "unparsed_publish_date")
            continue
        if t.close_date is None and has_date_text(t.close_date_raw):
            diagnostics.record_reject(t.site, "unparsed_close_date")
            continue

        if t.publish_date and t.publish_date > now:
            diagnostics.record_reject(t.site, "future_publish_date")
            continue
        if not is_new_tender(t.publish_date, hours=168):  # 7 days
            diagnostics.record_reject(t.site, "publish_window")
            continue
        if t.close_date and t.close_date < now:
            diagnostics.record_reject(t.site, "already_closed")
            continue
        if not is_closing_soon(t.close_date):
            diagnostics.record_reject(t.site, "close_window")
            continue

        # Dedup
        if is_seen(t.site, t.title, t.ref_number):
            logger.debug("Already seen: %s — %s", t.site, t.title[:50])
            diagnostics.record_reject(t.site, "dedup")
            continue

        # Mark as seen
        mark_seen(t.site, t.title, t.ref_number)

        # Calculate days until closing
        days_left = None
        if t.close_date:
            delta = t.close_date - now
            days_left = max(0, delta.days)

        matched.append(TenderRow(
            site=t.site,
            title=_clean_title(t.title),
            ref_number=t.ref_number or "N/A",
            publish_date=format_date(t.publish_date),
            close_date=format_date(t.close_date),
            days_left=days_left,
            link=t.link,
            company_match=result.company,
            matched_keywords=", ".join(result.matched_keywords[:5]),
            description=t.description[:200] if t.description else "",
        ))

    diagnostics.matched_total = len(matched)
    return matched, diagnostics


def write_csv(tenders: list[TenderRow], date_str: str) -> Path:
    """Write a daily snapshot CSV and append non-empty runs to the master CSV."""
    csv_path = LOG_DIR / f"tenders_{date_str}.csv"

    def _to_row(t):
        return [
            t.site, t.title, t.ref_number,
            t.publish_date, t.close_date,
            t.days_left if t.days_left is not None else "N/A",
            t.link, t.company_match, t.matched_keywords, t.description,
        ]

    # 1. Daily CSV snapshot (overwrite on each run to avoid stale same-day rows)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for t in tenders:
            writer.writerow(_to_row(t))

    if not tenders:
        logger.info("Daily CSV snapshot written: %s (0 rows)", csv_path.name)
        return csv_path

    # 2. Master CSV (cumulative — all matches ever found)
    master_path = LOG_DIR / "all_tenders.csv"
    write_master_header = not master_path.exists()
    with open(master_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if write_master_header:
            writer.writerow(CSV_HEADER)
        for t in tenders:
            writer.writerow(_to_row(t))

    logger.info("CSV written: %s (%d rows), master: %s", csv_path.name, len(tenders), master_path.name)
    return csv_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="KSA EV Tender Monitor")
    parser.add_argument("--purge", action="store_true", help="Purge old dedup records and exit")
    args = parser.parse_args()

    if args.purge:
        count = purge_old(days=90)
        logger.info("Purged %d old dedup records", count)
        return

    date_str = datetime.now(KSA_TZ).strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("KSA EV Tender Monitor — Run started: %s", date_str)
    logger.info("=" * 60)

    # Step 1: Scrape all sites
    logger.info("Step 1: Scraping %d sites...", len(SCRAPERS))
    all_tenders = await run_scrapers()
    logger.info("Total raw tenders scraped: %d", len(all_tenders))

    if not all_tenders:
        logger.warning("No tenders scraped from any site — check scrapers")
        return

    # Step 2: Filter by keywords, dates, dedup
    logger.info("Step 2: Filtering tenders...")
    matched, diagnostics = filter_tenders(all_tenders)
    logger.info("Matched tenders after filtering: %d", len(matched))
    _log_filter_diagnostics(diagnostics)

    # Step 3: Write CSV (always, even if empty — for audit trail)
    csv_path = write_csv(matched, date_str)

    # Step 4: Send Telegram alert (always — matches or no matches)
    tg_ok = await send_telegram_alert(matched, date_str)
    if tg_ok:
        logger.info("Telegram alert sent successfully")
    else:
        logger.debug("Telegram alert skipped or failed (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

    # Summary
    logger.info("=" * 60)
    logger.info("Run complete. CSV: %s | Matches: %d", csv_path.name, len(matched))
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
