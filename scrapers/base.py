"""
Abstract base scraper that all site-specific scrapers inherit from.
Provides common infrastructure: logging, retry logic, Playwright browser management.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    retry_if_exception_type,
    before_sleep_log,
)

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - Playwright is a runtime dependency.
    PlaywrightTimeoutError = TimeoutError

logger = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


def _is_retryable_httpx_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_HTTP_STATUSES
    return False


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
    ALLOWED_HOSTS: tuple[str, ...] = ()

    def __init__(self):
        self.logger = logging.getLogger(f"scraper.{self.SITE_NAME}")
        self.run_errors: list[str] = []

    def reset_run_errors(self) -> None:
        """Clear errors recorded during a previous scrape attempt."""
        self.run_errors = []

    def record_run_error(self, message: str, exc: Exception | None = None) -> None:
        """Record a handled scrape error so the run summary can report it."""
        if exc is None:
            self.run_errors.append(message)
            return

        self.run_errors.append(f"{message}: {type(exc).__name__}: {exc}")

    @abstractmethod
    async def scrape(self, browser=None) -> list[Tender]:
        """
        Scrape the site and return a list of raw Tender objects.
        If NEEDS_BROWSER is True, a Playwright browser instance is passed in.
        """
        ...

    def build_source_url(
        self,
        href: str,
        *,
        base_url: str | None = None,
        allowed_hosts: tuple[str, ...] | None = None,
    ) -> str:
        """Resolve and validate a URL from an untrusted source page/API."""
        if not href:
            return ""

        resolved = urljoin(base_url or self.BASE_URL, href.strip())
        parsed = urlparse(resolved)
        base_host = urlparse(base_url or self.BASE_URL).netloc.lower()
        hosts = tuple(host.lower() for host in (allowed_hosts or self.ALLOWED_HOSTS or (base_host,)))

        if parsed.scheme != "https" or parsed.netloc.lower() not in hosts:
            self.logger.warning("Rejected unexpected %s link host/scheme: %s", self.SITE_NAME, resolved)
            return ""

        return resolved

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception(_is_retryable_httpx_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_response_with_retry(self, client, method: str, url: str, **kwargs) -> httpx.Response:
        """Fetch an httpx response with retries for transient network/server errors."""
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    async def fetch_with_retry(self, client, url: str, **kwargs) -> str:
        """Fetch a URL with httpx, with automatic retry on transient errors."""
        response = await self.fetch_response_with_retry(client, "GET", url, **kwargs)
        return response.text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((TimeoutError, PlaywrightTimeoutError)),
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
