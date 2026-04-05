"""Quick test — sends a sample tender alert via Telegram to verify setup."""

import asyncio
from dataclasses import dataclass

from dotenv import load_dotenv
from utils.telegram_notifier import send_telegram_alert


@dataclass
class TenderRow:
    site: str
    title: str
    ref_number: str
    publish_date: str
    close_date: str
    days_left: int | None
    link: str
    company_match: str
    matched_keywords: str
    description: str

load_dotenv()

SAMPLE_TENDERS = [
    TenderRow(
        site="Etimad",
        title="Supply and Installation of EV Charging Stations - Riyadh",
        ref_number="ETM-2025-001",
        publish_date="29 Mar 2025",
        close_date="10 Apr 2025",
        days_left=12,
        link="https://etimad.sa",
        company_match="Climatech",
        matched_keywords="ev charging, charging station",
        description="Supply, installation and commissioning of EV chargers across 5 locations.",
    ),
    TenderRow(
        site="KSAGate",
        title="Electric Fleet Maintenance Contract - Jeddah",
        ref_number="KSA-2025-042",
        publish_date="28 Mar 2025",
        close_date="02 Apr 2025",
        days_left=4,
        link="https://ksatendersgate.com",
        company_match="EVS",
        matched_keywords="fleet maintenance, ev fleet",
        description="Annual maintenance contract for 80-vehicle electric fleet.",
    ),
]

async def main():
    print("Sending test Telegram alert...")
    ok = await send_telegram_alert(SAMPLE_TENDERS, "2025-03-29")
    if ok:
        print("SUCCESS! Check your Telegram - you should have received a message.")
    else:
        print("FAILED. Check that TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set correctly in .env")

if __name__ == "__main__":
    asyncio.run(main())
