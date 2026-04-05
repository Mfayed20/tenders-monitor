from dataclasses import dataclass

from utils.telegram_notifier import _build_messages


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
