"""
Chase bank statement parser.
Handles Chase's standard PDF layout: Date | Description | Amount | Balance
Also handles Chase credit card statements: Date | Description | Amount
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

# Chase header identifiers
_CHASE_MARKERS = [
    "jpmorgan chase",
    "chase bank",
    "chase.com",
    "chase sapphire",
    "chase freedom",
    "chase total checking",
    "jp morgan",
    "chase ultimate rewards",
    "chase credit card",
]

# Chase transaction line pattern (text-mode fallback)
# Checking: "01/15 AMAZON.COM -123.45 4,567.89"
# CC:       "01/15 AMAZON.COM -123.45"
# Note: pdfplumber collapses multi-space gaps to single space, so we use \s+
_CHASE_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2})"               # MM/DD
    r"\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>[\d,]+\.\d{2}))?$"
)

# Credit card signals
_CC_SIGNALS = [
    "minimum payment",
    "credit access line",
    "new balance",
    "account activity",
    "chase ultimate rewards",
    "chase sapphire",
    "chase freedom",
    "chase slate",
    "payment due",
    "minimum due",
]


class ChaseParser(BaseParser):
    """Parser for Chase bank checking, savings, and credit card statements."""

    bank_name = "JPMorgan Chase"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(marker in text_lower for marker in _CHASE_MARKERS)

    def _is_credit_card(self, text: str) -> bool:
        """Return True if the text looks like a Chase credit card statement."""
        text_lower = text.lower()
        hits = sum(1 for s in _CC_SIGNALS if s in text_lower)
        return hits >= 2

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        is_cc   = self._is_credit_card(text)
        account = self.build_account(text, bank_name="JPMorgan Chase")
        if is_cc:
            account.account_type = AccountType.CREDIT

        # Override period with Chase-specific patterns (build_account's generic
        # extract_date_range can pick up barcodes / promo-rate dates).
        year = self._year_hint()
        if is_cc:
            # "Opening/Closing Date 04/07/26 - 05/06/26"
            m = re.search(
                r"Opening/Closing\s+Date\s+(\d{2}/\d{2}/\d{2,4})\s*[-–]\s*(\d{2}/\d{2}/\d{2,4})",
                text, re.I,
            )
            if m:
                account.statement_start = parse_date(m.group(1), year_hint=year)
                account.statement_end   = parse_date(m.group(2), year_hint=year)
        else:
            # "April 21, 2026throughMay 20, 2026"  (may have no space around "through")
            m = re.search(
                r"([A-Za-z]+\s+\d{1,2},?\s*\d{4})\s*through\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})",
                text, re.I,
            )
            if m:
                account.statement_start = parse_date(m.group(1))
                account.statement_end   = parse_date(m.group(2))

        txns = self._extract_transactions(text, is_cc=is_cc)
        txns = clean(txns, warn=self.warn)

        statement = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="chase_cc" if is_cc else "chase",
            warnings=self.warnings,
        )
        statement.assign_fit_ids()
        return statement

    def _extract_transactions(self, text: str, is_cc: bool = False) -> list[Transaction]:
        """
        Chase statements have a clear transaction section.
        First attempt table extraction; fall back to line matching.
        """
        # Try table extraction across all pages
        txns = self._from_tables()
        if txns:
            if is_cc:
                for tx in txns:
                    tx.amount = -tx.amount
            return txns

        # Fallback: find the transaction section and parse lines
        return self._from_lines(text, is_cc=is_cc)

    def _from_tables(self) -> list[Transaction]:
        txns: list[Transaction] = []
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                parsed = self._parse_chase_table(table)
                for tx in parsed:
                    tx.source_page = page_num + 1
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

    def _year_hint(self) -> int:
        """Extract statement year, avoiding phone-number false positives."""
        text = self.full_text
        # High-confidence patterns
        for pat in [
            r"charged\s+in\s+(20\d{2})\b",        # "charged in 2026"
            r"(20\d{2})\s+totals?\s+year",         # "2026 Totals Year-to-Date"
            r"fees?\s+in\s+(20\d{2})\b",           # "fees in 2026"
            r"Opening/Closing\s+Date\s+\d{2}/\d{2}/(\d{2,4})",
        ]:
            m = re.search(pat, text, re.I)
            if m:
                yr = m.group(1)
                yr = 2000 + int(yr) if len(yr) == 2 else int(yr)
                if 2015 <= yr <= 2040:
                    return yr
        # Fallback: first 4-digit year
        m = re.search(r"\b(20\d{2})\b", text)
        return int(m.group(1)) if m else date.today().year

    def _from_lines(self, text: str, is_cc: bool = False) -> list[Transaction]:
        """Line-by-line parsing for Chase statement text."""
        txns: list[Transaction] = []

        year = self._year_hint()

        in_tx_section = False
        prev_page = 1
        for line in text.splitlines():
            line = line.strip()

            # Chase uses section headers like "ACCOUNT ACTIVITY" / "TRANSACTION DETAIL"
            # Some PDFs render text with doubled characters (e.g. "AACCCCOOUUNNTT AACCTTIIVVIITTYY")
            # The doubled-char pattern uses char+ for each letter to handle both forms.
            if re.search(
                r"A+C+O+U+N+T+\s+A+C+T+I+V+I+T+Y+"
                r"|T+R+A+N+S+A+C+T+I+O+N+\s+D+E+T+A+I+L+",
                line, re.I
            ):
                in_tx_section = True
                continue

            if not in_tx_section:
                continue

            # Skip column headers and divider lines
            if re.match(r"^(date\s+description|date\s+of|beginning\s+balance)", line, re.I):
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

            # For CC statements: PDF sign is inverted vs OFX convention
            # (positive charge → negative OFX; negative payment → positive OFX)
            if is_cc:
                amount = -amount

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