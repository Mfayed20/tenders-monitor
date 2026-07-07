"""
Scraper for metenders.com — Middle East tender aggregator.
The /SaudiArabia/ landing page is just categories, no actual tenders.

Actual tenders are on sub-pages. We scrape:
1. Newly added tenders page
2. Key category pages (construction, IT, etc.)

Tenders are in <tbody> blocks with:
- Title/link: a[href*="RequestInfo.asp"]
- Ref number: font[color="Red"]
- Description: after <b>Description:</b>
- No closing dates on public pages
"""

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Tender

logger = logging.getLogger(__name__)


class METendersScraper(BaseScraper):
    SITE_NAME = "METenders"
    BASE_URL = "https://metenders.com"
    ALLOWED_HOSTS = ("metenders.com", "www.metenders.com")
    NEEDS_BROWSER = True

    # Pages with actual tender listings
    SCRAPE_URLS = [
        "https://metenders.com/newely_added_tenders.asp",
        "https://metenders.com/SaudiArabia/SaudiArabia-Riyadh-Jeddah-Construction-Buildings-Tenders-and-Projects.asp",
    ]
    SAUDI_TEXT_MARKERS = (
        "saudi",
        "saudi arabia",
        "kingdom of saudi arabia",
        "ksa",
        "riyadh",
        "jeddah",
        "dammam",
        "makkah",
        "mecca",
        "madinah",
        "medina",
        "khobar",
        "al khobar",
        "dhahran",
        "jubail",
        "yanbu",
        "tabuk",
        "qassim",
        "abha",
        "jazan",
        "jizan",
        "najran",
        "hail",
        "ha'il",
        "taif",
    )

    async def scrape(self, browser=None) -> list[Tender]:
        if browser is None:
            self.logger.error("METenders requires a Playwright browser instance")
            return []

        tenders = []
        seen_refs = set()
        context = await browser.new_context(
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()

        try:
            for url in self.SCRAPE_URLS:
                self.logger.info("Fetching METenders: %s", url)

                try:
                    html = await self._load_listing_page(page, url)
                except Exception as exc:
                    self.logger.exception("Failed to fetch METenders: %s", url)
                    self.record_run_error(f"Failed to fetch METenders URL {url}", exc)
                    continue

                page_tenders = self._parse_page(html, source_url=url)
                # Deduplicate across pages
                for t in page_tenders:
                    key = t.ref_number or t.title
                    if key not in seen_refs:
                        seen_refs.add(key)
                        tenders.append(t)

                self.logger.info(
                    "Found %d tenders on %s",
                    len(page_tenders), url.split("/")[-1],
                )
        finally:
            await context.close()

        self.logger.info("METenders total: %d tenders scraped", len(tenders))
        return tenders

    async def _load_listing_page(self, page, url: str) -> str:
        """Load a METenders listing page through a browser to avoid direct HTTP blocking."""
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector('a[href*="RequestInfo.asp"]', state="attached", timeout=12000)
        except Exception as exc:
            self.logger.warning("METenders tender links were not attached within timeout: %s", exc)
        await page.wait_for_timeout(1000)
        return await page.content()

    def _parse_page(self, html: str, source_url: str | None = None) -> list[Tender]:
        """Parse tenders from METenders HTML using tbody blocks."""
        soup = BeautifulSoup(html, "lxml")
        tenders = []
        source_is_saudi = self._is_saudi_listing_url(source_url)

        # Find the main data table
        table = soup.select_one("table.hover-eff") or soup.select_one("table.top_table21")
        if not table:
            # Fallback: search entire page for tender links
            return self._parse_fallback(soup, source_url=source_url)

        # Each tender is in its own <tbody>
        tbodies = table.find_all("tbody")
        for tbody in tbodies:
            if source_url and not source_is_saudi and not self._has_saudi_marker(tbody.get_text(" ", strip=True)):
                continue

            tender = self._parse_tbody(tbody)
            if tender:
                tenders.append(tender)

        # If no tbody-based tenders found, try fallback
        if not tenders:
            tenders = self._parse_fallback(soup, source_url=source_url)

        return tenders

    def _parse_tbody(self, tbody) -> Tender | None:
        """Extract a tender from a single <tbody> block."""
        # Title and link
        link_el = tbody.select_one('a[href*="RequestInfo.asp"]')
        if not link_el:
            return None

        title = link_el.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        href = link_el.get("href", "")
        link = self._full_url(href)

        # Reference number
        ref_el = tbody.select_one('font[color="Red"], font[color="red"]')
        ref_number = ref_el.get_text(strip=True) if ref_el else ""

        # Description
        description = ""
        desc_b = tbody.find("b", string=re.compile(r"Description", re.I))
        if desc_b and desc_b.parent:
            desc_text = desc_b.parent.get_text(strip=True)
            description = re.sub(r"^Description\s*:\s*", "", desc_text, flags=re.I)[:500]

        # Status
        status_b = tbody.find("b", string=re.compile(r"Status", re.I))
        if status_b and status_b.parent:
            status_text = status_b.parent.get_text(strip=True)
            status = re.sub(r"^.*Status\s*:\s*", "", status_text, flags=re.I).strip()
            # Include status in description for context
            if status:
                description = f"[{status}] {description}"

        return Tender(
            site=self.SITE_NAME,
            title=title,
            ref_number=ref_number,
            link=link,
            description=description,
        )

    def _parse_fallback(self, soup, source_url: str | None = None) -> list[Tender]:
        """Fallback: just find all RequestInfo links on the page."""
        tenders = []
        links = soup.select('a[href*="RequestInfo.asp"]')
        seen = set()
        source_is_saudi = self._is_saudi_listing_url(source_url)

        for link_el in links:
            title = link_el.get_text(strip=True)
            href = link_el.get("href", "")
            if not title or len(title) < 10 or title in seen:
                continue
            if source_url and not source_is_saudi:
                container = (
                    link_el.find_parent("tbody")
                    or link_el.find_parent("tr")
                    or link_el.find_parent("li")
                    or link_el.find_parent("td")
                )
                if not container:
                    continue
                if not self._has_saudi_marker(container.get_text(" ", strip=True)):
                    continue
            seen.add(title)

            tenders.append(Tender(
                site=self.SITE_NAME,
                title=title,
                ref_number="",
                link=self._full_url(href),
            ))

        return tenders

    def _full_url(self, href: str) -> str:
        return self.build_source_url(href.lstrip("./"), base_url=self.BASE_URL)

    def _is_saudi_listing_url(self, source_url: str | None) -> bool:
        if not source_url:
            return False
        return "/saudiarabia/" in source_url.lower()

    def _has_saudi_marker(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.lower())
        return any(re.search(rf"\b{re.escape(marker)}\b", normalized) for marker in self.SAUDI_TEXT_MARKERS)
