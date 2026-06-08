"""
U.S. Bank statement parser.

Statement layout:
  - Sections: "Other Deposits", "Other Withdrawals", "Checks Presented Conventionally"
  - pdfplumber extracts SOME transactions as single-row tables with 5 columns:
      [Date, Type+Ref, To/From+Details, Empty, Amount]
    and the REMAINING transactions appear only in the plain-text extraction.
  - Date format: "May 7", "May12", "May 1" (month name + day, no year)
  - Amount: positive for deposits, "amount-" suffix (or "$ amount-") for withdrawals
  - No running balance per transaction (daily balance summary at end only)

Strategy: run BOTH table extraction and text-line extraction, then deduplicate
with clean().  Each approach captures different rows; together they get all of them.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from src.models import ParsedStatement, Transaction, BankAccount
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_USB_MARKERS = [
    "usbank.com",
    "u.s. bank national association",
    "u.s. bank silver",
    "u.s. bank gold",
    "u.s. bank platinum",
    "u.s. bank checking",
    "800-673-3555",            # U.S. Bank 24-Hour Business Solutions line
]

# Month-name date:  "May 7", "May12", "May 1", "May31"
_MONTH_DATE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})$",
    re.I,
)

# Text-mode transaction line.
# US Bank plain-text format:
#   "May 7 Electronic Deposit From STATE OF OHIO $ 10,523.66"
#   "May 1 Electronic Withdrawal To PAYPAL $ 9.99-"
#   "May 1 Electronic Withdrawal To PAYCHEX EIB 204.93-"
#   "May12 Mobile Banking Transfer From Account 230107642590 40,000.00"
#
# The $ sign is optional and may have a space after it; the trailing - is
# present on withdrawals but absent on deposits.  We capture the numeric
# amount separately from the trailing sign so we can pass a clean string
# to parse_amount().
_TEXT_LINE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})"  # Month Day
    r"\s+(?P<desc>.+?)"                             # lazy description
    r"\s+\$?\s*(?P<amount>[\d,]+\.\d{2})"          # amount (optional $ + optional space)
    r"(?P<neg>-)?"                                  # optional trailing minus (withdrawal)
    r"\s*$",
    re.I,
)

# Check entry within a "Checks Presented Conventionally" line.
# Two checks can appear on the same line, so we use finditer (no ^ anchor):
#   "1080 May 8  9252275911  4,500.00  2698* May18  8054232835  75.07"
_CHECK_ENTRY = re.compile(
    r"(?P<num>\d+)\*?\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(?P<day>\d{1,2})"
    r"\s+\d+\s+(?P<amount>[\d,]+\.\d{2})",
    re.I,
)


class USBankParser(BaseParser):
    """Parser for U.S. Bank checking and savings statements."""

    bank_name = "U.S. Bank"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _USB_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self._build_usb_account(text)
        txns    = self._extract_all(text)
        txns    = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="us_bank",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account ───────────────────────────────────────────────────────────────

    def _build_usb_account(self, text: str) -> BankAccount:
        account = self.build_account(text, bank_name="U.S. Bank")

        # Account number pattern: "Account Number:\n1 301 2629 4193"
        an = re.search(r"Account\s+Number[:\s]+([\d\s\-]+)", text, re.I)
        if an:
            account.account_id = re.sub(r"\s+", "", an.group(1))[:20]

        # Statement period: "May 1, 2026\nthrough\nMay 31, 2026"
        period = re.search(
            r"Statement\s+Period[:\s]+"
            r"([A-Za-z]+\s+\d+,?\s+\d{4})"
            r"[\s\S]{1,30}?"
            r"([A-Za-z]+\s+\d+,?\s+\d{4})",
            text, re.I,
        )
        if period:
            account.statement_start = parse_date(period.group(1))
            account.statement_end   = parse_date(period.group(2))

        # Opening balance
        ob = re.search(r"Beginning\s+Balance\s+on\s+\w+\s+\d+\s+\$?\s*([\d,]+\.\d{2})", text, re.I)
        if ob:
            account.opening_balance = parse_amount(ob.group(1))

        # Closing balance
        cb = re.search(r"Ending\s+Balance\s+on\s+\w+\s+\d+,?\s+\d{4}\s+\$?\s*([\d,]+\.\d{2})", text, re.I)
        if cb:
            account.closing_balance = parse_amount(cb.group(1))

        return account

    # ── Transaction extraction ────────────────────────────────────────────────

    def _extract_all(self, text: str) -> list[Transaction]:
        """Run table-based AND text-based extraction, then merge.

        US Bank PDFs mix table-extracted rows (deposits/withdrawals sections)
        with plain-text rows that pdfplumber doesn't present as tables.
        Running both and deduplicating via clean() captures everything.
        """
        table_txns = self._from_tables()
        text_txns  = self._from_lines(text)
        if not table_txns and not text_txns:
            self.warn("US Bank: no transactions found via table or line parser.")
        # Merge — clean() deduplicates exact (date, desc, amount) matches
        return table_txns + text_txns

    def _from_tables(self) -> list[Transaction]:
        """
        Each US Bank transaction is a 1-row table with 5 columns:
          [Date, TypeAndRef, ToFrom+Details, Empty, Amount]
        Deposits have positive amounts; withdrawals have "amount-" suffix.
        """
        txns: list[Transaction] = []
        year = self._year_hint()

        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                if not table or len(table[0]) < 3:
                    continue

                row = table[0]   # each US Bank TX is a single-row table
                date_raw = (row[0] or "").strip()
                if not _MONTH_DATE.match(date_raw):
                    continue

                # Build description from cols 1 + 2 (take only first line of each)
                type_col   = re.split(r"\n", row[1] or "")[0].strip()
                detail_col = re.split(r"\n", row[2] or "")[0].strip()
                desc = f"{type_col} {detail_col}".strip()
                if not desc:
                    desc = "Transaction"

                # Amount in last non-empty column
                amt_raw = ""
                for col in reversed(row):
                    if col and col.strip():
                        amt_raw = col.strip()
                        break

                amount = parse_amount(amt_raw)
                if amount is None:
                    continue

                # US Bank encodes sign directly in the amount string:
                # - Withdrawals: "204.93-"  → parse_amount returns negative  ✓
                # - Deposits:    "40,000.00" → parse_amount returns positive  ✓

                tx_date = parse_date(f"{date_raw} {year}", year_hint=year)
                if tx_date is None:
                    continue

                try:
                    txns.append(Transaction(
                        date=tx_date,
                        description=desc,
                        amount=amount,
                        source_page=page_num + 1,
                    ))
                except Exception as e:
                    self.warn(f"US Bank table row error: {e}")

        # Also grab checks from text (they're not in single-row tables)
        txns.extend(self._checks_from_text(self.full_text, year))
        return txns

    def _checks_from_text(self, text: str, year: int) -> list[Transaction]:
        """Parse check lines from plain text.

        Two checks can appear on the same text line, so we use finditer.
        """
        txns: list[Transaction] = []
        in_checks = False
        for line in text.splitlines():
            ls = line.strip()
            if re.search(r"checks presented conventionally", ls, re.I):
                in_checks = True
                continue
            if re.search(r"balance summary|conventional checks paid|total.*checks", ls, re.I):
                in_checks = False
                continue
            if not in_checks:
                continue

            for m in _CHECK_ENTRY.finditer(ls):
                amount = parse_amount(m.group("amount"))
                if amount is None:
                    continue
                month_day = f"{m.group('month')} {m.group('day')}"
                tx_date = parse_date(f"{month_day} {year}", year_hint=year)
                if not tx_date:
                    continue
                check_num = m.group("num")
                try:
                    txns.append(Transaction(
                        date=tx_date,
                        description=f"Check #{check_num}",
                        amount=-abs(amount),
                    ))
                except Exception as e:
                    self.warn(f"US Bank check parse error: {e}")

        return txns

    def _from_lines(self, text: str) -> list[Transaction]:
        """Text-mode extraction for US Bank statements.

        Captures transactions that pdfplumber does not present as tables.
        Uses section tracking ("Other Deposits" / "Other Withdrawals") to
        determine sign, and relies on the trailing '-' in withdrawal amounts.
        """
        txns: list[Transaction] = []
        year = self._year_hint()
        in_deposit    = False
        in_withdrawal = False

        for raw_line in text.splitlines():
            ls = raw_line.strip()

            # ── Stop conditions (check BEFORE section headers) ────────────
            # "Total Other Deposits/Withdrawals", "Balance Summary",
            # "Analysis Service Charge", "Conventional Checks Paid"
            if re.search(
                r"balance summary|analysis service charge"
                r"|total\s+other\s+(deposits?|withdrawals?)"
                r"|conventional checks paid",
                ls, re.I,
            ):
                in_deposit = in_withdrawal = False
                continue

            # ── Section header detection (anchored at start of line) ──────
            if re.match(r"other deposits", ls, re.I):
                in_deposit = True
                in_withdrawal = False
                continue

            if re.match(r"other withdrawals|checks presented", ls, re.I):
                in_deposit = False
                in_withdrawal = True
                continue

            if not (in_deposit or in_withdrawal):
                continue

            # ── Transaction line matching ──────────────────────────────────
            m = _TEXT_LINE.match(ls)
            if not m:
                continue

            date_str = f"{m.group(1)} {m.group(2)} {year}"
            tx_date  = parse_date(date_str, year_hint=year)
            if not tx_date:
                continue

            desc = m.group("desc").strip() if m.group("desc") else "Transaction"

            # Reconstruct the full amount string (with trailing - if present)
            amt_digits = m.group("amount")           # e.g. "9.99" or "40,000.00"
            amt_raw    = amt_digits + (m.group("neg") or "")  # e.g. "9.99-"
            amount     = parse_amount(amt_raw)
            if amount is None:
                continue

            # Ensure withdrawals are negative (belt-and-suspenders: the
            # trailing '-' already makes parse_amount return a negative value,
            # but apply section-based flip as a fallback).
            if in_withdrawal and amount > 0:
                amount = -amount

            try:
                txns.append(Transaction(date=tx_date, description=desc, amount=amount))
            except Exception as e:
                self.warn(f"US Bank line error: {e}")

        return txns

    def _year_hint(self) -> int:
        m = re.search(r"\b(20\d{2})\b", self.full_text)
        return int(m.group(1)) if m else date.today().year
