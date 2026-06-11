"""
Capital One statement parser.

Handles:
  - Capital One credit cards (Quicksilver, Venture, Savor, etc.)
  - Capital One 360 checking / savings

Credit card layout (typical):
  Transaction Date | Posted Date | Description | Category | Amount
  or simply:
  Transaction Date | Description | Amount

Checking layout:
  Date | Description | Debit | Credit | Balance

Sign convention: PDF charges are positive → we flip to negative for OFX.
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction, AccountType
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_MARKERS = [
    "capital one",
    "capitalone.com",
    "capital one 360",
    "venture card",
    "quicksilver",
    "savor card",
    "venture x",
]

# CC: "Jan 15 AMAZON.COM $123.45" or "01/15 AMAZON.COM $123.45"
_CC_LINE = re.compile(
    r"^(?P<date>[A-Za-z]{3}\s+\d{1,2}|\d{1,2}/\d{1,2})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})\s*$"
)

# Checking: full date with amount ± balance
_CHK_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)

_CC_SIGNALS = [
    "minimum payment", "credit limit", "new balance", "payment due",
    "rewards", "cash back", "purchase apr", "account activity",
    "quicksilver", "venture", "savor",
]


class CapitalOneParser(BaseParser):
    bank_name = "Capital One"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _MARKERS)

    def _is_credit_card(self, text: str) -> bool:
        hits = sum(1 for s in _CC_SIGNALS if s in text.lower())
        return hits >= 2

    def extract(self) -> ParsedStatement:
        text  = self.full_text
        is_cc = self._is_credit_card(text)

        account = self.build_account(text, bank_name="Capital One")
        if is_cc:
            account.account_type = AccountType.CREDIT

        txns = self._from_tables(is_cc) or self._from_lines(text, is_cc)
        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="capital_one_cc" if is_cc else "capital_one",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Table extraction ──────────────────────────────────────────────────────

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
            if not row or len(row) < 2:
                continue
            date_raw = (row[0] or "").strip()
            if not re.match(r"(\d{1,2}/\d{1,2}|[A-Za-z]{3}\s+\d{1,2})", date_raw):
                continue
            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue
            desc = (row[1] or "").strip()
            if not desc:
                continue

            if len(row) >= 5:
                # Could be: date, posted, desc, category, amount
                # or:       date, desc, debit, credit, balance
                raw_amount = row[-1] or row[-2] or ""
                balance    = None
            elif len(row) >= 3:
                raw_amount = row[-1] or ""
                balance    = parse_amount(row[-1]) if len(row) == 4 else None
            else:
                continue

            amount = parse_amount(raw_amount)
            if amount is None:
                continue

            # CC PDFs show charges as positive — flip to negative for OFX
            if is_cc and amount > 0:
                amount = -amount
            elif is_cc and amount < 0:
                amount = abs(amount)  # payments shown negative → positive credit

            try:
                txns.append(Transaction(date=parsed_date, description=desc,
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Capital One table row error: {e}")
        return txns

    # ── Line fallback ─────────────────────────────────────────────────────────

    def _from_lines(self, text: str, is_cc: bool) -> list[Transaction]:
        year  = _year_from(text)
        txns: list[Transaction] = []
        pattern = _CC_LINE if is_cc else _CHK_LINE
        for line in text.splitlines():
            m = pattern.match(line.strip())
            if not m:
                continue
            parsed_date = parse_date(m.group("date"), year_hint=year)
            if not parsed_date:
                continue
            amount  = parse_amount(m.group("amount"))
            balance = parse_amount(m.group("balance")) if "balance" in m.groupdict() and m.group("balance") else None
            if amount is None:
                continue
            if is_cc and amount > 0:
                amount = -amount
            elif is_cc and amount < 0:
                amount = abs(amount)
            try:
                txns.append(Transaction(date=parsed_date, description=m.group("desc").strip(),
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"Capital One line error: {e}")
        return txns


def _year_from(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    return int(m.group(1)) if m else date.today().year
