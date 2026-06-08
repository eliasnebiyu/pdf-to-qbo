"""
Kemba Credit Union statement parser.

Statement layout (plain text, no formal tables):
  Date         Withdrawal  Deposit  Balance  Transaction Description
  MM/DD/YYYY   [amount]   [balance]  Description text
  [continuation line(s)]

- First line: "MM/DD/YYYY balance Beginning Balance"  (opening balance)
- Regular:    "MM/DD/YYYY signed_amount balance description"
  where signed_amount is negative for withdrawals and positive for deposits.
- Multi-line descriptions: continuation lines follow immediately and do not
  start with a date.
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

_KEMBA_MARKERS = [
    "kemba credit union",
    "kemba.com",
    "5600 chappell crossing",
]

# A line that starts a new transaction: MM/DD/YYYY amount [balance] description
_TX_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<first>-?[\d,]+\.\d{2})"          # first number (amount OR balance)
    r"(?:\s+(?P<second>[\d,]+\.\d{2}))?"   # optional second number (balance)
    r"(?:\s+(?P<desc>.+))?$"
)

# Opening balance line: MM/DD/YYYY balance Beginning Balance
_OB_LINE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+([\d,]+\.\d{2})\s+Beginning Balance",
    re.I,
)


class KembaParser(BaseParser):
    """Parser for Kemba Credit Union checking/savings statements."""

    bank_name = "Kemba Credit Union"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _KEMBA_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self._build_kemba_account(text)
        txns    = self._parse_transactions(text)
        txns    = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="kemba",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account extraction ────────────────────────────────────────────────────

    def _build_kemba_account(self, text: str) -> BankAccount:
        account = self.build_account(text, bank_name="Kemba Credit Union")

        # Kemba date range format: "MM/DD/YYYY thru MM/DD/YYYY"
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s+thru\s+(\d{2}/\d{2}/\d{4})", text, re.I)
        if m:
            account.statement_start = parse_date(m.group(1))
            account.statement_end   = parse_date(m.group(2))

        # Account number
        an = re.search(r"Account\s+Number[:\s]+(\d+)", text, re.I)
        if an:
            account.account_id = an.group(1)

        # Opening balance from "Beginning Balance" line
        ob = _OB_LINE.search(text)
        if ob:
            account.opening_balance = parse_amount(ob.group(2))

        return account

    # ── Transaction extraction ────────────────────────────────────────────────

    def _parse_transactions(self, text: str) -> list[Transaction]:
        txns: list[Transaction] = []
        year = date.today().year
        y_m = re.search(r"\b(20\d{2})\b", text)
        if y_m:
            year = int(y_m.group(1))

        in_tx_section = False
        pending: Optional[dict] = None   # buffered transaction awaiting flush

        for page_num in range(self.page_count):
            page_text = self.get_page_text(page_num)
            for raw_line in page_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Section header detection
                if re.search(r"Date\s+Withdrawal\s+Deposit\s+Balance", line, re.I):
                    in_tx_section = True
                    continue

                if not in_tx_section:
                    # Try to auto-detect: any line starting with full date
                    if re.match(r"\d{2}/\d{2}/\d{4}", line):
                        in_tx_section = True
                    else:
                        continue

                # Beginning Balance → skip as transaction, just note the balance
                if _OB_LINE.match(line):
                    if pending:
                        txns.append(self._flush(pending, page_num + 1))
                        pending = None
                    continue

                # Stop at account summary/fee sections — everything after here is metadata
                if re.match(
                    r"(Ending\s+Balance|Year\s+To\s+Date|Total\s+For\s+This\s+Period"
                    r"|Fee\s+Summary|Total\s+Overdraft|Savings|Certificates|Loans)",
                    line, re.I,
                ):
                    if pending:
                        txns.append(self._flush(pending, page_num + 1))
                        pending = None
                    in_tx_section = False
                    continue

                m = _TX_LINE.match(line)
                if m:
                    # Flush previous pending
                    if pending:
                        txns.append(self._flush(pending, page_num + 1))

                    first  = parse_amount(m.group("first"))
                    second = parse_amount(m.group("second")) if m.group("second") else None
                    desc   = (m.group("desc") or "").strip()
                    tx_date = parse_date(m.group("date"), year_hint=year)

                    if tx_date is None or first is None:
                        pending = None
                        continue

                    if second is not None:
                        # Two numbers: first=amount, second=balance
                        amount  = first
                        balance = second
                    else:
                        # Only one number — treat as balance-only (e.g. divider rows)
                        # Skip it unless description hints it's a real transaction
                        if not desc or re.match(r"beginning\s+balance", desc, re.I):
                            pending = None
                            continue
                        amount  = first
                        balance = None

                    pending = {
                        "date":    tx_date,
                        "desc":    desc,
                        "amount":  amount,
                        "balance": balance,
                    }

                else:
                    # Continuation line — append to current description
                    if pending and line:
                        # Skip lines that are just fee/totals markers
                        if not re.match(r"(Total|Ending|Beginning)", line, re.I):
                            pending["desc"] = f"{pending['desc']} {line}".strip()

        # Flush the last pending transaction
        if pending:
            txns.append(self._flush(pending, self.page_count))

        return txns

    @staticmethod
    def _flush(p: dict, source_page: int) -> Transaction:
        return Transaction(
            date=p["date"],
            description=p["desc"] or "Transaction",
            amount=p["amount"],
            balance=p["balance"],
            source_page=source_page,
        )
