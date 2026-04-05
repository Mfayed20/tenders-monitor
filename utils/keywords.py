"""
Keyword matching for EV tender classification.
Maps tenders to Climatech Charger (chargers/installation) or EVS (fleet SMR).

Three matching layers:
1. Simple keywords   — direct substring match
2. Compound keywords — keyword matches only if an EV-context word also appears
3. Negative keywords — rejects false positives even after a positive match

Keywords are loaded from config/keywords.yaml if it exists, falling back to
the hardcoded defaults below.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded defaults (used as fallback if keywords.yaml is missing)
# ---------------------------------------------------------------------------

_DEFAULT_NEGATIVE = [
    "drone", "railway", "railroad", "rail line", "metro line",
    "tramway", "forklift", "generator set", "elevator", "escalator",
    "phone charger", "laptop charger", "ups battery",
]

_DEFAULT_CLIMATECH_EN = [
    "ev charging", "ev charger", "electric vehicle charging",
    "charger installation", "charging station", "charging infrastructure",
    "dc fast charger", "ac charger", "level 2 charger",
    "charging point", "charging equipment",
    "charging network", "smart charging",
    "supercharger",
    "cpo", "charge point operator",
    "cpms", "charge point management",
    "ocpp", "ocpi",
    "sec certified", "sec certification",
    "charger maintenance",
    "ev infrastructure",
    "e-mobility", "emobility",
]

_DEFAULT_CLIMATECH_AR = [
    "شحن السيارات الكهربائية", "شاحن سيارات كهربائية", "شاحن كهربائي",
    "محطة شحن", "تركيب شاحن", "بنية تحتية للشحن",
    "المشغل المسؤول عن الشحن", "نقطة شحن", "شاحن سريع",
    "شبكة شحن", "الشحن الذكي",
    "إدارة نقاط الشحن", "تشغيل نقاط الشحن",
    "صيانة الشواحن", "تجهيز موقع الشحن",
]

_DEFAULT_CLIMATECH_COMPOUND_EN = [
    ("commissioning", ["charger", "ev", "charging", "شحن", "شاحن"]),
    ("electrical works", ["charger", "ev", "charging", "شحن"]),
    ("site preparation", ["charger", "ev", "charging", "شحن"]),
    ("battery charger", ["ev", "electric vehicle", "charging station", "محطة شحن"]),
    ("battery charging", ["ev", "electric vehicle", "charging station", "محطة شحن"]),
    ("load balancing", ["charger", "ev", "charging", "شحن", "شاحن"]),
    ("load management", ["charger", "ev", "charging", "شحن", "شاحن"]),
]

_DEFAULT_CLIMATECH_COMPOUND_AR = [
    ("أعمال كهربائية", ["شحن", "شاحن", "ev", "charger"]),
    ("تجهيز الموقع", ["شحن", "شاحن", "ev", "charger"]),
    ("هيئة تنظيم الكهرباء", ["شحن", "شاحن", "charger", "ev", "charging"]),
]

_DEFAULT_EVS_EN = [
    "ev maintenance", "electric vehicle maintenance",
    "ev fleet",
    "ev service", "ev servicing",
    "ev repair",
    "ev diagnostics",
    "electric bus", "electric truck",
]

_DEFAULT_EVS_AR = [
    "صيانة السيارات الكهربائية", "صيانة مركبات كهربائية",
    "أسطول كهربائي",
    "حافلة كهربائية", "شاحنة كهربائية",
    "خدمة المركبات الكهربائية", "إصلاح المركبات الكهربائية",
    "ورشة مركبات كهربائية", "قطع غيار المركبات الكهربائية",
]

_DEFAULT_EVS_COMPOUND_EN = [
    ("body shop", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("bodywork", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("spare parts", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("workshop", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("service center", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("vehicle inspection", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("inverter", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("vehicle repair", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("fleet maintenance", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("fleet management", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("collision repair", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("battery repair", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("battery diagnostics", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("powertrain repair", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("periodic servicing", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("preventive maintenance", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
]

_DEFAULT_EVS_COMPOUND_AR = [
    ("ورشة", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("قطع غيار", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("فحص مركبات", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("صيانة الأسطول", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("إدارة الأسطول", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("إصلاح البطارية", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("تشخيص البطارية", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("صيانة وقائية", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
    ("هيكل ودهان", ["vehicle", "vehicles", "fleet", "bus", "buses", "truck", "trucks", "مركبة", "مركبات", "سيارة", "سيارات", "أسطول", "حافلة", "حافلات", "شاحنة", "شاحنات"]),
]

_DEFAULT_EVS_EV_ANCHORS_EN = [
    "ev",
    "electric",
    "electric vehicle",
    "electric vehicles",
    "electric fleet",
    "electric bus",
    "electric buses",
    "electric truck",
    "electric trucks",
]

_DEFAULT_EVS_EV_ANCHORS_AR = [
    "كهربائي",
    "كهربائية",
    "مركبة كهربائية",
    "مركبات كهربائية",
    "المركبات الكهربائية",
    "سيارة كهربائية",
    "سيارات كهربائية",
    "السيارات الكهربائية",
    "أسطول كهربائي",
    "حافلة كهربائية",
    "حافلات كهربائية",
    "شاحنة كهربائية",
    "شاحنات كهربائية",
]

_DEFAULT_EVS_VEHICLE_ANCHORS_EN = [
    "vehicle",
    "vehicles",
    "fleet",
    "bus",
    "buses",
    "truck",
    "trucks",
]

_DEFAULT_EVS_VEHICLE_ANCHORS_AR = [
    "مركبة",
    "مركبات",
    "سيارة",
    "سيارات",
    "أسطول",
    "حافلة",
    "حافلات",
    "شاحنة",
    "شاحنات",
]

_DEFAULT_EVS_SUPPLY_NEGATIVE = [
    "electrical materials",
    "circuit breaker",
    "circuit breakers",
    "breaker panel",
    "مواد كهربائية",
    "قواطع",
    "لوحات كهربائية",
]


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def _load_yaml_keywords(yaml_path: Path) -> dict | None:
    """Load keyword config from YAML. Returns None if unavailable."""
    try:
        import yaml  # type: ignore
    except ImportError:
        logger.debug("PyYAML not installed — using hardcoded keywords")
        return None

    if not yaml_path.exists():
        logger.debug("keywords.yaml not found at %s — using hardcoded keywords", yaml_path)
        return None

    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        logger.info("Keywords loaded from %s", yaml_path)
        return data
    except Exception as exc:
        logger.warning("Failed to parse keywords.yaml (%s) — using hardcoded keywords", exc)
        return None


def _parse_compounds(entries: list) -> list[tuple]:
    """Convert YAML compound entries to (keyword, context_list) tuples."""
    result = []
    for entry in (entries or []):
        if isinstance(entry, dict):
            kw = entry.get("keyword", "").strip()
            # Strip inline YAML comments (e.g. "ورشة              # workshop")
            kw = kw.split("#")[0].strip()
            ctx = [str(c) for c in entry.get("context", [])]
            if kw:
                result.append((kw, ctx))
    return result


def _strip_comments(values: list) -> list[str]:
    """Strip inline YAML comments from string list items."""
    out = []
    for v in (values or []):
        s = str(v).split("#")[0].strip()
        if s:
            out.append(s)
    return out


_YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "keywords.yaml"
_yaml_data = _load_yaml_keywords(_YAML_PATH)

if _yaml_data:
    NEGATIVE_KEYWORDS      = _strip_comments(_yaml_data.get("negative", []))
    CLIMATECH_KEYWORDS_EN  = _strip_comments(_yaml_data.get("climatech", {}).get("simple_en", []))
    CLIMATECH_KEYWORDS_AR  = _strip_comments(_yaml_data.get("climatech", {}).get("simple_ar", []))
    CLIMATECH_COMPOUND_EN  = _parse_compounds(_yaml_data.get("climatech", {}).get("compound_en", []))
    CLIMATECH_COMPOUND_AR  = _parse_compounds(_yaml_data.get("climatech", {}).get("compound_ar", []))
    _evs_data             = _yaml_data.get("evs", {})
    EVS_KEYWORDS_EN       = _strip_comments(_evs_data.get("simple_en", []))
    EVS_KEYWORDS_AR       = _strip_comments(_evs_data.get("simple_ar", []))
    EVS_COMPOUND_EN       = _parse_compounds(_evs_data.get("compound_en", []))
    EVS_COMPOUND_AR       = _parse_compounds(_evs_data.get("compound_ar", []))
    EVS_EV_ANCHORS_EN     = _strip_comments(_evs_data.get("ev_anchors_en", [])) or _DEFAULT_EVS_EV_ANCHORS_EN
    EVS_EV_ANCHORS_AR     = _strip_comments(_evs_data.get("ev_anchors_ar", [])) or _DEFAULT_EVS_EV_ANCHORS_AR
    EVS_VEHICLE_ANCHORS_EN = _strip_comments(_evs_data.get("vehicle_anchors_en", [])) or _DEFAULT_EVS_VEHICLE_ANCHORS_EN
    EVS_VEHICLE_ANCHORS_AR = _strip_comments(_evs_data.get("vehicle_anchors_ar", [])) or _DEFAULT_EVS_VEHICLE_ANCHORS_AR
    EVS_SUPPLY_NEGATIVE   = _strip_comments(_evs_data.get("supply_negative", [])) or _DEFAULT_EVS_SUPPLY_NEGATIVE
else:
    NEGATIVE_KEYWORDS      = _DEFAULT_NEGATIVE
    CLIMATECH_KEYWORDS_EN  = _DEFAULT_CLIMATECH_EN
    CLIMATECH_KEYWORDS_AR  = _DEFAULT_CLIMATECH_AR
    CLIMATECH_COMPOUND_EN  = _DEFAULT_CLIMATECH_COMPOUND_EN
    CLIMATECH_COMPOUND_AR  = _DEFAULT_CLIMATECH_COMPOUND_AR
    EVS_KEYWORDS_EN        = _DEFAULT_EVS_EN
    EVS_KEYWORDS_AR        = _DEFAULT_EVS_AR
    EVS_COMPOUND_EN        = _DEFAULT_EVS_COMPOUND_EN
    EVS_COMPOUND_AR        = _DEFAULT_EVS_COMPOUND_AR
    EVS_EV_ANCHORS_EN      = _DEFAULT_EVS_EV_ANCHORS_EN
    EVS_EV_ANCHORS_AR      = _DEFAULT_EVS_EV_ANCHORS_AR
    EVS_VEHICLE_ANCHORS_EN = _DEFAULT_EVS_VEHICLE_ANCHORS_EN
    EVS_VEHICLE_ANCHORS_AR = _DEFAULT_EVS_VEHICLE_ANCHORS_AR
    EVS_SUPPLY_NEGATIVE    = _DEFAULT_EVS_SUPPLY_NEGATIVE


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    matched: bool
    company: str  # "Climatech", "EVS", "Both", or ""
    matched_keywords: list[str]


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for matching."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _check_simple(combined: str, keywords: list[str]) -> list[str]:
    """Return list of simple keywords found in combined text."""
    hits = []
    for kw in keywords:
        if kw.lower() in combined or kw in combined:
            hits.append(kw)
    return hits


def _has_word(word: str, text: str) -> bool:
    """Check if word appears as a whole word (not substring) in text.

    Uses word-boundary regex for Latin words (e.g. 'ev' won't match 'development').
    Arabic words use substring matching since Arabic has no spaces between some forms.
    """
    # Arabic characters don't need word boundary — substring is fine
    if any("\u0600" <= ch <= "\u06FF" for ch in word):
        return word in text
    return bool(re.search(r"\b" + re.escape(word.lower()) + r"\b", text))


def _check_compound(combined: str, compounds: list[tuple]) -> list[str]:
    """Return list of compound keywords where both keyword and context match.

    Context words are checked with word-boundary matching to avoid
    'ev' matching 'development' or 'electric' matching 'electrical'.
    """
    hits = []
    for kw, contexts in compounds:
        if kw.lower() in combined or kw in combined:
            if any(_has_word(ctx, combined) for ctx in contexts):
                hits.append(kw)
    return hits


def _contains_any(text: str, keywords: list[str]) -> bool:
    """Return True when any keyword/phrase is present in text."""
    return any(_has_word(keyword, text) for keyword in keywords)


def _check_evs_compound(combined: str, compounds: list[tuple]) -> list[str]:
    """Return EVS compound hits only when EV and vehicle anchors both exist."""
    hits = []
    has_ev_anchor = _contains_any(combined, EVS_EV_ANCHORS_EN + EVS_EV_ANCHORS_AR)
    has_vehicle_anchor = _contains_any(
        combined,
        EVS_VEHICLE_ANCHORS_EN + EVS_VEHICLE_ANCHORS_AR,
    )

    if not (has_ev_anchor and has_vehicle_anchor):
        return hits

    for kw, contexts in compounds:
        if kw.lower() in combined or kw in combined:
            if any(_has_word(ctx, combined) for ctx in contexts):
                hits.append(kw)
    return hits


def match_tender(title: str, description: str = "") -> MatchResult:
    """
    Check if tender text matches any EV keywords.
    Returns which company (Climatech/EVS/Both) it's relevant to.

    Three-layer matching:
    1. Simple keywords (direct substring)
    2. Compound keywords (keyword + EV context required)
    3. Negative keywords (reject false positives)
    """
    combined = _normalize(f"{title} {description}")
    matched_keywords = []

    # --- Layer 1 & 2: Positive matching ---
    climatech_hits = (
        _check_simple(combined, CLIMATECH_KEYWORDS_EN + CLIMATECH_KEYWORDS_AR)
        + _check_compound(combined, CLIMATECH_COMPOUND_EN + CLIMATECH_COMPOUND_AR)
    )

    evs_hits = (
        _check_simple(combined, EVS_KEYWORDS_EN + EVS_KEYWORDS_AR)
        + _check_evs_compound(combined, EVS_COMPOUND_EN + EVS_COMPOUND_AR)
    )

    # Guard against generic electrical-supply tenders that happen to mention spare parts.
    evs_vehicle_anchor = _contains_any(combined, EVS_VEHICLE_ANCHORS_EN + EVS_VEHICLE_ANCHORS_AR)
    if evs_hits and _contains_any(combined, EVS_SUPPLY_NEGATIVE) and not evs_vehicle_anchor:
        evs_hits = []

    climatech = bool(climatech_hits)
    evs = bool(evs_hits)
    matched_keywords = climatech_hits + evs_hits

    if not (climatech or evs):
        return MatchResult(matched=False, company="", matched_keywords=[])

    # --- Layer 3: Negative keyword rejection ---
    for neg in NEGATIVE_KEYWORDS:
        if neg.lower() in combined:
            return MatchResult(matched=False, company="", matched_keywords=[])

    # --- Determine company ---
    if climatech and evs:
        company = "Both"
    elif climatech:
        company = "Climatech"
    else:
        company = "EVS"

    # Deduplicate matched keywords
    matched_keywords = list(dict.fromkeys(matched_keywords))

    return MatchResult(
        matched=True,
        company=company,
        matched_keywords=matched_keywords,
    )
