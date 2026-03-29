"""
HTML email notification for matched tenders.
Uses Gmail SMTP with App Password authentication.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TenderRow:
    site: str
    title: str
    ref_number: str
    publish_date: str
    close_date: str
    days_left: int | None  # days until closing
    link: str
    company_match: str
    matched_keywords: str  # comma-separated keywords that triggered the match
    description: str  # tender description/scope (truncated)


def _urgency_badge(days_left: int | None) -> str:
    """Return colored urgency badge HTML based on days remaining."""
    if days_left is None:
        return '<span style="color:#6b7280;">N/A</span>'
    if days_left <= 3:
        return f'<span style="background:#dc2626;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{days_left}d URGENT</span>'
    if days_left <= 7:
        return f'<span style="background:#f59e0b;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">{days_left}d</span>'
    return f'<span style="background:#10b981;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">{days_left}d</span>'


def _build_table(tenders: list[TenderRow], header_color: str) -> str:
    """Build a styled HTML table for a list of tenders."""
    rows_html = ""
    for i, t in enumerate(tenders, 1):
        row_bg = "#ffffff" if i % 2 == 1 else "#f9fafb"
        desc_html = ""
        if t.description:
            desc_short = t.description[:150] + ("..." if len(t.description) > 150 else "")
            desc_html = f'<br/><span style="font-size:12px;color:#6b7280;">{desc_short}</span>'

        rows_html += f"""
        <tr style="background:{row_bg};">
            <td style="padding:10px;border:1px solid #e5e7eb;text-align:center;font-weight:bold;">{i}</td>
            <td style="padding:10px;border:1px solid #e5e7eb;">
                <a href="{t.link}" style="color:#2563eb;font-weight:600;text-decoration:none;">{t.title}</a>
                {desc_html}
            </td>
            <td style="padding:10px;border:1px solid #e5e7eb;">{t.site}</td>
            <td style="padding:10px;border:1px solid #e5e7eb;font-family:monospace;">{t.ref_number}</td>
            <td style="padding:10px;border:1px solid #e5e7eb;">{t.publish_date}</td>
            <td style="padding:10px;border:1px solid #e5e7eb;">{t.close_date}</td>
            <td style="padding:10px;border:1px solid #e5e7eb;text-align:center;">{_urgency_badge(t.days_left)}</td>
            <td style="padding:10px;border:1px solid #e5e7eb;font-size:11px;color:#6b7280;">{t.matched_keywords}</td>
        </tr>"""

    return f"""
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
            <thead>
                <tr style="background:{header_color};color:#ffffff;">
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:center;">#</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:left;">Tender</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:left;">Source</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:left;">Ref #</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:left;">Published</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:left;">Deadline</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:center;">Days Left</th>
                    <th style="padding:10px;border:1px solid #e5e7eb;text-align:left;">Keywords</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>"""


def _sort_by_urgency(tenders: list[TenderRow]) -> list[TenderRow]:
    return sorted(
        tenders,
        key=lambda t: (t.days_left is None, t.days_left if t.days_left is not None else 999),
    )


def _build_html(tenders: list[TenderRow], date_str: str) -> str:
    """Build an HTML email body with separate Climatech and EVS sections."""
    climatech = _sort_by_urgency([t for t in tenders if t.company_match in ("Climatech", "Both")])
    evs = _sort_by_urgency([t for t in tenders if t.company_match in ("EVS", "Both")])

    sections_html = ""

    if climatech:
        sections_html += f"""
        <h3 style="color:#2563eb;margin-top:30px;border-left:4px solid #2563eb;padding-left:10px;">
            Climatech Charger &mdash; {len(climatech)} Tender{'s' if len(climatech) != 1 else ''}
        </h3>
        <p style="font-size:12px;color:#6b7280;">Chargers, Installation, Infrastructure, CPO. Sorted by urgency.</p>
        {_build_table(climatech, "#2563eb")}"""

    if evs:
        sections_html += f"""
        <h3 style="color:#059669;margin-top:40px;border-left:4px solid #059669;padding-left:10px;">
            EVS &mdash; {len(evs)} Tender{'s' if len(evs) != 1 else ''}
        </h3>
        <p style="font-size:12px;color:#6b7280;">Fleet Maintenance, Service, Repair. Sorted by urgency.</p>
        {_build_table(evs, "#059669")}"""

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#1f2937;max-width:1200px;">
        <h2 style="color:#1e3a5f;">KSA EV Tender Alert &mdash; {date_str}</h2>
        <p>Found <strong>{len(tenders)}</strong> new matching tender(s) &mdash;
           Climatech: <strong>{len(climatech)}</strong> &nbsp;|&nbsp;
           EVS: <strong>{len(evs)}</strong>
        </p>
        {sections_html}
        <p style="font-size:11px;color:#9ca3af;margin-top:30px;">
            Generated by KSA EV Tender Monitor &mdash; {date_str}
        </p>
    </body>
    </html>"""


def send_email(tenders: list[TenderRow], date_str: str) -> bool:
    """
    Send an HTML email with matched tenders via Gmail SMTP.
    Requires GMAIL_USER, GMAIL_APP_PASSWORD, and NOTIFY_EMAILS env vars.
    Returns True on success.
    """
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    recipients = os.getenv("NOTIFY_EMAILS", "")

    if not gmail_user or not gmail_pass:
        logger.warning("GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping email")
        return False

    if not recipients:
        logger.warning("NOTIFY_EMAILS not set — skipping email")
        return False

    recipient_list = [r.strip() for r in recipients.split(",") if r.strip()]
    if not recipient_list:
        logger.warning("No valid recipients — skipping email")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"KSA EV Tenders — {len(tenders)} new match(es) — {date_str}"
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipient_list)

    # Plain text fallback — split into Climatech and EVS sections
    climatech_plain = [t for t in tenders if t.company_match in ("Climatech", "Both")]
    evs_plain = [t for t in tenders if t.company_match in ("EVS", "Both")]
    plain = f"KSA EV Tender Alert — {date_str}\n"
    plain += f"Found {len(tenders)} new tender(s) | Climatech: {len(climatech_plain)} | EVS: {len(evs_plain)}\n"

    if climatech_plain:
        plain += f"\n--- CLIMATECH CHARGER ({len(climatech_plain)}) ---\n\n"
        for i, t in enumerate(climatech_plain, 1):
            days = f"{t.days_left}d left" if t.days_left is not None else "N/A"
            plain += f"{i}. {t.title}\n"
            plain += f"   Source: {t.site} | Ref: {t.ref_number} | Deadline: {t.close_date} ({days})\n"
            plain += f"   Keywords: {t.matched_keywords}\n"
            plain += f"   Link: {t.link}\n\n"

    if evs_plain:
        plain += f"\n--- EVS ({len(evs_plain)}) ---\n\n"
        for i, t in enumerate(evs_plain, 1):
            days = f"{t.days_left}d left" if t.days_left is not None else "N/A"
            plain += f"{i}. {t.title}\n"
            plain += f"   Source: {t.site} | Ref: {t.ref_number} | Deadline: {t.close_date} ({days})\n"
            plain += f"   Keywords: {t.matched_keywords}\n"
            plain += f"   Link: {t.link}\n\n"

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_build_html(tenders, date_str), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, recipient_list, msg.as_string())
        logger.info("Email sent to %s", ", ".join(recipient_list))
        return True
    except Exception:
        logger.exception("Failed to send email")
        return False
