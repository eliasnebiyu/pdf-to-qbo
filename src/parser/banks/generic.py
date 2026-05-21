"""
Generic parser — handles standard bank statement layouts that don't
match a known bank-specific parser. Uses pdfplumber table extraction
with intelligent column detection.
"""
from __future__ import annotations
import re
from datetime import date
from decimal import Decimal
from typing import Optional

import pdfplumber

from src.models import ParsedStatement, Transaction, TransactionType
from src.parser.base import BaseParser
from src.utils.amount_parser import (
    parse_amount, parse_date, is_debit_column, is_credit_column,
)
from src.utils.dedup import clean


# Column header patterns
_DATE_HEADERS    = re.compile(r"date|posted|trans(?:action)?", re.I)
_DESC_HEADERS    = re.compile(r"desc(?:ription)?|detail|memo|narr(?:ative)?|particulars", re.I)
_AMOUNT_HEADERS  = re.compile(r"amount|amt|debit|credit|withdrawal|deposit", re.I)
_BALANCE_HEADERS = re.compile(r"balance|bal|running", re.I)

# Fallback: line-by-line regex for common single-column layouts
_TX_LINE = re.compile(
    r"^(?P<date>\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)"    # date
    r"\s+"
    r"(?P<desc>.+?)"                                          # description
    r"\s{2,}"                                                 # gap
    r"(?P<amount>[\-\+]?\$?[\d,]+\.\d{2})"                  # amount
    r"(?:\s+(?P<balance>[\-\+]?\$?[\d,]+\.\d{2}))?$",       # optional balance
    re.X,
)


class GenericParser(BaseParser):
    """
    Fallback parser for any bank statement PDF.
    Tries table extraction first; falls back to line-by-line text parsing.
    """
    bank_name = "Generic Bank"

    def can_parse(self) -> bool:
        # Generic parser always accepts — it's the last resort
        return True

    def extract(self) -> ParsedStatement:
        text     = self.full_text
        account  = self.build_account(text)
        txns: list[Transaction] = []

        # Strategy 1: table-based extraction (most reliable)
        table_txns = self._extract_from_tables()
        if table_txns:
            txns = table_txns
        else:
            # Strategy 2: line-by-line text parsing
            self.warn("No tables found — falling back to line-by-line parsing.")
            txns = self._extract_from_lines(text)

        if not txns:
            self.warn("No transactions extracted. PDF may be scanned/image-based.")

        txns = clean(txns, warn=self.warn)

        statement = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="generic",
            warnings=self.warnings,
        )
        statement.assign_fit_ids()
        return statement

    # ── Table extraction ──────────────────────────────────────────────────────

    def _extract_from_tables(self) -> list[Transaction]:
        """Loop through every page and every table, collect all transactions."""
        all_txns: list[Transaction] = []
        year_hint = date.today().year

        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                if not table:
                    continue
                txns = self._parse_table(table, year_hint)
                all_txns.extend(txns)

        return all_txns

    def _parse_table(
        self, table: list[list[str]], year_hint: int
    ) -> list[Transaction]:
        """
        Analyse a table's headers to determine column roles,
        then parse each row into a Transaction.
        """
        if len(table) < 2:
            return []

        # Detect header row — first row that has recognisable column names
        header_row, start_row = self._detect_header(table)
        if header_row is None:
            return []

        col_date    = self._find_col(header_row, _DATE_HEADERS)
        col_desc    = self._find_col(header_row, _DESC_HEADERS)
        col_amount  = self._find_col(header_row, _AMOUNT_HEADERS)
        col_balance = self._find_col(header_row, _BALANCE_HEADERS)

        # Separate debit/credit columns if amount column not found
        col_debit  = None
        col_credit = None
        if col_amount is None:
            for i, h in enumerate(header_row):
                if is_debit_column(h):
                    col_debit = i
                elif is_credit_column(h):
                    col_credit = i

        if col_date is None or col_desc is None:
            return []  # can't parse without at least date + description

        txns: list[Transaction] = []
        for row in table[start_row:]:
            if len(row) <= max(
                col_date, col_desc,
                col_amount or 0, col_balance or 0
            ):
                continue

            raw_date = row[col_date].strip()
            raw_desc = row[col_desc].strip()
            if not raw_date or not raw_desc:
                continue  # skip blank/subtotal rows

            # Parse date
            parsed_date = parse_date(raw_date, year_hint=year_hint)
            if not parsed_date:
                continue

            # Parse amount
            amount: Optional[Decimal] = None
            if col_amount is not None:
                amount = parse_amount(row[col_amount])
            elif col_debit is not None or col_credit is not None:
                debit = parse_amount(row[col_debit]) if col_debit is not None and row[col_debit] else None
                credit = parse_amount(row[col_credit]) if col_credit is not None and row[col_credit] else None
                if debit:
                    amount = -abs(debit)
                elif credit:
                    amount = abs(credit)

            if amount is None:
                continue

            # Parse optional balance
            balance: Optional[Decimal] = None
            if col_balance is not None and col_balance < len(row):
                balance = parse_amount(row[col_balance])

            try:
                tx = Transaction(
                    date=parsed_date,
                    description=raw_desc,
                    amount=amount,
                    balance=balance,
                )
                txns.append(tx)
            except Exception as e:
                self.warn(f"Skipped row {row}: {e}")

        return txns

    def _detect_header(
        self, table: list[list[str]]
    ) -> tuple[Optional[list[str]], int]:
        """
        Find the header row by looking for cells that match known column names.
        Returns (header_row, next_row_index).
        """
        for i, row in enumerate(table[:5]):  # header usually in first 5 rows
            hits = sum(
                1 for cell in row
                if cell and (
                    _DATE_HEADERS.search(cell)
                    or _DESC_HEADERS.search(cell)
                    or _AMOUNT_HEADERS.search(cell)
                )
            )
            if hits >= 2:
                return row, i + 1
        return None, 0

    @staticmethod
    def _find_col(headers: list[str], pattern: re.Pattern) -> Optional[int]:
        for i, h in enumerate(headers):
            if h and pattern.search(h):
                return i
        return None

    # ── Line-by-line fallback ─────────────────────────────────────────────────

    def _extract_from_lines(self, text: str) -> list[Transaction]:
        """Parse transactions from plain text lines using regex."""
        txns:      list[Transaction] = []
        year_hint: int = date.today().year

        for line in text.splitlines():
            line = line.strip()
            m    = _TX_LINE.match(line)
            if not m:
                continue

            parsed_date = parse_date(m.group("date"), year_hint=year_hint)
            if not parsed_date:
                continue

            amount = parse_amount(m.group("amount"))
            if amount is None:
                continue

            balance_raw = m.group("balance")
            balance     = parse_amount(balance_raw) if balance_raw else None

            try:
                tx = Transaction(
                    date=parsed_date,
                    description=m.group("desc").strip(),
                    amount=amount,
                    balance=balance,
                )
                txns.append(tx)
            except Exception as e:
                self.warn(f"Line parse error — '{line}': {e}")

        return txns