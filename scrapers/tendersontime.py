"""
Scraper for tendersontime.com — Saudi Arabia tenders.
URL: https://www.tendersontime.com/saudi-arabia-tenders/

Uses a direct POST API that returns JSON with tender data.
Endpoint: POST /ApiTenders/getTenderDetails
Content-Type: application/x-www-form-urlencoded (NOT JSON)
"""

import logging

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from scrapers.base import BaseScraper, Tender
from utils.dates import parse_date

logger = logging.getLogger(__name__)


def _is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return False


class TendersOnTimeScraper(BaseScraper):
    SITE_NAME = "TendersOnTime"
    BASE_URL = "https://www.tendersontime.com"
    LISTING_URL = "https://www.tendersontime.com/saudi-arabia-tenders/"
    API_URL = "https://www.tendersontime.com/ApiTenders/getTenderDetails"
    NEEDS_BROWSER = False

    MAX_PAGES = 3  # 3 pages of results
    RETRY_WAIT = wait_exponential(multiplier=2, min=2, max=10)

    async def _fetch_page(self, client: httpx.AsyncClient, page_num: int) -> dict | list:
        payload = f"regionkey=saudi-arabia-tenders&startpage={page_num}"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=self.RETRY_WAIT,
            retry=retry_if_exception(_is_retryable_http_error),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                response = await client.post(
                    self.API_URL,
                    content=payload,
                )
                response.raise_for_status()
                return response.json()

        return {}

    async def _fetch_listing_html(self, client: httpx.AsyncClient) -> str:
        response = await client.get(self.LISTING_URL)
        response.raise_for_status()
        return response.text

    def _parse_listing_page(self, html: str) -> list[Tender]:
        soup = BeautifulSoup(html, "lxml")
        tenders: list[Tender] = []
        seen_ids = set()

        for box in soup.select(".listingbox"):
            title_link = box.select_one("a.givemeEllipsis2[href], a[href*='/tenders-details/']")
            title_node = box.select_one(".listing-summary")
            title = (
                title_node.get_text(" ", strip=True)
                if title_node
                else title_link.get_text(" ", strip=True) if title_link else ""
            )
            if not title or len(title) < 5:
                continue

            country = box.select_one(".purchase-box strong")
            if country and "saudi" not in country.get_text(" ", strip=True).lower():
                continue

            ref_number = ""
            close_date_str = ""
            for item in box.select(".list-data"):
                text = item.get_text(" ", strip=True)
                strong = item.find("strong")
                value = strong.get_text(" ", strip=True) if strong else ""
                if "TOT Reference No" in text:
                    ref_number = value
                elif "Deadline" in text and not close_date_str:
                    close_date_str = value

            link = title_link.get("href", "").strip() if title_link else ""
            link = self.build_source_url(link, base_url=self.BASE_URL)

            dedup_key = ref_number or link or title
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            tenders.append(
                Tender(
                    site=self.SITE_NAME,
                    title=title,
                    ref_number=ref_number,
                    publish_date=None,
                    close_date=parse_date(close_date_str) if close_date_str else None,
                    close_date_raw=close_date_str,
                    link=link,
                    description=title,
                )
            )

        return tenders

    async def scrape(self, browser=None) -> list[Tender]:
        tenders = []
        seen_ids = set()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
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
                    data = await self._fetch_page(client, page_num)
                except Exception as exc:
                    if page_num == 1:
                        self.logger.warning(
                            "TendersOnTime API page 1 failed; falling back to listing HTML: %s",
                            exc,
                        )
                        try:
                            fallback_html = await self._fetch_listing_html(client)
                            tenders.extend(self._parse_listing_page(fallback_html))
                            break
                        except Exception as fallback_exc:
                            self.logger.exception("Failed to fetch TendersOnTime listing fallback")
                            self.record_run_error(
                                "Failed to fetch TendersOnTime listing fallback",
                                fallback_exc,
                            )
                            break

                    self.logger.exception(
                        "Failed to fetch TendersOnTime page %d", page_num
                    )
                    self.record_run_error(f"Failed to fetch TendersOnTime page {page_num}", exc)
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
        url = self.build_source_url(str(detlink), base_url=self.BASE_URL)

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
