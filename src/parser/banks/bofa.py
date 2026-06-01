"""
Bank of America statement parser.
BofA uses a consistent layout with a transaction table per page.
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean


_BOFA_MARKERS = [
    "bank of america",
    "bankofamerica.com",
    "bofa",
    "merrill lynch",
]

_BOFA_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{2,4})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)


class BofAParser(BaseParser):
    bank_name = "Bank of America"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(marker in text_lower for marker in _BOFA_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self.build_account(text, bank_name="Bank of America")
        txns    = self._extract_transactions(text)

        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="bofa",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    def _extract_transactions(self, text: str) -> list[Transaction]:
        # Tables first
        txns = []
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                parsed = self._parse_bofa_table(table)
                for tx in parsed:
                    tx.source_page = page_num + 1
                txns.extend(parsed)
        if txns:
            return txns

        # Line fallback
        return self._from_lines(text)

    def _parse_bofa_table(self, table: list[list[str]]) -> list[Transaction]:
        if not table:
            return []

        txns = []
        year = date.today().year
        ym   = re.search(r"\b(20\d{2})\b", self.full_text)
        if ym:
            year = int(ym.group(1))

        # Skip header rows (cells contain letters/words not dates)
        for row in table:
            if not row or len(row) < 2:
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

            # BofA typical: [Date, Description, Amount, Running Balance]
            amount  = parse_amount(row[2]) if len(row) > 2 and row[2] else None
            balance = parse_amount(row[3]) if len(row) > 3 and row[3] else None

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
                self.warn(f"BofA row error: {e}")

        return txns

    def _from_lines(self, text: str) -> list[Transaction]:
        txns = []
        year = date.today().year
        ym   = re.search(r"\b(20\d{2})\b", text)
        if ym:
            year = int(ym.group(1))

        for line in text.splitlines():
            m = _BOFA_LINE.match(line.strip())
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
                txns.append(Transaction(
                    date=parsed_date,
                    description=m.group("desc").strip(),
                    amount=amount,
                    balance=balance,
                ))
            except Exception as e:
                self.warn(f"BofA line error: {e}")

        return txns