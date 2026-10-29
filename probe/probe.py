"""One-off live probe of the fixed monitor (not part of it): a full dry run that reads three weeks
of Etimad and saves every tender seen, with the match result, so misses can be checked by hand."""
import asyncio, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["TENDER_FEED_PATH"] = str(OUT / "tenders.json")
import main  # noqa: E402
from scrapers.etimad import EtimadScraper  # noqa: E402
from utils.keywords import match_tender  # noqa: E402

EtimadScraper.LOOKBACK_HOURS = 21 * 24
EtimadScraper.API_MAX_PAGES = 400

seen = {}
real_filter = main.filter_tenders


def spy(tenders, *a, **k):
    for t in tenders:
        key = (t.site, t.ref_number or t.link or t.title)
        if key in seen:
            continue
        m = match_tender(t.title, t.description)
        seen[key] = {"site": t.site, "ref": t.ref_number, "title": t.title, "description": t.description[:400],
                     "published": t.publish_date.isoformat() if t.publish_date else t.publish_date_raw,
                     "closes": t.close_date.isoformat() if t.close_date else t.close_date_raw,
                     "activity": (t.raw_data or {}).get("tenderActivityName", ""),
                     "matched": m.matched, "company": m.company, "keywords": m.matched_keywords}
    return real_filter(tenders, *a, **k)


main.filter_tenders = spy
asyncio.run(main.main(["--dry-run", "--no-telegram", "--output-dir", str(OUT), "--seen-db", str(OUT / "seen.db")]))
with open(OUT / "raw_all.jsonl", "w", encoding="utf-8") as f:
    for row in seen.values():
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
s = json.load(open(OUT / "run_summary.json"))
print(json.dumps(s["scrapers"], indent=1))
print(len(seen), "tenders saved")
