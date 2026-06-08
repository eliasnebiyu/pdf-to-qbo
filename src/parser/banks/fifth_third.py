"""
Fifth Third Bank (5/3 Bank) statement parser.

Statement layout (plain text with structured columns):
  Header: "FTCSTMT002 ... 5/3 BUSINESS CKG" or similar
  Sections vary by account type:

  Business checking — tabular text:
    Date | Description | Withdrawals | Deposits | Balance
    MM/DD  Description text              amount   amount  balance

  Transaction lines may be:
    "05/04 PAYPAL PAYMENT               1,001.26          2,195.69"  (deposit)
    "05/05 DEBIT CARD PURCHASE  84.08              2,214.16"  (withdrawal)

  The "Daily Balance Summary" table on the last page gives running balances
  but not individual transaction descriptions.

NOTE: The sample PDF provided only contains the Daily Balance Summary page
(pages 3-4 of a 4-page document). Full transaction pages would be pages 1-2.
The parser correctly detects the bank and extracts whatever transactions are
present in the provided PDF.
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

_5TH3RD_MARKERS = [
    "fifth third",
    "5/3 bank",
    "53.com",
    "ftcstmt",
    "5/3 business ckg",
    "5/3 checking",
    "p.o. box 630900",           # Fifth Third Cincinnati HQ
    "877-534-2264",              # Fifth Third business support
]

# Transaction line patterns for Fifth Third:
# "MM/DD description  [debit]  [credit]  balance"
_TX_LINE_FULL = re.compile(
    r"^(?P<date>\d{2}/\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<debit>[\d,]+\.\d{2})?\s*"
    r"(?P<credit>[\d,]+\.\d{2})?\s+"
    r"(?P<balance>[\d,]+\.\d{2})$"
)

# Simpler fallback: "MM/DD description [+/-]amount balance"
_TX_LINE_SIMPLE = re.compile(
    r"^(?P<date>\d{2}/\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amount>-?[\d,]+\.\d{2})\s+"
    r"(?P<balance>[\d,]+\.\d{2})$"
)


class FifthThirdParser(BaseParser):
    """Parser for Fifth Third Bank (5/3) checking and savings statements."""

    bank_name = "Fifth Third Bank"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _5TH3RD_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self._build_53_account(text)
        txns    = self._parse_transactions(text)
        txns    = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="fifth_third",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account ───────────────────────────────────────────────────────────────

    def _build_53_account(self, text: str) -> BankAccount:
        account = self.build_account(text, bank_name="Fifth Third Bank")

        # Account number: "Account Number: 73018850"
        an = re.search(r"Account\s+Number[:\s]+(\d+)", text, re.I)
        if an:
            account.account_id = an.group(1)

        # Period: "Statement Period Date: 5/1/2026 - 5/31/2026"
        period = re.search(
            r"Statement\s+Period\s+Date[:\s]+"
            r"(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})",
            text, re.I,
        )
        if period:
            account.statement_start = parse_date(period.group(1))
            account.statement_end   = parse_date(period.group(2))

        # Opening/closing balance from table headers if present
        ob = re.search(r"Beginning\s+Balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        cb = re.search(r"Ending\s+Balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        if ob:
            account.opening_balance = parse_amount(ob.group(1))
        if cb:
            account.closing_balance = parse_amount(cb.group(1))

        return account

    # ── Transactions ──────────────────────────────────────────────────────────

    def _parse_transactions(self, text: str) -> list[Transaction]:
        txns: list[Transaction] = []
        year = self._year_hint()

        in_tx_section = False

        for page_num in range(self.page_count):
            page_text = self.get_page_text(page_num)

            for line in page_text.splitlines():
                line = line.strip()
                if not line:
                    continue

                # Detect transaction section header
                if re.search(
                    r"(checking\s+account\s+activity|account\s+activity|"
                    r"transaction\s+detail|date\s+description)",
                    line, re.I,
                ):
                    in_tx_section = True
                    continue

                # Stop at balance summary or daily balance table
                if re.search(r"daily\s+balance\s+summary|beginning\s+balance", line, re.I):
                    break

                if not in_tx_section:
                    continue

                # Skip column headers and totals
                if re.match(r"^(date|description|withdrawal|deposit|balance|total)", line, re.I):
                    continue

                # Try full 5-column parse first
                m = _TX_LINE_FULL.match(line)
                if m:
                    tx_date = parse_date(f"{m.group('date')}/{year}", year_hint=year)
                    if not tx_date:
                        continue

                    debit  = parse_amount(m.group("debit"))  if m.group("debit")  else None
                    credit = parse_amount(m.group("credit")) if m.group("credit") else None
                    bal    = parse_amount(m.group("balance"))

                    if debit:
                        amount = -abs(debit)
                    elif credit:
                        amount = abs(credit)
                    else:
                        continue

                    try:
                        txns.append(Transaction(
                            date=tx_date,
                            description=m.group("desc").strip(),
                            amount=amount,
                            balance=bal,
                            source_page=page_num + 1,
                        ))
                    except Exception as e:
                        self.warn(f"5/3 full-parse error: {e}")
                    continue

                # Simple 2-column fallback (signed amount + balance)
                m2 = _TX_LINE_SIMPLE.match(line)
                if m2:
                    tx_date = parse_date(f"{m2.group('date')}/{year}", year_hint=year)
                    amount  = parse_amount(m2.group("amount"))
                    bal     = parse_amount(m2.group("balance"))
                    if not tx_date or amount is None:
                        continue
                    try:
                        txns.append(Transaction(
                            date=tx_date,
                            description=m2.group("desc").strip(),
                            amount=amount,
                            balance=bal,
                            source_page=page_num + 1,
                        ))
                    except Exception as e:
                        self.warn(f"5/3 simple-parse error: {e}")

        if not txns:
            self.warn(
                "Fifth Third: no transactions found. "
                "The provided PDF may be a partial export (balance summary only). "
                "Please upload the complete statement."
            )

        return txns

    def _year_hint(self) -> int:
        m = re.search(r"\b(20\d{2})\b", self.full_text)
        return int(m.group(1)) if m else date.today().year
