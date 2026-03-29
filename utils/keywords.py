"""
Keyword matching for EV tender classification.
Maps tenders to Climatech Charger (chargers/installation) or EVS (fleet SMR).

Three matching layers:
1. Simple keywords   — direct substring match
2. Compound keywords — keyword matches only if an EV-context word also appears
3. Negative keywords — rejects false positives even after a positive match
"""

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Negative keywords — reject tender if ANY of these appear
# ---------------------------------------------------------------------------

NEGATIVE_KEYWORDS = [
    "drone", "railway", "railroad", "rail line", "metro line",
    "tramway", "forklift", "generator set", "elevator", "escalator",
    "phone charger", "laptop charger", "ups battery",
]

# ---------------------------------------------------------------------------
# Climatech Charger — chargers, installation, infrastructure, CPO
# ---------------------------------------------------------------------------

CLIMATECH_KEYWORDS_EN = [
    # Core charging
    "ev charging", "ev charger", "electric vehicle charging",
    "charger installation", "charging station", "charging infrastructure",
    "dc fast charger", "ac charger", "level 2 charger",
    "battery charger", "battery charging",
    "charging point", "charging equipment",
    "charging network", "smart charging",
    "supercharger",
    # Operator / management
    "cpo", "charge point operator",
    "cpms", "charge point management",
    "ocpp", "ocpi",
    # Regulation
    "sec certified", "sec certification",
    # Maintenance of chargers
    "charger maintenance",
    # Energy management
    "load balancing", "load management",
    # Broader
    "ev infrastructure",
    "e-mobility", "emobility",
]

CLIMATECH_KEYWORDS_AR = [
    "شحن السيارات الكهربائية",       # EV charging
    "شاحن سيارات كهربائية",         # EV charger
    "شاحن كهربائي",                 # electric charger
    "محطة شحن",                     # charging station
    "تركيب شاحن",                   # charger installation
    "بنية تحتية للشحن",             # charging infrastructure
    "المشغل المسؤول عن الشحن",      # CPO
    "نقطة شحن",                     # charging point
    "شاحن سريع",                    # fast charger
    "شبكة شحن",                     # charging network
    "الشحن الذكي",                  # smart charging
    "هيئة تنظيم الكهرباء",          # SEC (electricity regulator)
    # New
    "إدارة نقاط الشحن",             # charge point management
    "تشغيل نقاط الشحن",             # charge point operation
    "صيانة الشواحن",                # charger maintenance
    "تجهيز موقع الشحن",             # charging site preparation
]

# Compound: keyword only matches if an EV-context word is also present
CLIMATECH_COMPOUND_EN = [
    ("commissioning", ["charger", "ev", "charging", "شحن", "شاحن"]),
    ("electrical works", ["charger", "ev", "charging", "شحن"]),
    ("site preparation", ["charger", "ev", "charging", "شحن"]),
]

CLIMATECH_COMPOUND_AR = [
    ("أعمال كهربائية", ["شحن", "شاحن", "ev", "charger"]),   # electrical works + charging
    ("تجهيز الموقع", ["شحن", "شاحن", "ev", "charger"]),     # site preparation + charging
]

# ---------------------------------------------------------------------------
# EVS — EV Service, Maintenance & Repair
# ---------------------------------------------------------------------------

EVS_KEYWORDS_EN = [
    # Core maintenance
    "ev maintenance", "electric vehicle maintenance",
    "fleet maintenance", "fleet management", "ev fleet",
    # Service
    "ev service", "ev servicing",
    # Repair
    "ev repair", "collision repair",
    "battery repair", "battery diagnostics",
    "ev diagnostics", "powertrain repair",
    # Vehicles
    "electric bus", "electric truck",
    # Periodic
    "periodic servicing",
]

EVS_KEYWORDS_AR = [
    "صيانة السيارات الكهربائية",     # EV maintenance
    "صيانة مركبات كهربائية",         # electric vehicle maintenance
    "صيانة الأسطول",                 # fleet maintenance
    "إدارة الأسطول",                 # fleet management
    "أسطول كهربائي",                 # electric fleet
    "حافلة كهربائية",               # electric bus
    "شاحنة كهربائية",               # electric truck
    # New
    "خدمة المركبات الكهربائية",      # EV service
    "إصلاح المركبات الكهربائية",     # EV repair
    "إصلاح البطارية",               # battery repair
    "تشخيص البطارية",               # battery diagnostics
    "ورشة مركبات كهربائية",         # EV workshop
    "قطع غيار المركبات الكهربائية",  # EV spare parts
    "صيانة وقائية",                 # preventive maintenance
    "هيكل ودهان",                   # body & paint
]

# Compound: keyword only matches if an EV-context word is also present
EVS_COMPOUND_EN = [
    ("body shop", ["ev", "electric", "كهربائ"]),
    ("bodywork", ["ev", "electric", "كهربائ"]),
    ("spare parts", ["ev", "electric", "كهربائ"]),
    ("workshop", ["ev", "electric", "كهربائ"]),
    ("service center", ["ev", "electric", "كهربائ"]),
    ("vehicle inspection", ["ev", "electric", "كهربائ"]),
    ("preventive maintenance", ["ev", "electric", "fleet", "كهربائ", "أسطول"]),
    ("inverter", ["ev", "electric", "كهربائ"]),
    ("vehicle repair", ["ev", "electric", "كهربائ"]),
]

EVS_COMPOUND_AR = [
    ("ورشة", ["كهربائ", "ev", "electric"]),         # workshop + electric
    ("قطع غيار", ["كهربائ", "ev", "electric"]),      # spare parts + electric
    ("فحص مركبات", ["كهربائ", "ev", "electric"]),    # vehicle inspection + electric
]

# ---------------------------------------------------------------------------
# Combined list (for sites that support keyword search queries)
# ---------------------------------------------------------------------------

KEYWORDS_EN = list(dict.fromkeys(
    CLIMATECH_KEYWORDS_EN + EVS_KEYWORDS_EN
))

KEYWORDS_AR = list(dict.fromkeys(
    CLIMATECH_KEYWORDS_AR + EVS_KEYWORDS_AR
))

# Also add the general umbrella terms
KEYWORDS_EN += ["electric vehicle", "مركبة كهربائية"]


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
        + _check_compound(combined, EVS_COMPOUND_EN + EVS_COMPOUND_AR)
    )

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


def get_all_keywords() -> list[str]:
    """Return all keywords combined (for sites that support search)."""
    return KEYWORDS_EN + KEYWORDS_AR
