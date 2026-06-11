"""
Navy Federal Credit Union statement parser.

Handles checking, savings, and credit card accounts.

Checking/Savings typical layout (5-column):
  Date | Description | Withdrawals | Deposits | Balance

Credit card layout (4-column):
  Date | Transaction Details | Amount | Balance

Date formats used: MM/DD/YYYY and MM/DD/YY
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction, AccountType
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_MARKERS = [
    "navy federal",
    "navy federal credit union",
    "navyfederal.org",
    "nfcu",
    "navy federal cu",
]

_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)

_CC_SIGNALS = [
    "credit limit", "minimum payment", "purchase apr",
    "cash advance", "new balance", "statement balance",
]


class NavyFederalParser(BaseParser):
    bank_name = "Navy Federal Credit Union"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _MARKERS)

    def _is_credit_card(self, text: str) -> bool:
        return sum(1 for s in _CC_SIGNALS if s in text.lower()) >= 2

    def extract(self) -> ParsedStatement:
        text  = self.full_text
        is_cc = self._is_credit_card(text)

        account = self.build_account(text, bank_name="Navy Federal Credit Union")
        if is_cc:
            account.account_type = AccountType.CREDIT

        txns = self._from_tables(is_cc) or self._from_lines(text, is_cc)
        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="navy_federal_cc" if is_cc else "navy_federal",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    def _from_tables(self, is_cc: bool) -> list[Transaction]:
        txns = []
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                for tx in self._parse_table(table, is_cc):
                    tx.source_page = page_num + 1
                    txns.append(tx)
        return txns

    def _parse_table(self, table: list[list[str]], is_cc: bool) -> list[Transaction]:
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

            # 5-col: Date, Desc, Withdrawals, Deposits, Balance
            if len(row) >= 5:
                withdrawal = parse_amount(row[2]) if row[2] else None
                deposit    = parse_amount(row[3]) if row[3] else None
                balance    = parse_amount(row[4]) if row[4] else None
                if withdrawal:
                    amount = -abs(withdrawal)
                elif deposit:
                    amount = abs(deposit)
                else:
                    continue
            else:
                amount  = parse_amount(row[2]) if row[2] else None
                balance = parse_amount(row[3]) if len(row) > 3 and row[3] else None
                if amount is None:
                    continue
                if is_cc and amount > 0:
                    amount = -amount

            try:
                txns.append(Transaction(date=parsed_date, description=desc,
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Navy Federal table row error: {e}")
        return txns

    def _from_lines(self, text: str, is_cc: bool) -> list[Transaction]:
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
            if is_cc and amount > 0:
                amount = -amount
            try:
                txns.append(Transaction(date=parsed_date,
                                        description=m.group("desc").strip(),
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Navy Federal line error: {e}")
        return txns


def _year_from(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    return int(m.group(1)) if m else date.today().year
