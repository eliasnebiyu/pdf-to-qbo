"""
American Express (Amex) statement parser.

Statement layout (plain text, no formal tables):
  Page structure:
    - Account summary (page 1)
    - Terms/legal (page 2)
    - Transactions start page 3+:
        "Payments and Credits"  section → payments already negative in PDF
        "New Charges"           section → charges positive in PDF
        "Fees"                  section → fees positive in PDF

  Transaction line format:
    MM/DD/YY[*]  MERCHANT NAME CITY ST  [-]$amount[⧫]
    where * marks a posting date, ⧫ marks Pay-Over-Time balance.

  Multi-line transactions: continuation lines (phone numbers, zip codes,
  sub-merchant names) follow the date line and don't start with a date.

  OFX sign convention:
    - Payments (negative in PDF) → positive in OFX  (negate)
    - Charges  (positive in PDF) → negative in OFX  (negate)
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from src.models import ParsedStatement, Transaction, BankAccount, AccountType
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_AMEX_MARKERS = [
    "americanexpress.com",
    "american express",
    "membershiprewards.com",
    "membership rewards",
    "amex epayment",
]

# Corroborating signals to confirm this is really an Amex statement
# (not another bank that references Amex in a transaction)
_AMEX_CORROBORATE = [
    "pay over time",
    "membership rewards",
    "account ending",
    "closing date",
    "business platinum",
    "business gold",
    "blue cash",
    "gold card",
    "platinum card",
]

# MM/DD/YY with optional * suffix (posting date marker)
_AMEX_DATE = re.compile(r"^\d{2}/\d{2}/\d{2}\*?")

# Full transaction line: date [*] description [-]$amount [⧫]
_AMEX_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{2})\*?\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amount>-?\$[\d,]+\.\d{2})(?:⧫)?$"
)

# Section headers
_PAYMENT_SECTION = re.compile(
    r"^(payments?\s+and\s+credits?|payments?)\s*$", re.I
)
_CHARGE_SECTION = re.compile(
    r"^(new\s+charges?|charges?)\s*$", re.I
)
_FEE_SECTION = re.compile(r"^fees?\s*$", re.I)

# Lines to skip inside transaction blocks
_SKIP_PATTERNS = [
    re.compile(r"^(summary|detail|amount|total|pay\s+in\s+full|pay\s+over\s+time)", re.I),
    re.compile(r"^Card\s+Ending\d", re.I),
    re.compile(r"^\d{4}$"),                 # 4-digit zip codes
    re.compile(r"^\+1\d{10}$"),             # phone numbers
    re.compile(r"^[A-Z0-9]{6,}\s+\d{5}$"), # reference + zip
]


class AmexParser(BaseParser):
    """Parser for American Express credit card statements."""

    bank_name = "American Express"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        # Primary marker alone is sufficient
        if "americanexpress.com" in text_lower or "membershiprewards.com" in text_lower:
            return True
        # Secondary markers need corroboration
        has_marker = any(m in text_lower for m in _AMEX_MARKERS)
        has_corroboration = sum(1 for s in _AMEX_CORROBORATE if s in text_lower) >= 2
        return has_marker and has_corroboration

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self._build_amex_account(text)
        account.account_type = AccountType.CREDIT
        txns    = self._parse_transactions(text)
        txns    = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="amex",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account ───────────────────────────────────────────────────────────────

    def _build_amex_account(self, text: str) -> BankAccount:
        account = self.build_account(text, bank_name="American Express")

        # Account number ending: "Account Ending8-86001" or "Account Ending 8-86001"
        an = re.search(r"Account\s+Ending\s*([0-9A-Z\-]+)", text, re.I)
        if an:
            account.account_id = an.group(1).strip()

        # Closing date: "Closing Date05/25/26" or "Closing Date 05/25/26"
        cd = re.search(r"Closing\s+Date\s*(\d{2}/\d{2}/\d{2})", text, re.I)
        if cd:
            account.statement_end = parse_date(cd.group(1))

        # Next closing date → statement period start
        ncd = re.search(r"Next\s+Closing\s+Date\s*(\d{2}/\d{2}/\d{2})", text, re.I)
        # Opening/closing balance
        nb = re.search(r"New\s+Balance\s+\$?([\d,]+\.\d{2})", text, re.I)
        pb = re.search(r"Previous\s+Balance\s+\$?([\d,]+\.\d{2})", text, re.I)
        if pb:
            account.opening_balance = parse_amount(pb.group(1))
        if nb:
            account.closing_balance = parse_amount(nb.group(1))

        return account

    # ── Transactions ──────────────────────────────────────────────────────────

    def _parse_transactions(self, text: str) -> list[Transaction]:
        txns:    list[Transaction] = []
        year = self._year_hint()

        in_payment_section = False
        in_charge_section  = False
        in_fee_section     = False
        in_tx_block        = False   # inside a Detail sub-section

        pending: Optional[dict] = None

        page_num = 0
        for line in text.splitlines():
            raw = line
            line = line.strip()

            # Track page breaks (pdfplumber concatenates pages with newlines)
            # We approximate: not important for source_page on Amex (complex layout)

            if not line:
                continue

            # ── Section header detection ──────────────────────────────────────
            if _PAYMENT_SECTION.match(line):
                self._flush(pending, txns, page_num); pending = None
                in_payment_section = True
                in_charge_section  = False
                in_fee_section     = False
                in_tx_block        = False
                continue

            if _CHARGE_SECTION.match(line):
                self._flush(pending, txns, page_num); pending = None
                in_payment_section = False
                in_charge_section  = True
                in_fee_section     = False
                in_tx_block        = False
                continue

            if _FEE_SECTION.match(line):
                self._flush(pending, txns, page_num); pending = None
                in_payment_section = False
                in_charge_section  = False
                in_fee_section     = True
                in_tx_block        = False
                continue

            if not (in_payment_section or in_charge_section or in_fee_section):
                continue

            # "Detail" sub-header → marks start of transaction lines
            if re.match(r"^Detail\b", line, re.I):
                in_tx_block = True
                continue

            # "Summary" sub-header → end of transaction lines
            if re.match(r"^Summary\b", line, re.I):
                in_tx_block = False
                continue

            if not in_tx_block:
                # Try to detect transactions even without a "Detail" header
                if _AMEX_DATE.match(line):
                    in_tx_block = True
                else:
                    continue

            # ── Skip noise lines ──────────────────────────────────────────────
            if any(p.match(line) for p in _SKIP_PATTERNS):
                continue

            # ── Transaction line ──────────────────────────────────────────────
            m = _AMEX_LINE.match(line)
            if m:
                self._flush(pending, txns, page_num); pending = None

                tx_date = parse_date(m.group("date"), year_hint=year)
                if not tx_date:
                    continue

                amount = parse_amount(m.group("amount"))
                if amount is None:
                    continue

                # OFX sign: negate PDF sign
                # (negative PDF payment → positive OFX; positive PDF charge → negative OFX)
                amount = -amount

                pending = {
                    "date":    tx_date,
                    "desc":    m.group("desc").strip(),
                    "amount":  amount,
                    "page":    page_num + 1,
                }
                continue

            # ── Continuation line ─────────────────────────────────────────────
            if pending and not _AMEX_DATE.match(line):
                # Ignore pure reference/phone/zip lines
                if not re.match(r"^(\d{5,}|\+\d{10,}|[A-Z0-9]{8,}\s+\d{5})$", line):
                    # Append only if it looks like text (not pure numbers/refs)
                    if re.search(r"[A-Za-z]{2,}", line):
                        pending["desc"] += f" {line}"

        self._flush(pending, txns, page_num)
        return txns

    @staticmethod
    def _flush(pending: Optional[dict], txns: list, page_num: int):
        if pending is None:
            return
        try:
            txns.append(Transaction(
                date=pending["date"],
                description=pending["desc"] or "Transaction",
                amount=pending["amount"],
                source_page=pending.get("page", page_num + 1),
            ))
        except Exception:
            pass

    def _year_hint(self) -> int:
        m = re.search(r"\b(20\d{2})\b", self.full_text)
        return int(m.group(1)) if m else date.today().year
