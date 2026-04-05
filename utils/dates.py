"""
Date parsing for Arabic and English date formats found on KSA tender sites.
Handles Hijri references, Arabic numerals, and various locale formats.
"""

import re
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateutil_parser

# ---------------------------------------------------------------------------
# Arabic month names → English
# ---------------------------------------------------------------------------

ARABIC_MONTHS = {
    "يناير": "January", "فبراير": "February", "مارس": "March",
    "أبريل": "April", "إبريل": "April", "مايو": "May",
    "يونيو": "June", "يونية": "June", "يوليو": "July",
    "يوليه": "July", "أغسطس": "August", "اغسطس": "August",
    "سبتمبر": "September", "أكتوبر": "October", "اكتوبر": "October",
    "نوفمبر": "November", "ديسمبر": "December",
}

# Arabic-Indic numerals → Western
ARABIC_NUMERALS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# KSA timezone (AST = UTC+3)
KSA_TZ = timezone(timedelta(hours=3))

_AMBIGUOUS_NUMERIC_DATE_RE = re.compile(
    r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$"
)


def _transliterate_arabic(text: str) -> str:
    """Convert Arabic numerals and month names to English equivalents."""
    text = text.translate(ARABIC_NUMERALS)
    for ar, en in ARABIC_MONTHS.items():
        text = text.replace(ar, en)
    return text


def has_date_text(raw_date: str | None) -> bool:
    """Return True when the source exposed a non-empty date string."""
    return bool(raw_date and raw_date.strip())


def _is_ambiguous_numeric_date(text: str) -> bool:
    """Reject purely numeric dates when day/month ordering is ambiguous."""
    match = _AMBIGUOUS_NUMERIC_DATE_RE.match(text)
    if not match:
        return False

    first = int(match.group(1))
    second = int(match.group(2))
    return first <= 12 and second <= 12


def parse_date(date_str: str) -> datetime | None:
    """
    Parse a date string that may contain Arabic months/numerals.
    Returns a timezone-aware datetime in KSA time, or None on failure.
    """
    if not date_str or not date_str.strip():
        return None

    cleaned = _transliterate_arabic(date_str.strip())
    # Remove common prefixes/suffixes
    cleaned = re.sub(r"(هـ|ه|م|AM|PM|ص|م\.)", "", cleaned, flags=re.IGNORECASE).strip()
    # Remove day names
    cleaned = re.sub(
        r"(الأحد|الاثنين|الثلاثاء|الأربعاء|الخميس|الجمعة|السبت)",
        "", cleaned,
    ).strip()
    # Collapse separators
    cleaned = re.sub(r"\s+", " ", cleaned)

    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?$", cleaned):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.replace(tzinfo=KSA_TZ)
            except ValueError:
                continue

    if _is_ambiguous_numeric_date(cleaned):
        return None

    try:
        dt = dateutil_parser.parse(cleaned, dayfirst=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KSA_TZ)
        return dt
    except (ValueError, OverflowError):
        pass

    # Fallback: try common patterns manually
    for fmt in [
        "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y",
        "%d %B %Y", "%B %d, %Y", "%d %b %Y",
        "%Y/%m/%d", "%d/%m/%Y %H:%M",
    ]:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=KSA_TZ)
        except ValueError:
            continue

    return None


def is_new_tender(publish_date: datetime | None, hours: int = 24) -> bool:
    """Check if a tender was published within the last N hours."""
    if publish_date is None:
        return True  # Include if we can't determine date (conservative)
    now = datetime.now(KSA_TZ)
    age = now - publish_date
    return timedelta(0) <= age <= timedelta(hours=hours)


def is_closing_soon(close_date: datetime | None, days: int = 30) -> bool:
    """Check if a tender closes within the next N days."""
    if close_date is None:
        return True  # Include if we can't determine date (conservative)
    now = datetime.now(KSA_TZ)
    return timedelta(0) <= (close_date - now) <= timedelta(days=days)


def format_date(dt: datetime | None) -> str:
    """Format a datetime for CSV/display output."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d")
