"""
Truist Bank statement parser.

Truist was formed from the 2019 merger of BB&T and SunTrust Banks.
Statements may still carry SunTrust or BB&T branding on older accounts.

Typical layout:
  Date | Description | Amount | Balance

Debit / credit split layout (some products):
  Date | Description | Debit | Credit | Balance
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_MARKERS = [
    "truist",
    "truistbank.com",
    "truist bank",
    "truist financial",
    # Legacy brands
    "bb&t",
    "bbt.com",
    "branch banking",
    "suntrust",
    "suntrust bank",
    "suntrust.com",
]

_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)


class TruistParser(BaseParser):
    bank_name = "Truist Bank"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self.build_account(text, bank_name="Truist Bank")

        txns = self._from_tables() or self._from_lines(text)
        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="truist",
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

            # 5-col: Date, Desc, Debit, Credit, Balance
            if len(row) >= 5 and (row[2] or row[3]):
                debit   = parse_amount(row[2]) if row[2] else None
                credit  = parse_amount(row[3]) if row[3] else None
                balance = parse_amount(row[4]) if len(row) > 4 and row[4] else None
                if debit:
                    amount = -abs(debit)
                elif credit:
                    amount = abs(credit)
                else:
                    continue
            else:
                amount  = parse_amount(row[2]) if row[2] else None
                balance = parse_amount(row[3]) if len(row) > 3 and row[3] else None
                if amount is None:
                    continue

            try:
                txns.append(Transaction(date=parsed_date, description=desc,
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Truist table row error: {e}")
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
                self.warn(f"Truist line error: {e}")
        return txns


def _year_from(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    return int(m.group(1)) if m else date.today().year
