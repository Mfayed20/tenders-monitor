import json
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
    assert tenders[0].ref_number == "TSA-001"
    assert tenders[0].publish_date is not None
    assert tenders[0].close_date is not None


def test_tendersinfo_parser_extracts_record_fields():
    scraper = TendersInfoScraper()
    tender = scraper._parse_record(_read_json("tendersinfo_record.json"))

    assert tender is not None
    assert tender.ref_number == "TI-001"
    assert tender.link == "https://www.tendersinfo.com/tender/electric-bus-maintenance"
    assert scraper._is_saudi("Saudi Arabia") is True


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
