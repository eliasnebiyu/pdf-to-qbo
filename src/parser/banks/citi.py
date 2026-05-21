"""
Citi bank statement parser.

Citi has two distinct statement layouts depending on account type:

  1. Checking / Savings (CitiGold, Citi Priority, Basic)
     - Single signed "Amount" column (negative = debit)
     - Running balance column
     - Date format: MM/DD or MM/DD/YYYY

  2. Credit Card (Citi Double Cash, Citi Custom Cash, etc.)
     - Separate "Charges" and "Payments/Credits" columns
     - No running balance
     - Section headers: "Purchases and Adjustments", "Payments, Credits and Adjustments"

Both layouts are handled here. The parser detects which one it's
looking at per-table and applies the right column mapping.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from src.models import ParsedStatement, Transaction, AccountType
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

# ── Citi identity markers ─────────────────────────────────────────────────────
_CITI_MARKERS = [
    "citibank",
    "citi bank",
    "citigold",
    "citi priority",
    "citi double cash",
    "citi custom cash",
    "citi rewards",
    "citi.com",
    "citicards.com",
    "citicard",
    "thankyou rewards",          # Citi ThankYou card
    "costco anywhere visa",      # Citi-issued Costco card
]

# ── Section headers that bracket transaction blocks ───────────────────────────
_CHECKING_SECTIONS = re.compile(
    r"(account\s+activity|transaction\s+history|transactions|"
    r"deposits?\s+and\s+credits?|withdrawals?\s+and\s+debits?)",
    re.I,
)
_CC_SECTIONS = re.compile(
    r"(purchases?\s+and\s+adjustments?|payments?,?\s+credits?\s+and\s+adjustments?|"
    r"new\s+charges?|account\s+activity)",
    re.I,
)

# ── Line-level regex (text-mode fallback) ─────────────────────────────────────
# Checking/savings: "01/15  ATM WITHDRAWAL          -200.00   4,250.00"
_CHK_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"\s{1,6}"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$",
)

# Credit card: "01/15  01/16  AMAZON.COM             89.99"
_CC_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"(?:\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?)?"   # optional posting date
    r"\s{1,6}"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>[\d,]+\.\d{2})"               # always positive on CC
    r"$",
)


class CitiParser(BaseParser):
    """
    Parser for Citi checking, savings, and credit card statements.

    Detection order:
      1. Table extraction (most reliable for digital PDFs)
      2. Line-by-line text parsing (fallback)

    Credit-card statements produce positive amounts for charges and
    negative amounts for payments/credits to match OFX convention
    (debit = negative, credit = positive from the account perspective).
    """

    bank_name = "Citibank"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(marker in text_lower for marker in _CITI_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        is_cc   = self._is_credit_card(text)
        account = self.build_account(text, bank_name="Citibank")

        if is_cc:
            account.account_type = AccountType.CREDIT

        txns = self._extract_transactions(text, is_cc=is_cc)

        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="citi_cc" if is_cc else "citi_chk",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account type detection ────────────────────────────────────────────────

    def _is_credit_card(self, text: str) -> bool:
        """Return True if this looks like a credit card statement."""
        text_lower = text.lower()
        cc_signals = [
            "minimum payment",
            "credit limit",
            "new balance",
            "payment due",
            "purchases and adjustments",
            "new charges",
            "citicards",
            "double cash",
            "custom cash",
            "costco anywhere",
            "thankyou rewards",
        ]
        return sum(1 for s in cc_signals if s in text_lower) >= 2

    # ── Main dispatch ─────────────────────────────────────────────────────────

    def _extract_transactions(
        self, text: str, is_cc: bool
    ) -> list[Transaction]:
        # Try table extraction first
        txns = self._from_tables(is_cc)
        if txns:
            return txns

        # Fallback: line-by-line
        self.warn("Citi: no table transactions found — using line parser.")
        return self._from_lines(text, is_cc)

    # ── Table extraction ──────────────────────────────────────────────────────

    def _from_tables(self, is_cc: bool) -> list[Transaction]:
        txns: list[Transaction] = []
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                if is_cc:
                    parsed = self._parse_cc_table(table)
                else:
                    parsed = self._parse_chk_table(table)
                txns.extend(parsed)
        return txns

    # ── Checking / savings table ──────────────────────────────────────────────

    def _parse_chk_table(
        self, table: list[list[str]]
    ) -> list[Transaction]:
        """
        Citi checking table layout (most common):
          Date | Description | Debit (-) | Credit (+) | Balance
        or:
          Date | Description | Amount    | Balance

        Both single-amount and split debit/credit columns are handled.
        """
        if not table or len(table) < 2:
            return []

        # Find the header row
        header, start = self._find_header(table)
        if header is None:
            return []

        h = [c.lower().strip() for c in header]

        col_date    = self._col(h, ["date", "posted", "trans"])
        col_desc    = self._col(h, ["desc", "detail", "memo", "narr", "transaction"])
        col_amount  = self._col(h, ["amount", "amt"])
        col_debit   = self._col(h, ["debit", "withdrawal", "withdraw"])
        col_credit  = self._col(h, ["credit", "deposit"])
        col_balance = self._col(h, ["balance", "bal"])

        if col_date is None or col_desc is None:
            return []

        year = self._year_hint()
        txns: list[Transaction] = []

        for row in table[start:]:
            if not row:
                continue

            date_raw = (row[col_date] if col_date < len(row) else "").strip()
            if not re.match(r"\d{1,2}/\d{1,2}", date_raw):
                continue  # skip header repeats / subtotal rows

            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue

            desc = (row[col_desc] if col_desc < len(row) else "").strip()
            if not desc:
                continue

            # Resolve amount
            amount: Optional[Decimal] = None
            if col_amount is not None and col_amount < len(row) and row[col_amount]:
                amount = parse_amount(row[col_amount])
            elif col_debit is not None or col_credit is not None:
                dr = parse_amount(row[col_debit])  if col_debit  is not None and col_debit  < len(row) else None
                cr = parse_amount(row[col_credit]) if col_credit is not None and col_credit < len(row) else None
                if dr:
                    amount = -abs(dr)
                elif cr:
                    amount = abs(cr)

            if amount is None:
                continue

            balance: Optional[Decimal] = None
            if col_balance is not None and col_balance < len(row) and row[col_balance]:
                balance = parse_amount(row[col_balance])

            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=desc,
                    amount=amount,
                    balance=balance,
                ))
            except Exception as e:
                self.warn(f"Citi chk row error: {e}")

        return txns

    # ── Credit card table ─────────────────────────────────────────────────────

    def _parse_cc_table(
        self, table: list[list[str]]
    ) -> list[Transaction]:
        """
        Citi credit card table layout:
          Date | Description | Amount
        where purchases are positive and payments are negative in the PDF.

        We flip the sign to OFX convention: charges = negative (money out),
        payments/credits = positive (money in).

        Citi sometimes has a separate "Payments and Credits" section where
        amounts are already shown as positive — we detect this via the
        section context and negate them.
        """
        if not table or len(table) < 2:
            return []

        header, start = self._find_header(table)

        # If no clear header, try heuristic: first row with a date in col 0
        if header is None:
            # Many Citi CC tables have no explicit header — just rows
            start = 0
            # Guess column positions: Date(0), Desc(1 or 2), Amount(-1)
            col_date   = 0
            col_desc   = 1
            col_amount = -1          # last column
            col_is_payment = None
        else:
            h = [c.lower().strip() for c in header]
            col_date   = self._col(h, ["date", "trans", "posted"]) or 0
            col_desc   = self._col(h, ["desc", "detail", "memo", "narr", "transaction"]) or 1
            col_amount = self._col(h, ["amount", "charge", "debit", "credit"]) or -1
            col_is_payment = None

        year = self._year_hint()
        txns: list[Transaction] = []
        in_payment_section = False

        for row in table[start:]:
            if not row:
                continue

            # Detect section transitions within the table FIRST —
            # before the date check discards divider rows.
            # Citi merges purchases + payments into one table with a
            # plain-text divider row between the two sections.
            joined = " ".join(c for c in row if c).lower()
            if re.search(r"payment|credit.*adjust", joined) and not re.match(r"\d{1,2}/\d", joined):
                in_payment_section = True
                continue
            if re.search(r"purchase|new charge", joined) and not re.match(r"\d{1,2}/\d", joined):
                in_payment_section = False
                continue

            # Resolve actual column indices (handle negative index)
            n = len(row)
            amt_idx = col_amount if col_amount >= 0 else (n + col_amount)

            date_raw = (row[col_date] if col_date < n else "").strip()

            # Citi CC sometimes has two date columns (transaction + posting)
            # We take the first date-looking cell
            if not re.match(r"\d{1,2}/\d{1,2}", date_raw):
                # Try second cell
                if n > 1 and re.match(r"\d{1,2}/\d{1,2}", (row[1] or "").strip()):
                    date_raw = row[1].strip()
                    col_desc = 2
                else:
                    continue

            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue

            desc = (row[col_desc] if col_desc < n else "").strip()
            if not desc or desc.lower() in ("total", "subtotal", "balance"):
                continue

            amount_raw = (row[amt_idx] if 0 <= amt_idx < n else "").strip()
            amount = parse_amount(amount_raw)
            if amount is None:
                continue

            # OFX sign convention for credit cards:
            # - Purchases/charges → negative (money leaves the account)
            # - Payments/credits  → positive (money comes in)
            if in_payment_section:
                amount = abs(amount)      # payment = positive
            else:
                amount = -abs(amount)     # charge  = negative

            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=desc,
                    amount=amount,
                ))
            except Exception as e:
                self.warn(f"Citi CC row error: {e}")

        return txns

    # ── Line-by-line fallback ─────────────────────────────────────────────────

    def _from_lines(self, text: str, is_cc: bool) -> list[Transaction]:
        txns: list[Transaction] = []
        year = self._year_hint()
        pattern = _CC_LINE if is_cc else _CHK_LINE

        in_tx_section = False
        in_payment_section = False

        for line in text.splitlines():
            line = line.strip()

            # Track section context
            if _CHECKING_SECTIONS.search(line) or _CC_SECTIONS.search(line):
                in_tx_section = True
                in_payment_section = bool(
                    re.search(r"payment|credit.*adjust", line, re.I)
                )
                continue

            if not in_tx_section:
                continue

            # Skip divider lines and totals
            if re.match(r"^[-=]{5,}$", line):
                continue
            if re.search(r"^\s*(total|subtotal|balance\s+forward)", line, re.I):
                continue

            m = pattern.match(line)
            if not m:
                continue

            parsed_date = parse_date(m.group("date"), year_hint=year)
            if not parsed_date:
                continue

            amount = parse_amount(m.group("amount"))
            if amount is None:
                continue

            # Apply sign convention
            if is_cc:
                amount = abs(amount) if in_payment_section else -abs(amount)

            balance: Optional[Decimal] = None
            if not is_cc and "balance" in m.groupdict() and m.group("balance"):
                balance = parse_amount(m.group("balance"))

            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=m.group("desc").strip(),
                    amount=amount,
                    balance=balance,
                ))
            except Exception as e:
                self.warn(f"Citi line error ({line!r}): {e}")

        return txns

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_header(
        self, table: list[list[str]]
    ) -> tuple[Optional[list[str]], int]:
        """
        Scan the first 5 rows for one that looks like a column header.
        Returns (header_row, first_data_row_index).
        """
        for i, row in enumerate(table[:5]):
            if not row:
                continue
            joined = " ".join(c.lower() for c in row if c)
            # A header row has at least two recognisable column keywords
            hits = sum(
                1 for kw in ("date", "desc", "amount", "balance",
                             "debit", "credit", "withdrawal", "deposit",
                             "transaction", "detail", "charge")
                if kw in joined
            )
            if hits >= 2:
                return row, i + 1
        return None, 0

    @staticmethod
    def _col(headers: list[str], keywords: list[str]) -> Optional[int]:
        for i, h in enumerate(headers):
            if any(k in h for k in keywords):
                return i
        return None

    def _year_hint(self) -> int:
        """Extract the statement year from the full text, or default to today."""
        m = re.search(r"\b(20\d{2})\b", self.full_text)
        return int(m.group(1)) if m else date.today().year