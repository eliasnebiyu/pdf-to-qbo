"""
Chase bank statement parser.
Handles Chase's standard PDF layout: Date | Description | Amount | Balance
"""
from __future__ import annotations
import re
from datetime import date
from decimal import Decimal
from typing import Optional

from src.models import ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

# Chase header identifiers
_CHASE_MARKERS = [
    "jpmorgan chase",
    "chase bank",
    "chase.com",
    "chase sapphire",
    "chase freedom",
    "chase total checking",
    "jp morgan",
]

# Chase transaction line pattern (text-mode fallback)
# Example: "01/15 AMAZON.COM*1234567  -123.45   4,567.89"
_CHASE_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2})"               # MM/DD
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)


class ChaseParser(BaseParser):
    """Parser for Chase bank checking, savings, and credit card statements."""

    bank_name = "JPMorgan Chase"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(marker in text_lower for marker in _CHASE_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self.build_account(text, bank_name="JPMorgan Chase")
        txns    = self._extract_transactions(text)

        txns = clean(txns, warn=self.warn)

        statement = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="chase",
            warnings=self.warnings,
        )
        statement.assign_fit_ids()
        return statement

    def _extract_transactions(self, text: str) -> list[Transaction]:
        """
        Chase statements have a clear transaction section.
        First attempt table extraction; fall back to line matching.
        """
        # Try table extraction across all pages
        txns = self._from_tables()
        if txns:
            return txns

        # Fallback: find the transaction section and parse lines
        return self._from_lines(text)

    def _from_tables(self) -> list[Transaction]:
        txns: list[Transaction] = []
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                parsed = self._parse_chase_table(table)
                txns.extend(parsed)
        return txns

    def _parse_chase_table(self, table: list[list[str]]) -> list[Transaction]:
        if not table:
            return []

        # Chase tables: [Date, Description, Amount, Balance]
        # or:           [Date, Description, Withdrawals, Deposits, Balance]
        txns: list[Transaction] = []

        # Detect if this looks like a transaction table
        has_date = any(
            re.search(r"\d{2}/\d{2}", cell)
            for row in table[:3] for cell in row
        )
        if not has_date:
            return []

        year = date.today().year
        # Try to extract statement year from text
        year_match = re.search(r"\b(20\d{2})\b", self.full_text)
        if year_match:
            year = int(year_match.group(1))

        for row in table:
            if not row or len(row) < 3:
                continue

            # Detect date in first cell
            date_raw = row[0].strip() if row[0] else ""
            if not re.match(r"\d{1,2}/\d{1,2}", date_raw):
                continue

            # Append year if missing
            if not re.search(r"\d{4}", date_raw):
                date_raw = f"{date_raw}/{year}"

            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue

            desc = row[1].strip() if len(row) > 1 else ""
            if not desc:
                continue

            # 3-col layout: Date, Desc, Amount[, Balance]
            if len(row) == 3 or len(row) == 4:
                amount  = parse_amount(row[2]) if row[2] else None
                balance = parse_amount(row[3]) if len(row) == 4 and row[3] else None
            # 5-col layout: Date, Desc, Withdrawals, Deposits, Balance
            elif len(row) == 5:
                debit   = parse_amount(row[2]) if row[2] else None
                credit  = parse_amount(row[3]) if row[3] else None
                balance = parse_amount(row[4]) if row[4] else None
                if debit:
                    amount = -abs(debit)
                elif credit:
                    amount = abs(credit)
                else:
                    continue
            else:
                continue

            if amount is None:
                continue

            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=desc,
                    amount=amount,
                    balance=balance,
                ))
            except Exception as e:
                self.warn(f"Chase row parse error: {e}")

        return txns

    def _from_lines(self, text: str) -> list[Transaction]:
        """Line-by-line parsing for Chase statement text."""
        txns: list[Transaction] = []

        year = date.today().year
        year_m = re.search(r"\b(20\d{2})\b", text)
        if year_m:
            year = int(year_m.group(1))

        in_tx_section = False
        for line in text.splitlines():
            line = line.strip()

            # Chase uses section headers like "ACCOUNT ACTIVITY"
            if re.search(r"account\s+activity|transaction\s+detail", line, re.I):
                in_tx_section = True
                continue

            if not in_tx_section:
                continue

            m = _CHASE_LINE.match(line)
            if not m:
                continue

            date_raw    = f"{m.group('date')}/{year}"
            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue

            amount  = parse_amount(m.group("amount"))
            balance = parse_amount(m.group("balance")) if m.group("balance") else None

            if amount is None:
                continue

            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=m.group("desc").strip(),
                    amount=amount,
                    balance=balance,
                ))
            except Exception as e:
                self.warn(f"Chase line parse error: {e}")

        return txns