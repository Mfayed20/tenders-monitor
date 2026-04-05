"""
Scraper for tendersinfo.com — international tender aggregator.
URL: https://www.tendersinfo.com/global-saudi-arabia-tenders.php

Uses the DataTables AJAX API directly instead of scraping rendered HTML.
Endpoint: POST /esearch/results_test/{search_text}/{type}
Returns JSON with tender data.
"""

import logging

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Tender
from utils.dates import parse_date
from utils.keywords import TENDERSINFO_QUERIES

logger = logging.getLogger(__name__)


class TendersInfoScraper(BaseScraper):
    SITE_NAME = "TendersInfo"
    BASE_URL = "https://www.tendersinfo.com"
    API_URL = "https://www.tendersinfo.com/esearch/results_test"
    NEEDS_BROWSER = False

    # Search terms — the /location endpoint already filters to Saudi Arabia
    SEARCH_QUERIES = TENDERSINFO_QUERIES
    PAGE_SIZE = 50

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
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.tendersinfo.com/global-saudi-arabia-tenders.php",
                "Origin": "https://www.tendersinfo.com",
            },
        ) as client:
            # Fetch all queries concurrently
            import asyncio

            async def _fetch_query(query):
                encoded = query.replace(" ", "%20")
                url = f"{self.API_URL}/{encoded}/location"
                self.logger.info("Fetching TendersInfo API: %s", query)
                try:
                    payload = {
                        "draw": "1",
                        "start": "0",
                        "length": str(self.PAGE_SIZE),
                        "columns[0][data]": "site_tender_id",
                        "columns[1][data]": "region_name",
                        "columns[2][data]": "tender_sector",
                        "columns[3][data]": "short_desc",
                        "columns[4][data]": "date_c",
                        "columns[5][data]": "doc_last",
                    }
                    response = await client.post(url, data=payload)
                    response.raise_for_status()
                    return query, response.json()
                except Exception:
                    self.logger.exception("Failed to fetch TendersInfo for query: %s", query)
                    return query, None

            results = await asyncio.gather(
                *[_fetch_query(q) for q in self.SEARCH_QUERIES]
            )

            for query, data in results:
                if data is None:
                    continue

                records = data.get("data", [])
                self.logger.info(
                    "TendersInfo query '%s': %d records (total: %s)",
                    query, len(records), data.get("recordsTotal", "?"),
                )

                for record in records:
                    tender_id = record.get("site_tender_id", "")
                    if tender_id in seen_ids:
                        continue
                    seen_ids.add(tender_id)

                    # Filter: Saudi Arabia only
                    region = self._strip_html(record.get("region_name", ""))
                    if not self._is_saudi(region):
                        continue

                    tender = self._parse_record(record)
                    if tender:
                        tenders.append(tender)

        self.logger.info("TendersInfo total: %d tenders scraped", len(tenders))
        return tenders

    def _parse_record(self, record: dict) -> Tender | None:
        """Parse a tender from the DataTables API JSON record."""
        # Fields may contain HTML — strip tags
        title = self._strip_html(record.get("short_desc", ""))
        if not title or len(title) < 5:
            return None

        ref_number = str(record.get("site_tender_id", "")).strip()
        close_date_str = self._strip_html(record.get("doc_last", ""))
        publish_date_str = self._strip_html(record.get("date_c", ""))

        # Build the detail URL
        url = record.get("url", "")
        if url and not url.startswith("http"):
            url = f"{self.BASE_URL}/{url.lstrip('/')}"

        description_parts = [
            self._strip_html(record.get("tender_sector", "")),
            self._strip_html(record.get("region_name", "")),
        ]
        description = " | ".join(part for part in description_parts if part)

        return Tender(
            site=self.SITE_NAME,
            title=title,
            ref_number=ref_number,
            publish_date=parse_date(publish_date_str),
            close_date=parse_date(close_date_str),
            publish_date_raw=publish_date_str,
            close_date_raw=close_date_str,
            link=url,
            description=description[:500],
        )

    @staticmethod
    def _is_saudi(region: str) -> bool:
        """Check if a region string refers to Saudi Arabia."""
        r = region.lower()
        return any(kw in r for kw in ["saudi", "ksa", "riyadh", "jeddah", "dammam", "mecca", "medina"])

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags from a string."""
        if not text:
            return ""
        if "<" in str(text):
            soup = BeautifulSoup(str(text), "lxml")
            return soup.get_text(strip=True)
        return str(text).strip()
