# KSA EV Tender Monitor

A daily scraper that monitors Saudi Arabia tender websites for EV-related opportunities, filters results by keyword, and sends Telegram alerts.

Built for two companies:
- **Climatech Charger** — EV chargers, installation, infrastructure, CPO
- **EVS** — fleet maintenance, service, repair, management

## How It Works

1. Scrapes 6 tender sources concurrently (HTTP-based) or via headless browser (JS-heavy sites)
2. Filters tenders by precision-first EV business rules in English and Arabic
3. Uses company-specific matching for Climatech Charger and EVS so generic electrical, software, and non-EV supply tenders do not trigger alerts
4. Deduplicates against previously seen tenders (SQLite)
5. Writes results to a latest-run daily snapshot CSV and a cumulative master CSV
6. Sends a Telegram digest to the configured chat, even when no new matches are found

## Sources

| Scraper | Site |
|---|---|
| Etimad | etimad.sa |
| KSA Gate | ksagate.com |
| Tenders.sa | tenders.sa |
| TendersInfo | tendersinfo.com |
| ME Tenders | metenders.net |
| Tenders on Time | tendersontime.com |

## Setup

**1. Clone and create virtual environment**
```bash
git clone https://github.com/Mfayed20/tenders-monitor.git
cd tenders-monitor
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

**2. Configure environment**
```bash
cp .env.example .env
```

Edit `.env` with your Telegram bot details:
```env
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

To get these values:
1. Create a bot with `@BotFather` and copy the bot token
2. Send a message to the bot
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your `chat_id`

## Usage

```bash
# Full run: scrape + filter + CSV + Telegram
python main.py

# Purge dedup records older than 90 days
python main.py --purge

# Inspect a run without sending Telegram or marking tenders as seen
python main.py --dry-run --no-telegram

# Override runtime paths/windows
python main.py --output-dir output --seen-db output/seen_tenders.db --run-window-hours 168 --close-window-days 30
```

### Runtime Configuration

Each CLI option can also be configured through environment variables:

| Setting | CLI | Environment |
|---|---|---|
| Output directory | `--output-dir` | `TENDER_OUTPUT_DIR` |
| Dedup SQLite path | `--seen-db` | `TENDER_SEEN_DB_PATH` |
| Log level | `--log-level` | `TENDER_LOG_LEVEL` |
| Publish window | `--run-window-hours` | `TENDER_RUN_WINDOW_HOURS` |
| Closing window | `--close-window-days` | `TENDER_CLOSE_WINDOW_DAYS` |
| Disabled scrapers | `--disable-scraper` | `TENDER_DISABLED_SCRAPERS` |
| Telegram enabled | `--no-telegram` | `TENDER_TELEGRAM_ENABLED=false` |
| Dry run | `--dry-run` | `TENDER_DRY_RUN=true` |

`--disable-scraper` accepts site names such as `Etimad`, `TenderSA`, or `TendersInfo`; it can be repeated or comma-separated.

## Matching Rules

- `Climatech Charger` focuses on EV charging, installation, and charging infrastructure tenders.
- `EVS` focuses on EV fleet and vehicle service work such as maintenance, repair, diagnostics, firmware, battery-module work, workshops, bodywork, and spare parts.
- Matching is intentionally strict: generic electrical-material tenders like `مواد كهربائية`, `قواطع`, generic software-development work, and non-EV energy-storage infrastructure should not alert.
- Keyword rules are maintained in `config/keywords.yaml`, with matcher logic in `utils/keywords.py`.

## Testing

Install development dependencies in the virtual environment:

```bash
python -m pip install -r requirements-dev.txt
```

Run the regression checks:

```bash
python -m compileall -q .
python -m ruff check .
python -m pytest -q
```

## Output

All output is saved to the `output/` directory (gitignored):

| File | Description |
|---|---|
| `tenders_YYYY-MM-DD.csv` | Latest-run daily snapshot of matched tenders |
| `all_tenders.csv` | Cumulative master CSV |
| `seen_tenders.db` | SQLite dedup database |
| `tender_monitor.log` | Run logs |
| `run_summary.json` | Machine-readable run status, scraper stats, filter diagnostics, outputs, and Telegram result |

## Project Structure

```
tenders-monitor/
├── main.py                  # Entry point and pipeline
├── requirements.txt
├── .env.example
├── scrapers/
│   ├── base.py              # Tender dataclass and base scraper
│   ├── etimad.py
│   ├── ksagate.py
│   ├── metenders.py
│   ├── tendersa.py
│   ├── tendersinfo.py
│   └── tendersontime.py
└── utils/
    ├── dates.py             # Date parsing and filtering
    ├── dedup.py             # SQLite deduplication
    ├── keywords.py          # EV keyword matching (EN + AR)
    └── telegram_notifier.py # Telegram digest sender
```

## Automating Daily Runs

Use a cron job (Linux/Mac) or Task Scheduler (Windows) to run daily:

```bash
# Run every day at 8:00 AM
0 8 * * * cd /path/to/tenders-monitor && venv/bin/python main.py
```

## GitHub Actions

This repo includes:

- `.github/workflows/ci.yml` — compile, lint, and test on push/PR.
- `.github/workflows/daily-monitor.yml` — scheduled daily run at `05:00 UTC` / `08:00 Asia/Riyadh`, plus manual dispatch.

Configure these repository secrets before enabling the daily workflow:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

The daily workflow restores and saves `output/seen_tenders.db` through the GitHub Actions cache, then uploads CSVs, logs, the dedup DB, and `run_summary.json` as 30-day artifacts.
