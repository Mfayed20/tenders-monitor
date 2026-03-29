# KSA EV Tender Monitor

A daily scraper that monitors Saudi Arabia tender websites for EV-related opportunities, filters results by keyword, and sends HTML email alerts.

Built for two companies:
- **Climatech Charger** — EV chargers, installation, infrastructure, CPO
- **EVS** — fleet maintenance, service, repair, management

## How It Works

1. Scrapes 6 tender sources concurrently (HTTP-based) or via headless browser (JS-heavy sites)
2. Filters tenders by EV keywords in English and Arabic
3. Deduplicates against previously seen tenders (SQLite)
4. Writes results to a daily CSV and a cumulative master CSV
5. Sends an HTML email digest to configured recipients

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

Edit `.env` with your Gmail credentials and recipient emails:
```env
GMAIL_USER=your-email@gmail.com
GMAIL_APP_PASSWORD=your-app-password
NOTIFY_EMAILS=recipient1@example.com,recipient2@example.com
```

> Use a [Gmail App Password](https://myaccount.google.com/apppasswords), not your real password.

## Usage

```bash
# Full run: scrape + filter + email
python main.py

# Scrape and save CSV only (no email)
python main.py --no-email

# Purge dedup records older than 90 days
python main.py --purge
```

## Output

All output is saved to the `output/` directory (gitignored):

| File | Description |
|---|---|
| `tenders_YYYY-MM-DD.csv` | Daily matched tenders |
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
    └── notifier.py          # HTML email builder and sender
```

## Automating Daily Runs

Use a cron job (Linux/Mac) or Task Scheduler (Windows) to run daily:

```bash
# Run every day at 8:00 AM
0 8 * * * cd /path/to/tenders-monitor && venv/bin/python main.py
```
