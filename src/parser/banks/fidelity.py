"""
Fidelity statement parser.

Handles:
  - Fidelity Cash Management Account (CMA)
  - Fidelity brokerage with cash/money-market activity

Cash Management / bank-like layout:
  Date | Description | Deposits/Credits | Withdrawals/Debits | Balance

Brokerage activity layout:
  Date | Description | Amount | Shares | Price | Balance

We focus on cash transactions only (skip share columns).
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_MARKERS = [
    "fidelity",
    "fidelity.com",
    "fidelity investments",
    "fidelity brokerage",
    "fidelity management",
    "national financial services",
    "fidelity cash management",
]

# Avoid matching "fidelity" that appears in boilerplate of other banks' statements.
# Require at least one strong anchor.
_STRONG_MARKERS = [
    "fidelity.com",
    "fidelity investments",
    "fidelity brokerage",
    "national financial services",
    "fidelity cash management",
]

_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)


class FidelityParser(BaseParser):
    bank_name = "Fidelity"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        # Require at least one strong marker to avoid false positives
        return any(m in text_lower for m in _STRONG_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self.build_account(text, bank_name="Fidelity")

        txns = self._from_tables() or self._from_lines(text)
        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="fidelity",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    def _from_tables(self) -> list[Transaction]:
        txns = []
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                for tx in self._parse_table(table):
                    tx.source_page = page_num + 1
                    txns.append(tx)
        return txns

    def _parse_table(self, table: list[list[str]]) -> list[Transaction]:
        if not table:
            return []
        year = _year_from(self.full_text)
        txns = []
        for row in table:
            if not row or len(row) < 3:
                continue
            date_raw = (row[0] or "").strip()
            if not re.match(r"\d{1,2}/\d{1,2}", date_raw):
                continue
            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue
            desc = (row[1] or "").strip()
            if not desc:
                continue

            # 5-col cash layout: Date, Desc, Credits, Debits, Balance
            if len(row) >= 5:
                credit  = parse_amount(row[2]) if row[2] else None
                debit   = parse_amount(row[3]) if row[3] else None
                balance = parse_amount(row[4]) if row[4] else None
                if credit and not debit:
                    amount = abs(credit)
                elif debit and not credit:
                    amount = -abs(debit)
                elif credit:
                    amount = abs(credit)
                elif debit:
                    amount = -abs(debit)
                else:
                    continue
            else:
                amount  = parse_amount(row[2]) if row[2] else None
                balance = parse_amount(row[3]) if len(row) > 3 and row[3] else None
                if amount is None:
                    continue
                # Skip rows that look like share-quantity fields (no decimal .xx pattern)
                if not re.search(r"\.\d{2}$", (row[2] or "").strip()):
                    continue

            try:
                txns.append(Transaction(date=parsed_date, description=desc,
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Fidelity table row error: {e}")
        return txns

    def _from_lines(self, text: str) -> list[Transaction]:
        year  = _year_from(text)
        txns: list[Transaction] = []
        for line in text.splitlines():
            m = _LINE.match(line.strip())
            if not m:
                continue
            parsed_date = parse_date(m.group("date"), year_hint=year)
            if not parsed_date:
                continue
            amount  = parse_amount(m.group("amount"))
            balance = parse_amount(m.group("balance")) if m.group("balance") else None
            if amount is None:
                continue
            try:
                txns.append(Transaction(date=parsed_date,
                                        description=m.group("desc").strip(),
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Fidelity line error: {e}")
        return txns


def _year_from(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    return int(m.group(1)) if m else date.today().year
