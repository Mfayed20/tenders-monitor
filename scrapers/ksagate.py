"""
Scraper for ksatendersgate.com — WordPress-based KSA tender aggregator.

Uses the open WordPress REST API at /wp-json/wp/v2/tenders instead of
scraping JS-rendered pages. Much more reliable and faster.

Tender content is in HTML tables inside content.rendered with fields:
TenderID, Tender No, Tender Brief, Last Date of Bid Submission, etc.
"""

import logging
import re
from html import unescape

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Tender
from utils.dates import parse_date

logger = logging.getLogger(__name__)


class KSAGateScraper(BaseScraper):
    SITE_NAME = "KSATendersGate"
    BASE_URL = "https://ksatendersgate.com"
    API_URL = "https://ksatendersgate.com/wp-json/wp/v2/tenders"
    NEEDS_BROWSER = False

    PER_PAGE = 100
    MAX_PAGES = 3

    async def scrape(self, browser=None) -> list[Tender]:
        tenders = []

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
            },
        ) as client:
            for page_num in range(1, self.MAX_PAGES + 1):
                url = f"{self.API_URL}?per_page={self.PER_PAGE}&page={page_num}"
                self.logger.info("Fetching KSAGate API page %d", page_num)

                try:
                    response = await client.get(url)
                    if response.status_code == 400:
                        # WP returns 400 when page exceeds total
                        self.logger.info("No more pages at page %d", page_num)
                        break
                    response.raise_for_status()
                    data = response.json()
                except Exception:
                    self.logger.exception("Failed to fetch KSAGate API page %d", page_num)
                    break

                if not data:
                    self.logger.info("Empty response at page %d — stopping", page_num)
                    break

                for item in data:
                    tender = self._parse_api_item(item)
                    if tender:
                        tenders.append(tender)

                self.logger.info("Found %d tenders on page %d", len(data), page_num)

        self.logger.info("KSAGate total: %d tenders scraped", len(tenders))
        return tenders

    def _parse_api_item(self, item: dict) -> Tender | None:
        """Parse a tender from the WordPress REST API response."""
        title = unescape(item.get("title", {}).get("rendered", "")).strip()
        if not title:
            return None

        link = item.get("link", "")
        publish_date = parse_date(item.get("date", ""))

        # Parse the HTML content for structured fields
        content_html = item.get("content", {}).get("rendered", "")
        ref_number, close_date, description = self._parse_content(content_html)

        return Tender(
            site=self.SITE_NAME,
            title=title,
            ref_number=ref_number,
            publish_date=publish_date,
            close_date=close_date,
            link=link,
            description=description,
        )

    def _parse_content(self, html: str) -> tuple[str, ..., str]:
        """Extract ref number, close date, and description from content HTML tables."""
        if not html:
            return "", None, ""

        soup = BeautifulSoup(html, "lxml")
        ref_number = ""
        close_date = None
        description = ""

        rows = soup.select("tr")
        for row in rows:
            cells = row.select("td")
            if len(cells) < 2:
                continue

            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)

            if "tenderid" in label or "tender no" in label:
                ref_number = ref_number or value
            elif "last date" in label or "bid submission" in label or "closing" in label:
                close_date = close_date or parse_date(value)
            elif "tender brief" in label or "work detail" in label:
                description = description or value[:500]

        return ref_number, close_date, description
