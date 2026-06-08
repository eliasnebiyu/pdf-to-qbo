"""
PNC Bank statement parser.

Handles two distinct statement types:

1. PNC Business/Personal Checking
   Layout: section-based plain text, no formal tables.
   Sections: Deposits, ATM Deposits, ACH Additions, Other Additions,
             Checks, Debit Card Purchases, POS Purchases, ACH Deductions,
             Other Deductions.
   Transaction line: "MM/DD  amount  description  [refnum]"

2. PNC Credit Card
   Layout: plain text with a single transaction list.
   Header:  "TRANS DATE  POST DATE  REFERENCE NUMBER  DESCRIPTION  AMOUNT"
   Line:    "MM/DD  MM/DD  REFNUM  description  $amount[-]"
   Amount:  "$22,996.60-" = credit/payment (positive OFX)
            "$95.98"      = charge (negative OFX)
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from src.models import ParsedStatement, Transaction, BankAccount, AccountType
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_PNC_MARKERS = [
    "pnc bank",
    "pnc.com",
    "pnc.com/mybusiness",
    "pnc.com/accountview",
    "pnc business checking",
    "pnc checking",
]

# ── Checking transaction line ──────────────────────────────────────────────────
# "05/06 4,500.00 Mobile Deposit 072365988"
# "05/01 3,317.00 ACH Credit Cashout Venmo XXXXXXXXX3990 00026120913187655"
_CHK_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2})\s+"
    r"(?P<amount>[\d,]+\.\d{2})\s+"
    r"(?P<desc>.+?)(?:\s+\d{8,20})?$"
)

# Check line in the Checks section
# "05/01 5058 * 1,660.50 016389426"  or "05/01 5058 1,660.50 016389426"
_CHECK_LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2})\s+"
    r"(?P<num>\d+)\s*\*?\s+"
    r"(?P<amount>[\d,]+\.\d{2})"
)

# ── Credit card transaction line ───────────────────────────────────────────────
# "05/27 05/27 7443603H301MMYBN8 THANK YOU FOR YOUR PMT $22,996.60-"
# "04/30 05/02 2431605G9MQQ4SWMN SHELL OIL CINCINNATI OH $95.98"
_CC_LINE = re.compile(
    r"^(?P<trans>\d{2}/\d{2})\s+"
    r"(?P<post>\d{2}/\d{2})\s+"
    r"(?P<ref>[A-Z0-9]{8,})\s+"
    r"(?P<desc>.+?)\s+"
    r"\$(?P<amount>[\d,]+\.\d{2})(?P<credit>-)?$"
)

# Sections that represent ADDITIONS (positive) for checking
_ADDITION_SECTIONS = re.compile(
    r"(deposits?\s+and\s+other\s+additions?|deposits?"
    r"|atm\s+deposits?\s+and\s+additions?"
    r"|ach\s+additions?"
    r"|other\s+additions?)",
    re.I,
)

# Sections that represent DEDUCTIONS (negative) for checking
_DEDUCTION_SECTIONS = re.compile(
    r"(checks?\s+and\s+other\s+deductions?"
    r"|checks?\s+and\s+substitute\s+checks?"
    r"|debit\s+card\s+purchases?"
    r"|pos\s+purchases?"
    r"|atm/misc.*debit.*transactions?"
    r"|ach\s+deductions?"
    r"|other\s+deductions?)",
    re.I,
)

# Lines to skip within transaction sections
_SKIP_LINE = re.compile(
    r"^(date\s+(posted|transaction|check)|"
    r"for\s+24|business\s+checking|primary\s+account|page\s+\d|"
    r"activity\s+detail|mcc:\s*\d|merchant\s+zip|"
    r"total\s+\$|continued\s+on|account\s+number|"
    r"zel\s+to\s+[a-z])",
    re.I,
)


class PNCParser(BaseParser):
    """Parser for PNC Bank checking and credit card statements."""

    bank_name = "PNC Bank"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _PNC_MARKERS)

    def _is_credit_card(self, text: str) -> bool:
        text_lower = text.lower()
        cc_signals = [
            "your transactions",
            "trans date",
            "minimum payment",
            "new balance",
            "credit limit",
            "pnc.com/accountview",
            "cash rewards",
        ]
        return sum(1 for s in cc_signals if s in text_lower) >= 2

    def extract(self) -> ParsedStatement:
        text  = self.full_text
        is_cc = self._is_credit_card(text)

        account = self._build_pnc_account(text, is_cc)
        if is_cc:
            account.account_type = AccountType.CREDIT

        txns = self._extract_cc(text) if is_cc else self._extract_checking()
        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="pnc_cc" if is_cc else "pnc_chk",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account ───────────────────────────────────────────────────────────────

    def _build_pnc_account(self, text: str, is_cc: bool) -> BankAccount:
        account = self.build_account(text, bank_name="PNC Bank")

        # Period for checking: "For the Period MM/DD/YYYY to MM/DD/YYYY"
        period = re.search(
            r"For\s+the\s+Period\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})",
            text, re.I,
        )
        if period:
            account.statement_start = parse_date(period.group(1))
            account.statement_end   = parse_date(period.group(2))

        # Period for CC: "Statement closing date MM/DD/YY"
        cc_close = re.search(r"Statement\s+closing\s+date\s+(\d{2}/\d{2}/\d{2})", text, re.I)
        if cc_close and is_cc:
            account.statement_end = parse_date(cc_close.group(1))

        # Balance for checking
        bal = re.search(
            r"Beginning\s+.*?balance\s+([\d,]+\.\d{2})", text, re.I
        )
        if bal:
            account.opening_balance = parse_amount(bal.group(1))

        end_bal = re.search(r"Ending\s+.*?balance\s+([\d,]+\.\d{2})", text, re.I)
        if end_bal:
            account.closing_balance = parse_amount(end_bal.group(1))

        # CC balances
        if is_cc:
            prev = re.search(r"Previous\s+balance\s+\$?([\d,]+\.\d{2})", text, re.I)
            new  = re.search(r"New\s+balance\s+\$?([\d,]+\.\d{2})", text, re.I)
            if prev:
                account.opening_balance = parse_amount(prev.group(1))
            if new:
                account.closing_balance = parse_amount(new.group(1))

        return account

    # ── Checking extraction ───────────────────────────────────────────────────

    def _extract_checking(self) -> list[Transaction]:
        txns: list[Transaction] = []
        year = self._year_hint()

        in_activity_detail = False   # gate: only parse after "Activity Detail"
        is_addition = False
        is_deduction = False
        is_check_section = False

        for page_num in range(self.page_count):
            page_text = self.get_page_text(page_num)
            pending: Optional[dict] = None   # multi-line description buffer

            for raw_line in page_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Gate: require "Activity Detail" before capturing any transactions
                if re.match(r"^Activity\s+Detail", line, re.I):
                    in_activity_detail = True
                    continue

                # Stop at service charge detail / daily balance sections
                if re.match(r"^(Daily\s+Balance|Detail\s+of\s+Services|Balance\s+Summary)", line, re.I):
                    if pending:
                        txns.append(_make_tx(pending, page_num + 1))
                        pending = None
                    is_addition = is_deduction = False
                    continue

                if not in_activity_detail:
                    continue

                # Section transitions
                if _ADDITION_SECTIONS.match(line):
                    # Flush pending if switching sections
                    if pending:
                        txns.append(_make_tx(pending, page_num + 1))
                        pending = None
                    is_addition   = True
                    is_deduction  = False
                    is_check_section = False
                    continue

                if _DEDUCTION_SECTIONS.match(line):
                    if pending:
                        txns.append(_make_tx(pending, page_num + 1))
                        pending = None
                    is_check_section = bool(re.match(r"checks?", line, re.I))
                    is_addition   = False
                    is_deduction  = True
                    continue

                if not (is_addition or is_deduction):
                    continue

                if _SKIP_LINE.match(line):
                    continue

                # Check line within the Checks section
                if is_check_section:
                    cm = _CHECK_LINE.match(line)
                    if cm:
                        if pending:
                            txns.append(_make_tx(pending, page_num + 1))
                            pending = None
                        tx_date = parse_date(f"{cm.group('date')}/{year}", year_hint=year)
                        amount  = parse_amount(cm.group("amount"))
                        if tx_date and amount:
                            txns.append(Transaction(
                                date=tx_date,
                                description=f"Check #{cm.group('num')}",
                                amount=-abs(amount),
                                source_page=page_num + 1,
                            ))
                    continue

                # Normal transaction line
                m = _CHK_LINE.match(line)
                if m:
                    if pending:
                        txns.append(_make_tx(pending, page_num + 1))
                    tx_date = parse_date(f"{m.group('date')}/{year}", year_hint=year)
                    amount  = parse_amount(m.group("amount"))
                    if tx_date is None or amount is None:
                        pending = None
                        continue
                    if is_deduction:
                        amount = -abs(amount)
                    pending = {
                        "date":    tx_date,
                        "desc":    m.group("desc").strip(),
                        "amount":  amount,
                        "balance": None,
                    }
                elif pending and not _SKIP_LINE.match(line):
                    # Continuation description line (e.g. "Paychex Inc. Wgsmhj87...")
                    if re.match(r"[A-Za-z]", line):
                        pending["desc"] += f" {line}"

            if pending:
                txns.append(_make_tx(pending, page_num + 1))
                pending = None

        return txns

    # ── Credit card extraction ────────────────────────────────────────────────

    def _extract_cc(self, text: str) -> list[Transaction]:
        txns: list[Transaction] = []
        year = self._year_hint()
        in_tx = False

        for line in text.splitlines():
            line = line.strip()

            if re.search(r"your\s+transactions", line, re.I):
                in_tx = True
                continue
            if re.search(r"your\s+finance\s+charges|important\s+information", line, re.I):
                in_tx = False
                continue

            if not in_tx:
                continue

            # Skip MCC / merchant zip info lines
            if re.match(r"MCC:\s*\d|MERCHANT\s+ZIP", line, re.I):
                continue

            m = _CC_LINE.match(line)
            if not m:
                continue

            tx_date = parse_date(f"{m.group('trans')}/{year}", year_hint=year)
            if not tx_date:
                continue

            amount = parse_amount(m.group("amount"))
            if amount is None:
                continue

            # "$amount-" = credit/payment → positive OFX
            # "$amount"  = charge         → negative OFX
            if m.group("credit") == "-":
                amount = abs(amount)   # payment/credit → positive
            else:
                amount = -abs(amount)  # charge → negative

            desc = m.group("desc").strip()
            try:
                txns.append(Transaction(
                    date=tx_date,
                    description=desc,
                    amount=amount,
                ))
            except Exception as e:
                self.warn(f"PNC CC parse error: {e}")

        return txns

    def _year_hint(self) -> int:
        m = re.search(r"\b(20\d{2})\b", self.full_text)
        return int(m.group(1)) if m else date.today().year


def _make_tx(p: dict, source_page: int) -> Transaction:
    return Transaction(
        date=p["date"],
        description=p["desc"] or "Transaction",
        amount=p["amount"],
        balance=p.get("balance"),
        source_page=source_page,
    )
