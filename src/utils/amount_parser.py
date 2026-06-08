"""
Utility parsers for amounts and dates found in bank statement PDFs.
Banks format these inconsistently — these handle the most common patterns.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from dateutil import parser as dateutil_parser


# ── Amount parsing ────────────────────────────────────────────────────────────

_AMOUNT_CLEAN  = re.compile(r"[\$,\s]")
_PAREN_NEG     = re.compile(r"^\((.+)\)$")          # (1,234.56) → negative
_TRAILING_SIGN = re.compile(r"^([\d,.]+)\s*(-|CR|DR)$", re.I)
_LEADING_SIGN  = re.compile(r"^(-|\+)?([\d,.]+)$")


def parse_amount(raw: str, debit_is_negative: bool = True) -> Optional[Decimal]:
    """
    Parse a raw amount string into a signed Decimal.

    Handles:
      - Standard:        "1,234.56"   → Decimal("1234.56")
      - Parenthetical:   "(500.00)"   → Decimal("-500.00")
      - Trailing sign:   "500.00-"    → Decimal("-500.00")
      - CR/DR suffix:    "500.00 DR"  → Decimal("-500.00") if debit_is_negative
      - Dollar sign:     "$1,234.56"  → Decimal("1234.56")
      - Negative dollar: "-$95.85"    → Decimal("-95.85")
      - Dollar trailing: "$500.00-"   → Decimal("-500.00")
    """
    if not raw:
        return None
    raw = raw.strip()

    # Normalise: remove dollar signs so that $-prefixed strings work with all
    # subsequent checks (e.g. "$22,996.60-" → "22,996.60-").
    # Handle both leading "-$" and "$" to preserve the sign.
    normed = re.sub(r"(?<!\d)\$", "", raw)   # strip $ not preceded by digit

    # Parenthetical negative: (1,234.56)
    paren = _PAREN_NEG.match(normed)
    if paren:
        cleaned = _AMOUNT_CLEAN.sub("", paren.group(1))
        try:
            return -Decimal(cleaned)
        except InvalidOperation:
            return None

    # Trailing sign or CR/DR
    trail = _TRAILING_SIGN.match(normed)
    if trail:
        num_str = _AMOUNT_CLEAN.sub("", trail.group(1))
        suffix  = trail.group(2).upper()
        try:
            val = Decimal(num_str)
            if suffix in ("-", "DR"):
                val = -val if debit_is_negative else val
            return val
        except InvalidOperation:
            return None

    # Standard / leading sign
    cleaned = _AMOUNT_CLEAN.sub("", normed)
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def is_debit_column(header: str) -> bool:
    """Detect if a table column header represents debits/withdrawals."""
    h = header.lower().strip()
    return any(w in h for w in ("debit", "withdrawal", "withdraw", "payment", "charge", "dr"))


def is_credit_column(header: str) -> bool:
    """Detect if a table column header represents credits/deposits."""
    h = header.lower().strip()
    return any(w in h for w in ("credit", "deposit", "cr", "addition"))


# ── Date parsing ──────────────────────────────────────────────────────────────

# Most common bank date formats, tried in order
_DATE_PATTERNS = [
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",       # 01/15/2024
    r"\b(\d{1,2}/\d{1,2}/\d{2})\b",        # 01/15/24
    r"\b(\d{1,2}-\d{1,2}-\d{4})\b",        # 01-15-2024
    r"\b([A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4})\b",  # Jan 15, 2024
    r"\b([A-Za-z]{3}\.?\s+\d{1,2})\b",     # Jan 15  (no year — needs ctx)
    r"\b(\d{4}-\d{2}-\d{2})\b",            # 2024-01-15 (ISO)
    r"\b(\d{8})\b",                         # 20240115
]

_COMPILED_PATTERNS = [re.compile(p) for p in _DATE_PATTERNS]


def parse_date(raw: str, year_hint: Optional[int] = None) -> Optional[date]:
    """
    Parse a raw date string into a Python date object.
    year_hint is used when the raw string has no year (e.g. "Jan 15").
    """
    if not raw:
        return None
    raw = raw.strip()

    # Try dateutil first (handles most cases)
    try:
        default_dt = date(year_hint or date.today().year, 1, 1)
        from datetime import datetime
        parsed = dateutil_parser.parse(
            raw,
            default=datetime(default_dt.year, 1, 1),
            dayfirst=False,
        )
        return parsed.date()
    except Exception:
        pass

    # Fallback: 8-digit compact YYYYMMDD
    if re.fullmatch(r"\d{8}", raw):
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            pass

    return None


def extract_date_range(text: str) -> tuple[Optional[date], Optional[date]]:
    """
    Extract statement period start/end from header text.
    Handles patterns like:
      "Statement Period: 01/01/2024 – 01/31/2024"
      "For the period January 1, 2024 to January 31, 2024"
    """
    dates_found: list[date] = []
    for pattern in _COMPILED_PATTERNS[:4]:  # only patterns with years
        for m in pattern.finditer(text):
            d = parse_date(m.group(1))
            if d:
                dates_found.append(d)

    dates_found = sorted(set(dates_found))
    if len(dates_found) >= 2:
        return dates_found[0], dates_found[-1]
    if len(dates_found) == 1:
        return dates_found[0], None
    return None, None
