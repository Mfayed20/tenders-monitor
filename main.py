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
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from utils.dedup import is_seen, mark_seen, purge_old, set_db_path
from utils.telegram_notifier import (
    send_telegram_alert,
    send_telegram_test_message,
    telegram_credentials_configured,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = DEFAULT_OUTPUT_DIR


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
    dedup_title: str = ""
    dedup_ref_number: str = ""


@dataclass
class FilterDiagnostics:
    raw_total: int = 0
    matched_total: int = 0
    matched_counts: Counter[str] = field(default_factory=Counter)
    reject_counts: dict[str, Counter[str]] = field(default_factory=dict)

    def record_reject(self, site: str, reason: str) -> None:
        self.reject_counts.setdefault(site, Counter())[reason] += 1

    def record_match(self, site: str) -> None:
        self.matched_counts[site] += 1


@dataclass
class ScraperRunStats:
    site: str
    needs_browser: bool
    raw_count: int = 0
    elapsed_seconds: float = 0.0
    error: str = ""
    fatal: bool = False
    disabled: bool = False

    @property
    def status(self) -> str:
        if self.disabled:
            return "disabled"
        if self.error:
            return "failed" if self.fatal else "partial_failure"
        return "ok"


@dataclass
class RuntimeSettings:
    output_dir: Path | str = DEFAULT_OUTPUT_DIR
    seen_db_path: Path | str | None = None
    log_level: str = "INFO"
    run_window_hours: int = 168
    close_window_days: int = 30
    disabled_scrapers: set[str] = field(default_factory=set)
    telegram_enabled: bool = True
    dry_run: bool = False

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir).expanduser()
        if self.seen_db_path is None:
            self.seen_db_path = self.output_dir / "seen_tenders.db"
        else:
            self.seen_db_path = Path(self.seen_db_path).expanduser()
        self.log_level = self.log_level.upper()

    def validate(self, available_scrapers: set[str] | None = None) -> None:
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        if self.run_window_hours <= 0:
            raise ValueError("run_window_hours must be greater than 0")
        if self.close_window_days <= 0:
            raise ValueError("close_window_days must be greater than 0")
        if available_scrapers is not None:
            unknown = sorted(self.disabled_scrapers - available_scrapers)
            if unknown:
                raise ValueError(f"unknown disabled scraper(s): {', '.join(unknown)}")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger("main")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

CSV_HEADER = [
    "Site", "Title", "Ref#", "Published", "Deadline",
    "Days Left", "Link", "Match (Company)", "Keywords", "Description",
]
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")

# All scrapers to run
SCRAPERS = [
    EtimadScraper(),
    KSAGateScraper(),
    TendersaScraper(),
    TendersInfoScraper(),
    METendersScraper(),
    TendersOnTimeScraper(),
]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KSA EV Tender Monitor")
    parser.add_argument("--purge", action="store_true", help="Purge old dedup records and exit")
    parser.add_argument("--output-dir", help="Directory for CSVs, logs, SQLite DB, and run summary")
    parser.add_argument("--seen-db", help="Path to the SQLite dedup database")
    parser.add_argument("--log-level", help="Logging level: DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    parser.add_argument("--run-window-hours", type=int, help="Only include tenders published within this many hours")
    parser.add_argument("--close-window-days", type=int, help="Only include tenders closing within this many days")
    parser.add_argument("--dry-run", action="store_true", help="Run without Telegram sends or dedup writes")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram notifications for this run")
    parser.add_argument("--telegram-test", action="store_true", help="Send a Telegram smoke-test message and exit")
    parser.add_argument(
        "--disable-scraper",
        action="append",
        default=[],
        help="Disable a scraper by site name; may be repeated or comma-separated",
    )
    return parser


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def _parse_positive_int(name: str, value: int | str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _split_csv(values: list[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]

    out = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _normalize_scraper_name(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name.casefold())


def resolve_scraper_names(names: list[str]) -> set[str]:
    available = {_normalize_scraper_name(scraper.SITE_NAME): scraper.SITE_NAME for scraper in SCRAPERS}
    resolved = set()
    unknown = []

    for name in names:
        normalized = _normalize_scraper_name(name)
        if normalized in available:
            resolved.add(available[normalized])
        else:
            unknown.append(name)

    if unknown:
        raise ValueError(f"unknown disabled scraper(s): {', '.join(sorted(unknown))}")
    return resolved


def settings_from_args(args: argparse.Namespace) -> RuntimeSettings:
    env_disabled = _split_csv(os.getenv("TENDER_DISABLED_SCRAPERS"))
    cli_disabled = _split_csv(args.disable_scraper)
    dry_run = args.dry_run or _parse_bool(os.getenv("TENDER_DRY_RUN"), False)
    telegram_enabled = _parse_bool(os.getenv("TENDER_TELEGRAM_ENABLED"), True)
    if args.telegram_test:
        telegram_enabled = True
    if args.no_telegram or dry_run:
        telegram_enabled = False

    output_dir = args.output_dir or os.getenv("TENDER_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR
    seen_db = args.seen_db or os.getenv("TENDER_SEEN_DB_PATH")
    settings = RuntimeSettings(
        output_dir=output_dir,
        seen_db_path=seen_db,
        log_level=args.log_level or os.getenv("TENDER_LOG_LEVEL", "INFO"),
        run_window_hours=_parse_positive_int(
            "run_window_hours",
            args.run_window_hours if args.run_window_hours is not None else os.getenv("TENDER_RUN_WINDOW_HOURS"),
            168,
        ),
        close_window_days=_parse_positive_int(
            "close_window_days",
            args.close_window_days if args.close_window_days is not None else os.getenv("TENDER_CLOSE_WINDOW_DAYS"),
            30,
        ),
        disabled_scrapers=resolve_scraper_names(env_disabled + cli_disabled),
        telegram_enabled=telegram_enabled,
        dry_run=dry_run,
    )
    settings.validate({scraper.SITE_NAME for scraper in SCRAPERS})
    return settings


def configure_logging(output_dir: Path | str, log_level: str = "INFO") -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_tender_monitor_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    file_handler = logging.FileHandler(output_path / "tender_monitor.log", encoding="utf-8")

    for handler in (stream_handler, file_handler):
        handler.setFormatter(formatter)
        handler._tender_monitor_handler = True
        root_logger.addHandler(handler)

    root_logger.setLevel(getattr(logging, log_level.upper()))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def enabled_scrapers(settings: RuntimeSettings) -> list[Any]:
    return [scraper for scraper in SCRAPERS if scraper.SITE_NAME not in settings.disabled_scrapers]


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

async def _run_single_scraper(scraper: Any, browser: Any = None) -> tuple[list[Tender], ScraperRunStats]:
    """Run one scraper and return both tenders and operational stats."""
    stats = ScraperRunStats(site=scraper.SITE_NAME, needs_browser=scraper.NEEDS_BROWSER)
    started = time.perf_counter()

    try:
        if hasattr(scraper, "reset_run_errors"):
            scraper.reset_run_errors()
        tenders = await scraper.scrape(browser=browser)
        stats.raw_count = len(tenders)
        run_errors = list(getattr(scraper, "run_errors", []))
        if run_errors:
            stats.error = " | ".join(run_errors)
        return tenders, stats
    except Exception as exc:
        stats.error = f"{type(exc).__name__}: {exc}"
        stats.fatal = True
        logger.exception("Scraper %s failed", scraper.SITE_NAME)
        return [], stats
    finally:
        stats.elapsed_seconds = round(time.perf_counter() - started, 3)


async def run_scrapers(scrapers: list[Any] | None = None) -> tuple[list[Tender], list[ScraperRunStats]]:
    """Run all enabled scrapers, using a shared Playwright browser for JS-heavy sites."""
    scrapers = scrapers if scrapers is not None else SCRAPERS
    all_tenders: list[Tender] = []
    stats: list[ScraperRunStats] = []
    browser = None
    pw = None

    http_scrapers = [s for s in scrapers if not s.NEEDS_BROWSER]
    browser_scrapers = [s for s in scrapers if s.NEEDS_BROWSER]

    if http_scrapers:
        results = await asyncio.gather(*[_run_single_scraper(scraper) for scraper in http_scrapers])
        for tenders, scraper_stats in results:
            all_tenders.extend(tenders)
            stats.append(scraper_stats)

    if browser_scrapers:
        try:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            logger.info("Playwright browser launched")

            results = await asyncio.gather(
                *[_run_single_scraper(scraper, browser=browser) for scraper in browser_scrapers]
            )
            for tenders, scraper_stats in results:
                all_tenders.extend(tenders)
                stats.append(scraper_stats)
        except Exception as exc:
            logger.exception("Playwright setup failed; browser-based scrapers skipped")
            for scraper in browser_scrapers:
                stats.append(
                    ScraperRunStats(
                        site=scraper.SITE_NAME,
                        needs_browser=True,
                        error=f"Playwright setup failed: {type(exc).__name__}: {exc}",
                    )
                )
        finally:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
            if browser or pw:
                logger.info("Playwright browser closed")

    return all_tenders, stats


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


def filter_tenders(
    tenders: list[Tender],
    run_window_hours: int = 168,
    close_window_days: int = 30,
    dry_run: bool = False,
) -> tuple[list[TenderRow], FilterDiagnostics]:
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
        if not is_new_tender(t.publish_date, hours=run_window_hours):
            diagnostics.record_reject(t.site, "publish_window")
            continue
        if t.close_date and t.close_date < now:
            diagnostics.record_reject(t.site, "already_closed")
            continue
        if not is_closing_soon(t.close_date, days=close_window_days):
            diagnostics.record_reject(t.site, "close_window")
            continue

        # Dedup
        if is_seen(t.site, t.title, t.ref_number):
            logger.debug("Already seen: %s — %s", t.site, t.title[:50])
            diagnostics.record_reject(t.site, "dedup")
            continue

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
            dedup_title=t.title,
            dedup_ref_number=t.ref_number,
        ))
        diagnostics.record_match(t.site)

    diagnostics.matched_total = len(matched)
    return matched, diagnostics


def _csv_safe(value: Any) -> Any:
    """Prevent spreadsheet formula injection for untrusted scraped strings."""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped.startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_csv(
    tenders: list[TenderRow],
    date_str: str,
    output_dir: Path | str | None = None,
    append_master: bool = True,
) -> Path:
    """Write a daily snapshot CSV and append non-empty runs to the master CSV."""
    out_dir = Path(output_dir) if output_dir is not None else LOG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"tenders_{date_str}.csv"

    def _to_row(t):
        return [_csv_safe(value) for value in [
            t.site, t.title, t.ref_number,
            t.publish_date, t.close_date,
            t.days_left if t.days_left is not None else "N/A",
            t.link, t.company_match, t.matched_keywords, t.description,
        ]]

    # 1. Daily CSV snapshot (overwrite on each run to avoid stale same-day rows)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for t in tenders:
            writer.writerow(_to_row(t))

    if not tenders:
        logger.info("Daily CSV snapshot written: %s (0 rows)", csv_path.name)
        return csv_path

    if not append_master:
        logger.info("Daily CSV snapshot written: %s (%d rows); master append skipped", csv_path.name, len(tenders))
        return csv_path

    # 2. Master CSV (cumulative — all matches ever found)
    master_path = out_dir / "all_tenders.csv"
    write_master_header = not master_path.exists()
    with open(master_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if write_master_header:
            writer.writerow(CSV_HEADER)
        for t in tenders:
            writer.writerow(_to_row(t))

    logger.info("CSV written: %s (%d rows), master: %s", csv_path.name, len(tenders), master_path.name)
    return csv_path


def mark_tenders_seen(tenders: list[TenderRow]) -> int:
    """Mark delivered tenders as seen after durable outputs/alerts have succeeded."""
    marked = 0
    for tender in tenders:
        ref_number = tender.dedup_ref_number or ("" if tender.ref_number == "N/A" else tender.ref_number)
        mark_seen(tender.site, tender.dedup_title or tender.title, ref_number)
        marked += 1
    return marked


def _counter_map_to_dict(counters: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {site: dict(counter) for site, counter in sorted(counters.items())}


def _build_scraper_summary(
    scrape_stats: list[ScraperRunStats],
    diagnostics: FilterDiagnostics,
) -> list[dict[str, Any]]:
    reject_counts = diagnostics.reject_counts
    matched_counts = diagnostics.matched_counts

    return [
        {
            "site": stats.site,
            "status": stats.status,
            "needs_browser": stats.needs_browser,
            "disabled": stats.disabled,
            "raw_count": stats.raw_count,
            "matched_count": matched_counts.get(stats.site, 0),
            "rejected_count": sum(reject_counts.get(stats.site, Counter()).values()),
            "elapsed_seconds": stats.elapsed_seconds,
            "error": stats.error,
            "fatal": stats.fatal,
        }
        for stats in sorted(scrape_stats, key=lambda item: item.site)
    ]


def build_run_summary(
    *,
    date_str: str,
    settings: RuntimeSettings,
    scrape_stats: list[ScraperRunStats],
    diagnostics: FilterDiagnostics,
    csv_path: Path,
    telegram_sent: bool,
    dedup_marked: int = 0,
) -> dict[str, Any]:
    rejected_total = sum(sum(counter.values()) for counter in diagnostics.reject_counts.values())
    scraper_errors = [stats.error for stats in scrape_stats if stats.error]

    return {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial_failure" if scraper_errors else "ok",
        "settings": {
            "output_dir": str(settings.output_dir),
            "seen_db_path": str(settings.seen_db_path),
            "log_level": settings.log_level,
            "run_window_hours": settings.run_window_hours,
            "close_window_days": settings.close_window_days,
            "disabled_scrapers": sorted(settings.disabled_scrapers),
            "telegram_enabled": settings.telegram_enabled,
            "dry_run": settings.dry_run,
        },
        "totals": {
            "raw": diagnostics.raw_total,
            "matched": diagnostics.matched_total,
            "rejected": rejected_total,
            "dedup_marked": dedup_marked,
        },
        "scrapers": _build_scraper_summary(scrape_stats, diagnostics),
        "filter_reject_counts": _counter_map_to_dict(diagnostics.reject_counts),
        "matched_counts": dict(sorted(diagnostics.matched_counts.items())),
        "outputs": {
            "daily_csv": str(csv_path),
            "master_csv": str(Path(settings.output_dir) / "all_tenders.csv"),
            "log_file": str(Path(settings.output_dir) / "tender_monitor.log"),
            "seen_db": str(settings.seen_db_path),
            "run_summary": str(Path(settings.output_dir) / "run_summary.json"),
        },
        "telegram": {
            "enabled": settings.telegram_enabled,
            "configured": telegram_credentials_configured() if settings.telegram_enabled else False,
            "sent": telegram_sent,
        },
        "errors": scraper_errors,
    }


def write_run_summary(summary: dict[str, Any], output_dir: Path | str | None = None) -> Path:
    out_dir = Path(output_dir) if output_dir is not None else LOG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "run_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")
    logger.info("Run summary written: %s", summary_path.name)
    return summary_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def execute_run(settings: RuntimeSettings) -> dict[str, Any]:
    global LOG_DIR

    LOG_DIR = Path(settings.output_dir)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    set_db_path(settings.seen_db_path)

    selected_scrapers = enabled_scrapers(settings)
    disabled_stats = [
        ScraperRunStats(
            site=scraper.SITE_NAME,
            needs_browser=scraper.NEEDS_BROWSER,
            disabled=True,
        )
        for scraper in SCRAPERS
        if scraper.SITE_NAME in settings.disabled_scrapers
    ]

    date_str = datetime.now(KSA_TZ).strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("KSA EV Tender Monitor — Run started: %s", date_str)
    logger.info(
        "Settings: output_dir=%s seen_db=%s run_window_hours=%d close_window_days=%d "
        "telegram_enabled=%s dry_run=%s disabled_scrapers=%s",
        settings.output_dir,
        settings.seen_db_path,
        settings.run_window_hours,
        settings.close_window_days,
        settings.telegram_enabled,
        settings.dry_run,
        ", ".join(sorted(settings.disabled_scrapers)) or "none",
    )
    logger.info("=" * 60)

    # Step 1: Scrape all sites
    logger.info("Step 1: Scraping %d enabled sites...", len(selected_scrapers))
    all_tenders, scrape_stats = await run_scrapers(selected_scrapers)
    scrape_stats.extend(disabled_stats)
    logger.info("Total raw tenders scraped: %d", len(all_tenders))

    if not all_tenders:
        logger.warning("No tenders scraped from any site — check scrapers")

    # Step 2: Filter by keywords, dates, dedup
    logger.info("Step 2: Filtering tenders...")
    matched, diagnostics = filter_tenders(
        all_tenders,
        run_window_hours=settings.run_window_hours,
        close_window_days=settings.close_window_days,
        dry_run=settings.dry_run,
    )
    logger.info("Matched tenders after filtering: %d", len(matched))
    _log_filter_diagnostics(diagnostics)

    # Step 3: Write CSV (always, even if empty — for audit trail)
    csv_path = write_csv(
        matched,
        date_str,
        output_dir=settings.output_dir,
        append_master=not settings.dry_run,
    )

    # Step 4: Send Telegram alert (always — matches or no matches)
    tg_ok = False
    if settings.telegram_enabled:
        tg_ok = await send_telegram_alert(matched, date_str)
        if tg_ok:
            logger.info("Telegram alert sent successfully")
        else:
            logger.debug("Telegram alert skipped or failed (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    else:
        logger.info("Telegram alert disabled")

    dedup_marked = 0
    should_mark_seen = matched and not settings.dry_run and (not settings.telegram_enabled or tg_ok)
    if should_mark_seen:
        dedup_marked = mark_tenders_seen(matched)
        logger.info("Marked %d delivered tenders as seen", dedup_marked)
    elif matched and not settings.dry_run:
        logger.warning("Dedup marking deferred because Telegram delivery did not succeed")

    summary = build_run_summary(
        date_str=date_str,
        settings=settings,
        scrape_stats=scrape_stats,
        diagnostics=diagnostics,
        csv_path=csv_path,
        telegram_sent=tg_ok,
        dedup_marked=dedup_marked,
    )
    summary_path = write_run_summary(summary, settings.output_dir)

    # Summary
    logger.info("=" * 60)
    logger.info(
        "Run complete. CSV: %s | Summary: %s | Matches: %d",
        csv_path.name,
        summary_path.name,
        len(matched),
    )
    logger.info("=" * 60)
    return summary


async def execute_telegram_test(settings: RuntimeSettings) -> dict[str, Any]:
    global LOG_DIR

    LOG_DIR = Path(settings.output_dir)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(KSA_TZ).strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("KSA EV Tender Monitor — Telegram test started: %s", date_str)
    logger.info("Settings: output_dir=%s telegram_enabled=%s", settings.output_dir, settings.telegram_enabled)
    logger.info("=" * 60)

    tg_ok = False
    if settings.telegram_enabled:
        tg_ok = await send_telegram_test_message()
    else:
        logger.warning("Telegram test requested but Telegram is disabled")

    summary = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "telegram_test",
        "status": "ok" if tg_ok else "partial_failure",
        "settings": {
            "output_dir": str(settings.output_dir),
            "seen_db_path": str(settings.seen_db_path),
            "log_level": settings.log_level,
            "telegram_enabled": settings.telegram_enabled,
            "dry_run": settings.dry_run,
        },
        "totals": {
            "raw": 0,
            "matched": 0,
            "rejected": 0,
        },
        "scrapers": [],
        "filter_reject_counts": {},
        "matched_counts": {},
        "outputs": {
            "log_file": str(Path(settings.output_dir) / "tender_monitor.log"),
            "run_summary": str(Path(settings.output_dir) / "run_summary.json"),
        },
        "telegram": {
            "enabled": settings.telegram_enabled,
            "configured": telegram_credentials_configured() if settings.telegram_enabled else False,
            "sent": tg_ok,
        },
        "errors": [] if tg_ok else ["Telegram test message was not sent"],
    }
    write_run_summary(summary, settings.output_dir)

    if tg_ok:
        logger.info("Telegram test completed successfully")
    else:
        logger.error("Telegram test failed")

    return summary


async def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        settings = settings_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    configure_logging(settings.output_dir, settings.log_level)
    set_db_path(settings.seen_db_path)

    if args.telegram_test:
        summary = await execute_telegram_test(settings)
        if not summary["telegram"]["sent"]:
            sys.exit(1)
        return

    if args.purge:
        count = purge_old(days=90)
        logger.info("Purged %d old dedup records from %s", count, settings.seen_db_path)
        return

    await execute_run(settings)


if __name__ == "__main__":
    asyncio.run(main())
