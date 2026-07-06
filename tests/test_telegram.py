import asyncio
from dataclasses import dataclass

from utils import telegram_notifier
from utils.telegram_notifier import _build_messages, send_telegram_alert, telegram_credentials_configured


@dataclass
class TenderRow:
    site: str
    title: str
    ref_number: str
    publish_date: str
    close_date: str
    days_left: int | None
    link: str
    company_match: str
    matched_keywords: str
    description: str


def test_build_messages_escapes_html_and_includes_scope():
    tender = TenderRow(
        site="EVS & Co",
        title="EV <Repair> & Firmware Update",
        ref_number="REF_[1]",
        publish_date="2026-04-05",
        close_date="2026-04-10",
        days_left=5,
        link="https://example.com/tender?x=1&y=2",
        company_match="EVS",
        matched_keywords="firmware, battery module",
        description="Mixed English & Arabic scope <upgrade> for عربي fleet diagnostics.",
    )

    message = _build_messages([tender], "2026-04-05")[0]

    assert "<Repair>" not in message
    assert "&lt;Repair&gt;" in message
    assert "EVS &amp; Co" in message
    assert "Scope:" in message
    assert "عربي" in message
    assert 'href="https://example.com/tender?x=1&amp;y=2"' in message


def test_telegram_credentials_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert telegram_credentials_configured() is False

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    assert telegram_credentials_configured() is True


def test_send_telegram_alert_skips_empty_digest(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            calls.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(telegram_notifier.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(send_telegram_alert([], "2026-04-29", bot_token="token", chat_id="123"))

    assert result is False
    assert calls == []
