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
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Tender
from utils.dates import KSA_TZ, parse_date
from utils.keywords import match_tender

logger = logging.getLogger(__name__)


class EtimadScraper(BaseScraper):
    SITE_NAME = "Etimad"
    BASE_URL = "https://tenders.etimad.sa/Tender/AllTendersForVisitor"
    NEEDS_BROWSER = True

    MAX_PAGES = 15
    GREGORIAN_DATE_RE = re.compile(
        r"(?P<date>\d{1,2}/\d{1,2}/20\d{2}|\d{4}-\d{1,2}-\d{1,2})"
        r"(?:\s+\d{1,2}/\d{1,2}/1\d{3})?"
        r"(?:\s+(?P<time>\d{1,2}:\d{2})(?:\s*(?P<meridiem>AM|PM))?)?",
        re.IGNORECASE,
    )

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
                    html = await self._load_listing_page(page, url)
                except Exception as exc:
                    html = await page.content()
                    if "DetailsForVisitor" not in html:
                        self.logger.exception("Failed to load Etimad page %d", page_num)
                        self.record_run_error(f"Failed to load Etimad page {page_num}", exc)
                        break
                    self.logger.warning("Etimad page %d load raised %s, but detail links were available", page_num, exc)

                page_tenders = self._parse_page(html)
                if not page_tenders:
                    self.logger.info("No tenders found on page %d — stopping", page_num)
                    break

                page_tenders = await self._enrich_page_tenders(page, page_tenders)
                tenders.extend(page_tenders)
                self.logger.info("Found %d tenders on page %d", len(page_tenders), page_num)

        finally:
            await context.close()

        self.logger.info("Etimad total: %d tenders scraped", len(tenders))
        return tenders

    async def _load_listing_page(self, page, url: str) -> str:
        """Load a listing page without requiring every long-polling resource to go idle."""
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        try:
            await page.wait_for_selector('a[href*="DetailsForVisitor"]', state="attached", timeout=15000)
        except Exception as exc:
            self.logger.warning("Etimad detail links were not attached within timeout: %s", exc)

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            self.logger.debug("Etimad network did not go idle; continuing with current DOM")

        await page.wait_for_timeout(1000)
        return await page.content()

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
                tender_id = href.split("STenderId=")[-1].strip()

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
                ref_number=tender_id,
                publish_date=tender_data.get("publish_date"),
                close_date=tender_data.get("close_date"),
                publish_date_raw=tender_data.get("publish_date_raw", ""),
                close_date_raw=tender_data.get("close_date_raw", ""),
                link=link,
                raw_data={"etimad_stender_id": tender_id},
            ))

        return tenders

    async def _enrich_page_tenders(self, page, tenders: list[Tender]) -> list[Tender]:
        """Fetch detail pages for official reference numbers and deadline data."""
        enriched: list[Tender] = []
        for tender in tenders:
            if not tender.link or not self._should_enrich_detail(tender):
                enriched.append(tender)
                continue

            try:
                html = await self._load_detail_page(page, tender.link)
                enriched.append(self._parse_detail_page(html, tender))
            except Exception as exc:
                self.logger.warning("Failed to enrich Etimad tender detail %s: %s", tender.link, exc)
                enriched.append(tender)

        return enriched

    def _should_enrich_detail(self, tender: Tender) -> bool:
        return match_tender(tender.title, tender.description).matched

    async def _load_detail_page(self, page, url: str) -> str:
        """Load the basic detail tab plus the schedule tab."""
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_selector("body", state="attached", timeout=15000)
        await page.wait_for_timeout(1000)
        detail_html = await page.content()

        try:
            await page.locator("#tenderDatesTab").click(timeout=5000)
            await page.wait_for_selector("#d-2.active, #d-2.show", state="attached", timeout=5000)
            await page.wait_for_timeout(1000)
            detail_html += "\n" + await page.content()
        except Exception as exc:
            self.logger.debug("Etimad schedule tab was not available for %s: %s", url, exc)

        return detail_html

    def _parse_detail_page(self, html: str, tender: Tender) -> Tender:
        """Parse official Etimad fields from a tender detail page."""
        lines = self._text_lines(html)
        title = self._value_after_label(lines, "اسم المنافسة") or tender.title
        competition_number = self._value_after_label(lines, "رقم المنافسة")
        official_ref = self._value_after_label(lines, "الرقم المرجعي")
        description = self._value_after_label(lines, "الغرض من المنافسة") or tender.description
        status = self._value_after_label(lines, "حالة المنافسة")
        close_date_raw = self._date_after_label(lines, "آخر موعد لتقديم العروض")
        close_date = self._parse_etimad_gregorian_date(close_date_raw) if close_date_raw else None

        raw_data = dict(tender.raw_data)
        if competition_number:
            raw_data["etimad_competition_number"] = competition_number
        if status:
            raw_data["etimad_status"] = status

        return replace(
            tender,
            title=title,
            ref_number=official_ref or tender.ref_number,
            close_date=close_date or tender.close_date,
            close_date_raw=close_date_raw or tender.close_date_raw,
            description=description,
            raw_data=raw_data,
        )

    def _text_lines(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        return [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]

    def _value_after_label(self, lines: list[str], label: str) -> str:
        for index, line in enumerate(lines[:-1]):
            if line == label:
                return lines[index + 1].strip()
        return ""

    def _date_after_label(self, lines: list[str], label: str) -> str:
        value = ""
        for index, line in enumerate(lines[:-1]):
            if line != label:
                continue
            block = " ".join(lines[index + 1:index + 5])
            extracted = self._extract_gregorian_datetime(block)
            if extracted:
                value = extracted
        return value

    def _extract_gregorian_datetime(self, value: str) -> str:
        match = self.GREGORIAN_DATE_RE.search(value or "")
        if not match:
            return ""

        parts = [match.group("date")]
        if match.group("time"):
            parts.append(match.group("time"))
        if match.group("meridiem"):
            parts.append(match.group("meridiem").upper())
        return " ".join(parts)

    def _parse_etimad_gregorian_date(self, value: str) -> datetime | None:
        parsed = parse_date(value)
        if parsed:
            return parsed

        for fmt in ("%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=KSA_TZ)
            except ValueError:
                continue
        return None

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
        return self.build_source_url(self._encode_stender_id(href), base_url="https://tenders.etimad.sa")

    def _encode_stender_id(self, href: str) -> str:
        marker = "STenderId="
        if marker not in href:
            return href

        before, value = href.split(marker, 1)
        return before + marker + quote(value.strip(), safe="%*=@")
