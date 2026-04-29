"""
Scraper for tendersa.com — Saudi Arabia tender aggregator.
URL: https://www.tendersa.com/TendersSearch.aspx

Uses Playwright since the listing page is ASP.NET with dynamic content.
Tenders are in .details-wrapper containers with structured fields:
- Title: .FirstRowTndrNam h4 a
- Publish date: .take-off div
- Close date: .landing div
- Status: .total-time span
- Ref ID: span[id*="lblTenderJoID"]
"""

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Tender
from utils.dates import parse_date

logger = logging.getLogger(__name__)


class TendersaScraper(BaseScraper):
    SITE_NAME = "TenderSA"
    BASE_URL = "https://www.tendersa.com"
    SEARCH_URL = "https://www.tendersa.com/TendersSearch.aspx"
    NEEDS_BROWSER = True

    async def scrape(self, browser=None) -> list[Tender]:
        if browser is None:
            self.logger.error("TenderSA requires a Playwright browser instance")
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
            self.logger.info("Fetching TenderSA search page")
            html = await self.fetch_with_browser(
                page, self.SEARCH_URL,
                wait_selector=".details-wrapper",
            )
            tenders = self._parse_page(html)
            self.logger.info("Found %d tenders on TenderSA", len(tenders))

        except Exception as exc:
            self.logger.exception("Failed to scrape TenderSA")
            self.record_run_error("Failed to scrape TenderSA", exc)
        finally:
            await context.close()

        self.logger.info("TenderSA total: %d tenders scraped", len(tenders))
        return tenders

    def _parse_page(self, html: str) -> list[Tender]:
        soup = BeautifulSoup(html, "lxml")
        tenders = []

        wrappers = soup.select(".details-wrapper")
        for wrapper in wrappers:
            tender = self._parse_wrapper(wrapper)
            if tender:
                tenders.append(tender)

        return tenders

    def _parse_wrapper(self, wrapper) -> Tender | None:
        """Parse a single .details-wrapper into a Tender."""
        # Title and link
        title_el = wrapper.select_one(
            ".FirstRowTndrNam h4 a[href*='TenderDetails'], "
            ".FirstRowTndrNam a[href*='TenderDetails'], "
            "h4 a[href*='TenderDetails'], "
            "a[href*='TenderDetails']"
        )
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        href = title_el.get("href", "")
        link = self._full_url(href)
        wrapper_text = re.sub(r"\s+", " ", wrapper.get_text(" ", strip=True))

        # Publish date
        publish_date = None
        publish_date_raw = ""
        takeoff = wrapper.select_one(".take-off")
        if takeoff:
            text = takeoff.get_text()
            date_match = self._find_date(text)
            if date_match:
                publish_date_raw = date_match
                publish_date = parse_date(publish_date_raw)
        if not publish_date_raw:
            publish_date_raw = self._find_labeled_date(wrapper_text, "Publish Date")
            publish_date = parse_date(publish_date_raw)

        # Close date
        close_date = None
        close_date_raw = ""
        landing = wrapper.select_one(".landing")
        if landing:
            text = landing.get_text()
            date_match = self._find_date(text)
            if date_match:
                close_date_raw = date_match
                close_date = parse_date(close_date_raw)
        if not close_date_raw:
            close_date_raw = self._find_labeled_date(wrapper_text, "Deadline")
            close_date = parse_date(close_date_raw)

        # Ref ID
        ref_el = wrapper.select_one("span[id*='lblTenderJoID']")
        ref_number = ref_el.get_text(strip=True) if ref_el else ""
        if not ref_number:
            ref_match = re.search(r"\bTGID\s+(\d+)\b", wrapper_text, flags=re.I)
            if ref_match:
                ref_number = ref_match.group(1)

        # Extract tdc_id from link as fallback ref
        if not ref_number and "tdc_id=" in href:
            ref_number = href.split("tdc_id=")[-1]

        return Tender(
            site=self.SITE_NAME,
            title=title,
            ref_number=ref_number,
            publish_date=publish_date,
            close_date=close_date,
            publish_date_raw=publish_date_raw,
            close_date_raw=close_date_raw,
            link=link,
            description=wrapper_text[:500],
        )

    @staticmethod
    def _find_date(text: str) -> str:
        date_match = re.search(
            r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
            text,
        )
        return date_match.group(1) if date_match else ""

    def _find_labeled_date(self, text: str, label: str) -> str:
        match = re.search(
            rf"{re.escape(label)}\s+"
            r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2},\s+\d{4})",
            text,
            flags=re.I,
        )
        return match.group(1) if match else ""

    def _full_url(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        return f"{self.BASE_URL}/{href.lstrip('/')}"
