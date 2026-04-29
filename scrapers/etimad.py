"""
Scraper for tenders.etimad.sa — the official Saudi government procurement portal.
Public visitor page: https://tenders.etimad.sa/Tender/AllTendersForVisitor

The page uses ASP.NET with JS rendering. Tender listings appear as
links to /Tender/DetailsForVisitor?STenderId=...
Each tender has a title, ref number, dates, and status.

Requires Playwright for JS rendering.
"""

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Tender
from utils.dates import parse_date

logger = logging.getLogger(__name__)


class EtimadScraper(BaseScraper):
    SITE_NAME = "Etimad"
    BASE_URL = "https://tenders.etimad.sa/Tender/AllTendersForVisitor"
    NEEDS_BROWSER = True

    MAX_PAGES = 3

    async def scrape(self, browser=None) -> list[Tender]:
        if browser is None:
            self.logger.error("Etimad requires a Playwright browser instance")
            return []

        tenders = []
        context = await browser.new_context(
            locale="ar-SA",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            for page_num in range(1, self.MAX_PAGES + 1):
                url = f"{self.BASE_URL}?PageNumber={page_num}"
                self.logger.info("Fetching Etimad page %d: %s", page_num, url)

                try:
                    await page.goto(url, wait_until="networkidle", timeout=60000)
                    await page.wait_for_timeout(3000)
                    html = await page.content()
                except Exception:
                    self.logger.exception("Failed to load Etimad page %d", page_num)
                    break

                page_tenders = self._parse_page(html)
                if not page_tenders:
                    self.logger.info("No tenders found on page %d — stopping", page_num)
                    break

                tenders.extend(page_tenders)
                self.logger.info("Found %d tenders on page %d", len(page_tenders), page_num)

        finally:
            await context.close()

        self.logger.info("Etimad total: %d tenders scraped", len(tenders))
        return tenders

    def _parse_page(self, html: str) -> list[Tender]:
        """Parse tenders from Etimad HTML."""
        soup = BeautifulSoup(html, "lxml")
        tenders = []

        # Find all tender detail links
        detail_links = soup.select('a[href*="DetailsForVisitor"]')

        # Group by unique tender ID to avoid duplicates (each tender may have
        # multiple links — title + "التفاصيل" button)
        seen_ids = {}
        for link in detail_links:
            href = link.get("href", "")
            text = link.get_text(strip=True)

            # Extract tender ID from URL
            tender_id = ""
            if "STenderId=" in href:
                tender_id = href.split("STenderId=")[-1]

            if not tender_id:
                continue

            # Keep the link with the longest text (the title, not "التفاصيل")
            if tender_id not in seen_ids or len(text) > len(seen_ids[tender_id]["text"]):
                seen_ids[tender_id] = {"text": text, "href": href}

        # Now parse each unique tender
        for tender_id, info in seen_ids.items():
            title = info["text"]
            if not title or len(title) < 5 or title == "التفاصيل":
                continue

            href = info["href"]
            link = self._full_url(href)

            # Try to find dates and other info near this tender in the DOM
            # The parent container usually has date information
            tender_data = self._extract_context(soup, href)

            tenders.append(Tender(
                site=self.SITE_NAME,
                title=title,
                ref_number=tender_id[:20],
                publish_date=tender_data.get("publish_date"),
                close_date=tender_data.get("close_date"),
                publish_date_raw=tender_data.get("publish_date_raw", ""),
                close_date_raw=tender_data.get("close_date_raw", ""),
                link=link,
            ))

        return tenders

    def _extract_context(self, soup: BeautifulSoup, href: str) -> dict:
        """Try to extract dates from the DOM near a tender link."""
        result = {
            "publish_date": None,
            "close_date": None,
            "publish_date_raw": "",
            "close_date_raw": "",
        }

        # Find the link element
        link_el = soup.select_one(f'a[href="{href}"]')
        if not link_el:
            return result

        # Walk up to find a container with date info
        container = link_el
        for _ in range(8):
            container = container.parent
            if container is None or container.name == "body":
                break

            text = container.get_text()
            # Look for date patterns (dd/mm/yyyy or yyyy-mm-dd)
            dates = re.findall(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}", text)
            if dates:
                for i, d in enumerate(dates[:2]):
                    parsed = parse_date(d)
                    if i == 0:
                        result["publish_date_raw"] = result["publish_date_raw"] or d
                        if parsed and not result["publish_date"]:
                            result["publish_date"] = parsed
                    elif i == 1:
                        result["close_date_raw"] = result["close_date_raw"] or d
                        if parsed and not result["close_date"]:
                            result["close_date"] = parsed
                if result["publish_date"] or result["close_date"]:
                    break

        return result

    def _full_url(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        return f"https://tenders.etimad.sa{href}"
