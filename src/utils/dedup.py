"""
dedup.py — Transaction deduplication and repeated header filtering.

Handles three real-world problems:
  1. Page headers repeating on every page (same date/amount row appears 2-4x)
  2. Exact duplicate transactions (same date + description + amount)
  3. Near-duplicate transactions (same date + amount, slightly different description)

Drop this file into src/utils/dedup.py and call clean() on any
list of Transaction objects before building the ParsedStatement.

Usage:
    from src.utils.dedup import clean
    transactions = clean(transactions, warn=self.warn)
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Callable, Optional

from src.models import Transaction


# ── Header row patterns ───────────────────────────────────────────────────────
# These are text strings that appear in table header rows that pdfplumber
# sometimes picks up as transaction descriptions.

_HEADER_PATTERNS = [
    # Generic column headers
    r"^(date|posted|trans(action)?|description|detail|memo|narr(ative)?)$",
    r"^(amount|amt|debit|credit|withdrawal|deposit|balance|bal)$",
    r"^(charges?|payments?|activity|particulars?)$",

    # Summary / subtotal rows
    r"^(total|sub.?total|balance\s+forward|beginning\s+balance|ending\s+balance)$",
    r"^(opening\s+balance|closing\s+balance|new\s+balance|previous\s+balance)$",
    r"^(carried\s+forward|brought\s+forward|page\s+total)$",

    # Bank-specific header text
    r"^account\s+activity$",
    r"^transaction\s+(history|detail|list)$",
    r"^(deposits?\s+and\s+credits?|withdrawals?\s+and\s+debits?)$",
    r"^purchases?\s+and\s+adjustments?$",
    r"^payments?,?\s+credits?\s+and\s+adjustments?$",

    # Date range headers (e.g. "January 1 - January 31")
    r"^[a-z]+\s+\d{1,2}\s*[-–]\s*[a-z]+\s+\d{1,2}(,?\s*\d{4})?$",

    # Pure punctuation / separator rows
    r"^[-=_*]{3,}$",

    # Very short descriptions that are clearly not transactions
    r"^.{0,2}$",
]

_COMPILED_HEADERS = [re.compile(p, re.I) for p in _HEADER_PATTERNS]


def _is_header_row(tx: Transaction) -> bool:
    """Return True if this transaction looks like a misread header row."""
    desc = (tx.description or "").strip()
    return any(p.match(desc) for p in _COMPILED_HEADERS)


# ── Deduplication key ─────────────────────────────────────────────────────────

def _exact_key(tx: Transaction) -> tuple:
    """Key for exact duplicate detection: date + rounded amount + description."""
    return (
        tx.date,
        round(tx.amount, 2),
        tx.description.strip().lower(),
    )


def _near_key(tx: Transaction) -> tuple:
    """
    Key for near-duplicate detection: date + rounded amount.
    Two transactions on the same day for the same amount are suspicious
    unless the descriptions are clearly different.
    """
    return (tx.date, round(tx.amount, 2))


def _descriptions_differ(a: str, b: str, threshold: float = 0.35) -> bool:
    """
    Return True if two descriptions are meaningfully different.
    Uses word overlap (Jaccard on word sets) as the primary signal.
    Short strings like CHIPOTLE 2847 vs CHIPOTLE MEXICAN GRILL share
    the word CHIPOTLE and are treated as similar (not different).
    """
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return False
    # Word-level Jaccard (primary)
    wa = set(re.sub(r"[^a-z0-9\s]", " ", a).split())
    wb = set(re.sub(r"[^a-z0-9\s]", " ", b).split())
    if wa and wb:
        word_sim = len(wa & wb) / len(wa | wb)
        if word_sim >= threshold:
            return False
    # Bigram fallback
    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s) - 1))
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return True
    char_sim = len(ba & bb) / len(ba | bb)
    return char_sim < threshold


# ── Main clean function ───────────────────────────────────────────────────────

def clean(
    transactions: list[Transaction],
    warn: Optional[Callable[[str], None]] = None,
    remove_near_dupes: bool = False,
) -> list[Transaction]:
    """
    Clean a list of transactions by:
      1. Removing header/subtotal rows misread as transactions
      2. Removing exact duplicates (same date + amount + description)
      3. Optionally flagging near-duplicates (same date + amount)

    Args:
        transactions:       raw list of Transaction objects
        warn:               callable to record warning strings (e.g. self.warn)
        remove_near_dupes:  if True, remove near-duplicates too (conservative
                            — only use when statement is known single-account)

    Returns:
        Cleaned list of Transaction objects
    """
    if not transactions:
        return transactions

    result: list[Transaction] = []
    exact_seen: set[tuple] = set()
    near_seen: dict[tuple, Transaction] = {}
    removed_headers = 0
    removed_exact   = 0
    removed_near    = 0

    for tx in transactions:

        # ── Step 1: Remove header rows ────────────────────────────────────
        if _is_header_row(tx):
            removed_headers += 1
            continue

        # ── Step 2: Remove exact duplicates ───────────────────────────────
        key = _exact_key(tx)
        if key in exact_seen:
            removed_exact += 1
            continue
        exact_seen.add(key)

        # ── Step 3: Near-duplicate detection ─────────────────────────────
        if remove_near_dupes:
            nkey = _near_key(tx)
            if nkey in near_seen:
                existing = near_seen[nkey]
                if not _descriptions_differ(tx.description, existing.description):
                    # Descriptions are too similar — almost certainly a duplicate
                    removed_near += 1
                    continue
                # Descriptions differ meaningfully — keep both, just warn
                if warn:
                    warn(
                        f"Possible near-duplicate: {tx.date} {tx.amount} — "
                        f'"{existing.description}" vs "{tx.description}"'
                    )
            else:
                near_seen[nkey] = tx

        result.append(tx)

    # ── Report what was removed ───────────────────────────────────────────
    if warn:
        if removed_headers:
            warn(f"Removed {removed_headers} header/subtotal row(s) misread as transactions.")
        if removed_exact:
            warn(f"Removed {removed_exact} exact duplicate transaction(s).")
        if removed_near:
            warn(f"Removed {removed_near} near-duplicate transaction(s).")

    return result


# ── Page header deduplication (for multi-page statements) ─────────────────────

def dedup_across_pages(
    pages: list[list[Transaction]],
    warn: Optional[Callable[[str], None]] = None,
) -> list[Transaction]:
    """
    Merge transactions extracted page-by-page, removing cross-page duplicates.

    Some parsers extract each page independently, causing the last row of
    page N and the first row of page N+1 to be the same transaction (because
    the table header repeats and the last row carries over).

    This is smarter than a flat dedup — it only checks the boundary between
    consecutive pages (last 3 rows of page N vs first 3 rows of page N+1).

    Args:
        pages: list of per-page transaction lists
        warn:  warning callback

    Returns:
        Single flat list with cross-page duplicates removed
    """
    if not pages:
        return []

    merged: list[Transaction] = list(pages[0])

    for i, page in enumerate(pages[1:], start=1):
        if not page:
            continue

        # Check the boundary: last 5 of previous vs first 5 of this page
        boundary_keys = {_exact_key(t) for t in merged[-5:]}
        overlap = 0

        for tx in page:
            key = _exact_key(tx)
            if key in boundary_keys:
                overlap += 1
                continue
            merged.append(tx)
            boundary_keys.add(key)

        if overlap and warn:
            warn(f"Page {i+1}: removed {overlap} cross-page duplicate(s).")

    return merged