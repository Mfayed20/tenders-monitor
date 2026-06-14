"""
Scraper for ksatendersgate.com — WordPress-based KSA tender aggregator.

Uses the open WordPress REST API at /wp-json/wp/v2/tenders instead of
scraping JS-rendered pages. Much more reliable and faster.

Tender content is in HTML tables inside content.rendered with fields:
TenderID, Tender No, Tender Brief, Last Date of Bid Submission, etc.
"""

import logging
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
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    async def scrape(self, browser=None) -> list[Tender]:
        tenders = []

        async with self._build_client() as client:
            # Fetch all pages concurrently
            urls = [
                f"{self.API_URL}?per_page={self.PER_PAGE}&page={p}"
                for p in range(1, self.MAX_PAGES + 1)
            ]
            self.logger.info("Fetching KSAGate API pages 1-%d concurrently", self.MAX_PAGES)

            import asyncio
            results = await asyncio.gather(
                *[self._fetch_page(client, url, i + 1) for i, url in enumerate(urls)]
            )

            for page_num, data in enumerate(results, 1):
                if not data:
                    continue
                for item in data:
                    tender = self._parse_api_item(item)
                    if tender:
                        tenders.append(tender)
                self.logger.info("Found %d tenders on page %d", len(data), page_num)

        self.logger.info("KSAGate total: %d tenders scraped", len(tenders))
        return tenders

    def _build_client(self, *, verify: bool = True) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers=self.HEADERS,
            verify=verify,
        )

    async def _fetch_page(self, client, url: str, page_num: int):
        try:
            response = await self.fetch_response_with_retry(client, "GET", url)
            return self._json_records_from_response(response, page_num)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                return []
            self.logger.exception("Failed to fetch KSAGate API page %d", page_num)
            self.record_run_error(f"Failed to fetch KSAGate API page {page_num}", exc)
            return []
        except Exception as exc:
            if self._is_ssl_verify_error(exc):
                fallback_data = await self._fetch_page_without_ssl_verify(url, page_num)
                if fallback_data is not None:
                    return fallback_data

            self.logger.exception("Failed to fetch KSAGate API page %d", page_num)
            self.record_run_error(f"Failed to fetch KSAGate API page {page_num}", exc)
            return []

    async def _fetch_page_without_ssl_verify(self, url: str, page_num: int):
        self.logger.warning("Retrying KSAGate API page %d without TLS certificate verification", page_num)
        try:
            async with self._build_client(verify=False) as client:
                response = await self.fetch_response_with_retry(client, "GET", url)
                return self._json_records_from_response(response, page_num)
        except Exception as exc:
            self.logger.exception("Failed to fetch KSAGate API page %d with SSL fallback", page_num)
            self.record_run_error(f"Failed to fetch KSAGate API page {page_num} with SSL fallback", exc)
            return None

    def _json_records_from_response(self, response, page_num: int) -> list:
        data = response.json()
        if isinstance(data, list):
            return data

        payload_type = type(data).__name__
        self.logger.warning("KSAGate API page %d returned unexpected payload: %s", page_num, payload_type)
        self.record_run_error(f"KSAGate API page {page_num} returned unexpected payload: {payload_type}")
        return []

    @staticmethod
    def _is_ssl_verify_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "certificate_verify_failed" in message or "self-signed certificate" in message

    def _parse_api_item(self, item: dict) -> Tender | None:
        """Parse a tender from the WordPress REST API response."""
        title = unescape(item.get("title", {}).get("rendered", "")).strip()
        if not title:
            return None

        link = self.build_source_url(str(item.get("link", "")), base_url=self.BASE_URL)
        publish_date_raw = str(item.get("date", "")).strip()
        publish_date = parse_date(publish_date_raw)

        # Parse the HTML content for structured fields
        content_html = item.get("content", {}).get("rendered", "")
        ref_number, close_date, close_date_raw, description = self._parse_content(content_html)

        return Tender(
            site=self.SITE_NAME,
            title=title,
            ref_number=ref_number,
            publish_date=publish_date,
            close_date=close_date,
            publish_date_raw=publish_date_raw,
            close_date_raw=close_date_raw,
            link=link,
            description=description,
        )

    def _parse_content(self, html: str) -> tuple[str, object, str, str]:
        """Extract ref number, close date, and description from content HTML tables."""
        if not html:
            return "", None, "", ""

        soup = BeautifulSoup(html, "lxml")
        ref_number = ""
        close_date = None
        close_date_raw = ""
        description_parts: list[str] = []

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
                close_date_raw = close_date_raw or value
                close_date = close_date or parse_date(value)
            elif (
                "tender brief" in label
                or "work detail" in label
                or "scope" in label
                or "sector" in label
            ):
                if value:
                    description_parts.append(value)

        description = " | ".join(dict.fromkeys(description_parts))[:500]
        return ref_number, close_date, close_date_raw, description
