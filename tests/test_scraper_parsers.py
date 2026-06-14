import json
import asyncio
from pathlib import Path

import httpx
from tenacity import wait_none

from scrapers.base import Tender
from scrapers.etimad import EtimadScraper
from scrapers.ksagate import KSAGateScraper
from scrapers.metenders import METendersScraper
from scrapers.tendersa import TendersaScraper
from scrapers.tendersinfo import TendersInfoScraper
from scrapers.tendersontime import TendersOnTimeScraper
from utils.dates import KSA_TZ

FIXTURES = Path(__file__).parent / "fixtures"


def _read_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _read_json(name: str) -> dict:
    return json.loads(_read_text(name))


def test_etimad_parser_extracts_unique_tender_and_dates():
    tenders = EtimadScraper()._parse_page(_read_text("etimad.html"))

    assert len(tenders) == 1
    assert tenders[0].site == "Etimad"
    assert tenders[0].ref_number == "ETM123456789"
    assert "EV charging stations" in tenders[0].title
    assert tenders[0].publish_date is not None
    assert tenders[0].close_date is not None


def test_etimad_listing_load_tolerates_non_idle_network():
    class FakePage:
        def __init__(self):
            self.goto_wait_until = None

        async def goto(self, url, wait_until, timeout):
            self.goto_wait_until = wait_until

        async def wait_for_selector(self, selector, state, timeout):
            return None

        async def wait_for_load_state(self, state, timeout):
            raise TimeoutError("network stayed busy")

        async def wait_for_timeout(self, timeout):
            return None

        async def content(self):
            return _read_text("etimad.html")

    scraper = EtimadScraper()
    page = FakePage()
    html = asyncio.run(scraper._load_listing_page(page, "https://example.com"))

    assert page.goto_wait_until == "domcontentloaded"
    assert "DetailsForVisitor" in html
    assert scraper.run_errors == []


def test_etimad_detail_loader_clicks_schedule_tab_by_id():
    class FakeLocator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector

        async def click(self, timeout):
            self.page.clicked_selectors.append((self.selector, timeout))

    class FakePage:
        def __init__(self):
            self.clicked_selectors = []
            self.waited_selectors = []
            self.content_calls = 0

        async def goto(self, url, wait_until, timeout):
            self.goto_wait_until = wait_until

        async def wait_for_selector(self, selector, state, timeout):
            self.waited_selectors.append((selector, state, timeout))

        async def wait_for_timeout(self, timeout):
            return None

        async def content(self):
            self.content_calls += 1
            return f"<html>detail {self.content_calls}</html>"

        def locator(self, selector):
            return FakeLocator(self, selector)

    page = FakePage()
    html = asyncio.run(EtimadScraper()._load_detail_page(page, "https://example.com/tender"))

    assert page.clicked_selectors == [("#tenderDatesTab", 5000)]
    assert ("#d-2.active, #d-2.show", "attached", 5000) in page.waited_selectors
    assert html == "<html>detail 1</html>\n<html>detail 2</html>"


def test_etimad_detail_parser_extracts_reference_description_and_deadline():
    base_tender = Tender(
        site="Etimad",
        title="encrypted listing title",
        ref_number="*@@**jOil8b8AtY",
        link="https://tenders.etimad.sa/Tender/DetailsForVisitor?STenderId=*@@**jOil8b8AtY%20Iy58gwqV6A==",
    )

    tender = EtimadScraper()._parse_detail_page(_read_text("etimad_detail.html"), base_tender)

    assert tender.title == "توريد وتركيب شاحن سياره كهربائي"
    assert tender.ref_number == "260539007202"
    assert tender.description == "توريد وتركيب شاحن سياره كهربائ"
    assert tender.close_date_raw == "14/06/2026 10:00 AM"
    assert tender.close_date is not None
    assert tender.close_date.year == 2026
    assert tender.close_date.month == 6
    assert tender.close_date.day == 14
    assert tender.close_date.hour == 10
    assert tender.close_date.tzinfo == KSA_TZ
    assert tender.raw_data["etimad_competition_number"] == "ORN0000019467"
    assert tender.raw_data["etimad_status"] == "معتمدة"


def test_etimad_detail_parser_treats_ambiguous_deadlines_as_dayfirst():
    base_tender = Tender(site="Etimad", title="توريد وتركيب شاحن كهربائي", ref_number="1")
    html = _read_text("etimad_detail.html").replace(
        "14/06/2026 28/12/1447 10:00 AM",
        "05/07/2026 20/01/1448 02:30 PM",
    )

    tender = EtimadScraper()._parse_detail_page(html, base_tender)

    assert tender.close_date_raw == "05/07/2026 02:30 PM"
    assert tender.close_date is not None
    assert tender.close_date.year == 2026
    assert tender.close_date.month == 7
    assert tender.close_date.day == 5
    assert tender.close_date.hour == 14
    assert tender.close_date.tzinfo == KSA_TZ


def test_etimad_detail_parser_combines_split_deadline_nodes():
    base_tender = Tender(site="Etimad", title="توريد وتركيب شاحن كهربائي", ref_number="1")
    html = _read_text("etimad_detail.html").replace(
        "<div>14/06/2026 28/12/1447 10:00 AM</div>",
        "<div>14/06/2026</div><div>28/12/1447</div><div>10:00 AM</div>",
    )

    tender = EtimadScraper()._parse_detail_page(html, base_tender)

    assert tender.close_date_raw == "14/06/2026 10:00 AM"
    assert tender.close_date is not None
    assert tender.close_date.hour == 10


def test_etimad_detail_parser_reads_concatenated_basic_and_schedule_html():
    base_tender = Tender(site="Etimad", title="", ref_number="1")
    basic_html, schedule_html = _read_text("etimad_detail.html").split(
        "<div>آخر موعد لتقديم العروض</div>"
    )
    html = basic_html + "</body></html>\n<html><body><div>آخر موعد لتقديم العروض</div>" + schedule_html

    tender = EtimadScraper()._parse_detail_page(html, base_tender)

    assert tender.title == "توريد وتركيب شاحن سياره كهربائي"
    assert tender.close_date_raw == "14/06/2026 10:00 AM"
    assert tender.close_date is not None


def test_etimad_detail_urls_are_encoded_for_browser_and_alert_links():
    url = EtimadScraper()._full_url(
        "/Tender/DetailsForVisitor?STenderId=*@@**jOil8b8AtY Iy58gwqV6A=="
    )

    assert " " not in url
    assert "STenderId=*@@**jOil8b8AtY%20Iy58gwqV6A==" in url


def test_etimad_listing_scan_reaches_beyond_first_three_pages():
    assert EtimadScraper.MAX_PAGES >= 10


def test_etimad_detail_enrichment_is_limited_to_ev_relevant_rows():
    scraper = EtimadScraper()

    assert scraper._should_enrich_detail(
        Tender(site="Etimad", title="توريد وتركيب شاحن سياره كهربائي", ref_number="1")
    )
    assert not scraper._should_enrich_detail(
        Tender(site="Etimad", title="إعادة تصميم وتأثيث المكاتب", ref_number="2")
    )


def test_ksagate_parser_extracts_api_item_fields():
    tender = KSAGateScraper()._parse_api_item(_read_json("ksagate_item.json"))

    assert tender is not None
    assert tender.ref_number == "KSA-001"
    assert tender.close_date is not None
    assert "Public EV charger deployment" in tender.description


def test_ksagate_fetch_page_retries_ssl_verify_failure_without_recording_error(monkeypatch):
    class FakeResponse:
        def json(self):
            return [{"id": 1}]

    class FallbackClient:
        pass

    class FakeClientContext:
        async def __aenter__(self):
            return FallbackClient()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    scraper = KSAGateScraper()
    primary_client = object()
    fallback_verify_values = []

    def fake_build_client(*, verify=True):
        fallback_verify_values.append(verify)
        return FakeClientContext()

    async def fake_fetch(client, method, url):
        if client is primary_client:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate")
        return FakeResponse()

    monkeypatch.setattr(scraper, "_build_client", fake_build_client)
    monkeypatch.setattr(scraper, "fetch_response_with_retry", fake_fetch)

    data = asyncio.run(scraper._fetch_page(primary_client, "https://ksatendersgate.com/wp-json/wp/v2/tenders", 1))

    assert data == [{"id": 1}]
    assert fallback_verify_values == [False]
    assert scraper.run_errors == []


def test_tendersa_parser_extracts_wrapper_fields():
    tenders = TendersaScraper()._parse_page(_read_text("tendersa.html"))

    assert len(tenders) == 1
    assert tenders[0].site == "TenderSA"
    assert tenders[0].ref_number == "1228569"
    assert tenders[0].link == "https://www.tendersa.com/TenderDetails.aspx?tdc_id=1228569"
    assert tenders[0].publish_date is not None
    assert tenders[0].close_date is not None
    assert "Riyadh" in tenders[0].description


def test_tendersa_parser_extracts_current_tg2_cards():
    tenders = TendersaScraper()._parse_page(_read_text("tendersa_tg2.html"))

    assert len(tenders) == 1
    assert tenders[0].site == "TenderSA"
    assert tenders[0].title == "Operation and maintenance of an EV charging depot"
    assert tenders[0].ref_number == "1262313"
    assert tenders[0].link == "https://www.tendersa.com/TenderDetails.aspx?tdc_id=1262313"
    assert tenders[0].publish_date is not None
    assert tenders[0].publish_date_raw == "June 14, 2026"
    assert tenders[0].close_date is not None
    assert tenders[0].close_date_raw == "July 21, 2026"
    assert "Maintenance and Operation" in tenders[0].description


def test_tendersa_wait_selector_matches_current_cards():
    assert "article.tg2-card" in TendersaScraper.WAIT_SELECTOR


def test_tendersinfo_parser_extracts_record_fields():
    scraper = TendersInfoScraper()
    tender = scraper._parse_record(_read_json("tendersinfo_record.json"))

    assert tender is not None
    assert tender.ref_number == "TI-001"
    assert tender.link == "https://www.tendersinfo.com/tenders_details/electric-bus-maintenance-001"
    assert "Saudi Transport Authority" in tender.description
    assert scraper._is_saudi("Saudi Arabia") is True


def test_tendersinfo_payload_uses_saudi_listing_filters():
    scraper = TendersInfoScraper()
    payload = scraper._build_payload(draw=2, start=50)

    assert payload["draw"] == "2"
    assert payload["start"] == "50"
    assert payload["countrytxt"] == "0300682"
    assert payload["notice_type"] == "1, 3, 8"


def test_metenders_parser_extracts_table_body_fields():
    tenders = METendersScraper()._parse_page(_read_text("metenders.html"))

    assert len(tenders) == 1
    assert tenders[0].ref_number == "ME-001"
    assert tenders[0].link == "https://metenders.com/RequestInfo.asp?TID=ME001"
    assert "[New]" in tenders[0].description


def test_tendersontime_parser_extracts_record_fields():
    tender = TendersOnTimeScraper()._parse_record(_read_json("tendersontime_record.json"))

    assert tender is not None
    assert tender.ref_number == "TOT-001"
    assert tender.close_date is not None
    assert tender.link == "https://www.tendersontime.com/tender/ev-battery-diagnostics"


def test_tendersontime_listing_parser_extracts_visible_rows():
    tenders = TendersOnTimeScraper()._parse_listing_page(_read_text("tendersontime_listing.html"))

    assert len(tenders) == 1
    assert tenders[0].title == "EV Battery Diagnostics Services"
    assert tenders[0].ref_number == "140369262"
    assert tenders[0].close_date is not None
    assert tenders[0].link == "https://www.tendersontime.com/tenders-details/ev-battery-diagnostics-85ddd6e/"


def test_tendersontime_fetch_retries_transient_timeout():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tenderDetails": [{"ID": "TOT-RETRY"}]}

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def post(self, url, content):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectTimeout("temporary timeout")
            return FakeResponse()

    scraper = TendersOnTimeScraper()
    scraper.RETRY_WAIT = wait_none()
    client = FakeClient()

    data = asyncio.run(scraper._fetch_page(client, 1))

    assert client.calls == 2
    assert data["tenderDetails"][0]["ID"] == "TOT-RETRY"
