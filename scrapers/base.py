"""
Abstract base scraper that all site-specific scrapers inherit from.
Provides common infrastructure: logging, retry logic, Playwright browser management.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


@dataclass
class Tender:
    """Represents a single tender found on a site."""
    site: str
    title: str
    ref_number: str
    publish_date: datetime | None = None
    close_date: datetime | None = None
    publish_date_raw: str = ""
    close_date_raw: str = ""
    link: str = ""
    description: str = ""
    raw_data: dict = field(default_factory=dict)


class BaseScraper(ABC):
    """Abstract base for all site scrapers."""

    SITE_NAME: str = ""
    BASE_URL: str = ""
    NEEDS_BROWSER: bool = False  # Set True for JS-heavy sites

    def __init__(self):
        self.logger = logging.getLogger(f"scraper.{self.SITE_NAME}")

    @abstractmethod
    async def scrape(self, browser=None) -> list[Tender]:
        """
        Scrape the site and return a list of raw Tender objects.
        If NEEDS_BROWSER is True, a Playwright browser instance is passed in.
        """
        ...

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((TimeoutError, ConnectionError, OSError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_with_retry(self, client, url: str, **kwargs) -> str:
        """Fetch a URL with httpx, with automatic retry on transient errors."""
        response = await client.get(url, **kwargs)
        response.raise_for_status()
        return response.text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((TimeoutError, Exception)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_with_browser(self, page, url: str, wait_selector: str = None) -> str:
        """Navigate a Playwright page to a URL with retry logic."""
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if wait_selector:
            await page.wait_for_selector(wait_selector, state="attached", timeout=15000)
        # Give JS a moment to finish rendering
        await page.wait_for_timeout(2000)
        return await page.content()
