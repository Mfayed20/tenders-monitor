"""
Scraper for tendersontime.com — Saudi Arabia tenders.
URL: https://www.tendersontime.com/saudi-arabia-tenders/

Uses a direct POST API that returns JSON with tender data.
Endpoint: POST /ApiTenders/getTenderDetails
Content-Type: application/x-www-form-urlencoded (NOT JSON)
"""

import logging

import httpx

from scrapers.base import BaseScraper, Tender
from utils.dates import parse_date

logger = logging.getLogger(__name__)


class TendersOnTimeScraper(BaseScraper):
    SITE_NAME = "TendersOnTime"
    BASE_URL = "https://www.tendersontime.com"
    API_URL = "https://www.tendersontime.com/ApiTenders/getTenderDetails"
    NEEDS_BROWSER = False

    MAX_PAGES = 3  # 3 pages of results

    async def scrape(self, browser=None) -> list[Tender]:
        tenders = []
        seen_ids = set()

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.tendersontime.com/saudi-arabia-tenders/",
                "Origin": "https://www.tendersontime.com",
            },
        ) as client:
            for page_num in range(1, self.MAX_PAGES + 1):
                self.logger.info("Fetching TendersOnTime page %d", page_num)

                try:
                    payload = f"regionkey=saudi-arabia-tenders&startpage={page_num}"
                    response = await client.post(
                        self.API_URL,
                        content=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                except Exception:
                    self.logger.exception(
                        "Failed to fetch TendersOnTime page %d", page_num
                    )
                    break

                records = (
                    data.get("tenderDetails", [])
                    if isinstance(data, dict)
                    else data
                )
                self.logger.info(
                    "TendersOnTime page %d: %d records", page_num, len(records)
                )

                if not records:
                    break

                for record in records:
                    # Safety net: only Saudi Arabia
                    country = str(record.get("Country_Name", "")).lower()
                    if "saudi" not in country and country:
                        continue

                    tender_id = str(record.get("ID", ""))
                    if tender_id in seen_ids:
                        continue
                    seen_ids.add(tender_id)

                    tender = self._parse_record(record)
                    if tender:
                        tenders.append(tender)

        self.logger.info("TendersOnTime total: %d tenders scraped", len(tenders))
        return tenders

    def _parse_record(self, record: dict) -> Tender | None:
        """Parse a tender from the API JSON record."""
        title = str(record.get("Tender_Summery", "")).strip()
        if not title or len(title) < 5:
            return None

        ref_number = str(record.get("ID", "")).strip()
        close_date_str = str(record.get("Bid_Deadline_1", "")).strip()

        # Build detail URL
        detlink = record.get("detlink", "")
        if detlink and not detlink.startswith("http"):
            url = f"{self.BASE_URL}/{detlink.lstrip('/')}"
        else:
            url = detlink or ""

        return Tender(
            site=self.SITE_NAME,
            title=title,
            ref_number=ref_number,
            publish_date=None,
            close_date=parse_date(close_date_str) if close_date_str else None,
            close_date_raw=close_date_str,
            link=url,
            description=str(record.get("Description", "")),
        )
