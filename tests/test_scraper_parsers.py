import json
import asyncio
from pathlib import Path

from scrapers.etimad import EtimadScraper
from scrapers.ksagate import KSAGateScraper
from scrapers.metenders import METendersScraper
from scrapers.tendersa import TendersaScraper
from scrapers.tendersinfo import TendersInfoScraper
from scrapers.tendersontime import TendersOnTimeScraper

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


def test_ksagate_parser_extracts_api_item_fields():
    tender = KSAGateScraper()._parse_api_item(_read_json("ksagate_item.json"))

    assert tender is not None
    assert tender.ref_number == "KSA-001"
    assert tender.close_date is not None
    assert "Public EV charger deployment" in tender.description


def test_tendersa_parser_extracts_wrapper_fields():
    tenders = TendersaScraper()._parse_page(_read_text("tendersa.html"))

    assert len(tenders) == 1
    assert tenders[0].site == "TenderSA"
    assert tenders[0].ref_number == "1228569"
    assert tenders[0].link == "https://www.tendersa.com/TenderDetails.aspx?tdc_id=1228569"
    assert tenders[0].publish_date is not None
    assert tenders[0].close_date is not None
    assert "Riyadh" in tenders[0].description


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
