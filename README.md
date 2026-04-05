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
```

## Matching Rules

- `Climatech Charger` focuses on EV charging, installation, and charging infrastructure tenders.
- `EVS` focuses on EV fleet and vehicle service work such as maintenance, repair, diagnostics, firmware, battery-module work, workshops, bodywork, and spare parts.
- Matching is intentionally strict: generic electrical-material tenders like `مواد كهربائية`, `قواطع`, generic software-development work, and non-EV energy-storage infrastructure should not alert.
- Keyword rules are maintained in `config/keywords.yaml`, with matcher logic in `utils/keywords.py`.

## Testing

Install `pytest` in the virtual environment if needed:

```bash
python -m pip install pytest
```

Run the regression tests:

```bash
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
