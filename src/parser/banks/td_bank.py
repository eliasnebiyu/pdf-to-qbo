"""
TD Bank statement parser.

Handles TD Bank checking, savings, and money market accounts.

Typical layout (5-column):
  Date | Description | Debit (-) | Credit (+) | Balance

Alternate layout (3-column):
  Date | Description | Amount | Balance
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_MARKERS = [
    "td bank",
    "tdbank.com",
    "td everyday checking",
    "td convenience checking",
    "td simple savings",
    "toronto-dominion",
    "td complete checking",
]

# Fallback: "01/15/2024  AMAZON.COM              -50.00  4,500.00"
_TD_LINE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4})"
    r"\s+"
    r"(?P<desc>.+?)"
    r"\s{2,}"
    r"(?P<amount>-?[\d,]+\.\d{2})"
    r"(?:\s+(?P<balance>-?[\d,]+\.\d{2}))?$"
)


class TDBankParser(BaseParser):
    bank_name = "TD Bank"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self._build_td_account(text)

        txns = self._from_tables() or self._from_lines(text)
        if not txns:
            self.warn("TD Bank: trying sectioned layout parse.")
            txns = self._parse_sectioned_layout(text)
        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="td_bank",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    def _build_td_account(self, text: str):
        """Build account with TD Bank-specific balance patterns."""
        from src.models import BankAccount
        from src.utils.amount_parser import extract_date_range
        start, end = extract_date_range(text)
        # extract_date_range only handles 4-digit years; fall back to 2-digit
        if start is None:
            yr = _td_statement_year(text)
            dates_found = []
            for m in re.finditer(r"\b(\d{1,2}/\d{1,2}/\d{2})\b", text):
                d = parse_date(m.group(1), year_hint=yr)
                if d:
                    dates_found.append(d)
            dates_found = sorted(set(dates_found))
            if len(dates_found) >= 2:
                start, end = dates_found[0], dates_found[-1]
            elif len(dates_found) == 1:
                start = dates_found[0]
        acct_id    = self.extract_account_id(text)

        open_bal = close_bal = None
        # TD Bank uses "Previous Statement Balance" and "Current Statement Balance"
        # The amounts may be separated by dot-leaders: "Balance as of … 300.00"
        ob = re.search(
            r"previous\s+statement\s+balance\b[\s\w/\.…]*?([\d,]+\.\d{2})",
            text, re.I,
        )
        cb = re.search(
            r"current\s+statement\s+balance\b[\s\w/\.…]*?([\d,]+\.\d{2})",
            text, re.I,
        )
        # Also try generic opening/closing patterns as fallback
        if not ob:
            ob = re.search(r"opening\s+balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        if not cb:
            cb = re.search(r"(?:closing|ending)\s+balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)

        if ob:
            open_bal = parse_amount(ob.group(1))
        if cb:
            close_bal = parse_amount(cb.group(1))

        return BankAccount(
            bank_name="TD Bank",
            account_id=acct_id,
            statement_start=start,
            statement_end=end,
            opening_balance=open_bal,
            closing_balance=close_bal,
        )

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
            if len(row) >= 5:
                debit   = parse_amount(row[2]) if row[2] else None
                credit  = parse_amount(row[3]) if row[3] else None
                balance = parse_amount(row[4]) if row[4] else None
                if debit:
                    amount = -abs(debit)
                elif credit:
                    amount = abs(credit)
                else:
                    continue
            # 4-col: Date, Desc, Amount, Balance
            elif len(row) == 4:
                amount  = parse_amount(row[2]) if row[2] else None
                balance = parse_amount(row[3]) if row[3] else None
                if amount is None:
                    continue
            # 3-col: Date, Desc, Amount
            else:
                amount  = parse_amount(row[2]) if row[2] else None
                balance = None
                if amount is None:
                    continue

            try:
                txns.append(Transaction(date=parsed_date, description=desc,
                                        amount=amount, balance=balance))
            except Exception as e:
                self.warn(f"TD Bank table row error: {e}")
        return txns

    def _from_lines(self, text: str) -> list[Transaction]:
        year  = _year_from(text)
        txns: list[Transaction] = []
        for line in text.splitlines():
            m = _TD_LINE.match(line.strip())
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
                self.warn(f"TD Bank line error: {e}")
        return txns


    def _parse_sectioned_layout(self, text: str) -> list[Transaction]:
        """
        Parse TD Bank sample-statement layout across two section types:

        Section 1 — ***Checking Transactions*** (checks/debits)
          Serial  Date  Amount
          101     10/25  17.75
          (Serial is the check number; treated as a debit)

        Section 2 — ***Checking Account Transactions***
          Date  Description  Debits  Credits
          10/17  POS Debit 10/15   63.00
          Sneaker World                       ← continuation line
          10/21  Deposit               43.25
          ...
          (continuation lines have no leading date and are appended to
           the previous transaction's description)
        """
        year  = _td_statement_year(text)
        txns: list[Transaction] = []

        checks_hdr  = re.compile(r"\*+Checking Transactions\*+", re.I)
        acct_hdr    = re.compile(r"\*+Checking Account Transactions\*+", re.I)
        balance_hdr = re.compile(r"\*+Balance by Date\*+", re.I)
        end_markers = re.compile(
            r"(\*+Balance by Date\*+|TD Bank Financial Education|"
            r"Statement Date|Tracy Transaction|555 Deposit Lane|Cherry Hill)",
            re.I,
        )

        # Patterns
        serial_row  = re.compile(r"^(\d{3,6})\s+(\d{1,2}/\d{1,2})\s+([\d,]+\.\d{2})\s*$")
        # Transaction row: date, description text, optional debit, optional credit
        # Amounts are right-aligned so they appear at the end; the description may be
        # anything in between.
        tx_row_date = re.compile(r"^(\d{1,2}/\d{1,2})\s+(.+)$")
        amount_only = re.compile(r"^([\d,]+\.\d{2})\s*$")

        SECTION_NONE   = 0
        SECTION_CHECKS = 1
        SECTION_ACCT   = 2

        section = SECTION_NONE
        lines   = text.splitlines()

        # State for current pending transaction in SECTION_ACCT
        pend_date  = None
        pend_desc  = None
        pend_debit = None
        pend_credit= None

        def flush():
            nonlocal pend_date, pend_desc, pend_debit, pend_credit
            if pend_date is None:
                return
            if pend_debit is not None:
                amount = -abs(pend_debit)
            elif pend_credit is not None:
                amount = abs(pend_credit)
            else:
                pend_date = pend_desc = pend_debit = pend_credit = None
                return
            try:
                txns.append(Transaction(
                    date=pend_date,
                    description=(pend_desc or "").strip(),
                    amount=amount,
                ))
            except Exception as e:
                self.warn(f"TD Bank sectioned row error: {e}")
            pend_date = pend_desc = pend_debit = pend_credit = None

        def parse_tx_line(line: str):
            """
            Parse a line from ***Checking Account Transactions***.
            Format: DATE DESCRIPTION [DEBIT] [CREDIT]
            The debit/credit column appears as a bare number at line end.
            Returns (date, description, debit_or_none, credit_or_none).
            We infer debit vs credit based on column position heuristic:
            if only one amount, we rely on the description keyword.
            """
            m = tx_row_date.match(line)
            if not m:
                return None
            raw_date = m.group(1)
            rest     = m.group(2).strip()

            # Find all numbers at the end (one or two amounts)
            nums = re.findall(r"([\d,]+\.\d{2})", rest)
            # Strip trailing amounts from description
            desc_part = re.sub(r"\s*([\d,]+\.\d{2})\s*$", "", rest).strip()
            # Remove a second trailing amount if present
            desc_part = re.sub(r"\s*([\d,]+\.\d{2})\s*$", "", desc_part).strip()

            d = parse_date(f"{raw_date}/{year}", year_hint=year)
            if not d:
                return None

            if len(nums) == 2:
                debit  = parse_amount(nums[0])
                credit = parse_amount(nums[1])
            elif len(nums) == 1:
                val = parse_amount(nums[0])
                # Heuristic: deposit/credit keywords → credit; otherwise debit
                desc_lower = desc_part.lower()
                if any(w in desc_lower for w in ("deposit", "credit")):
                    debit, credit = None, val
                else:
                    debit, credit = val, None
            else:
                debit = credit = None

            return d, desc_part, debit, credit

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Section transitions
            if checks_hdr.search(stripped):
                flush()
                section = SECTION_CHECKS
                continue
            if acct_hdr.search(stripped):
                flush()
                section = SECTION_ACCT
                continue
            if balance_hdr.search(stripped) or end_markers.search(stripped):
                flush()
                section = SECTION_NONE
                continue

            # Skip header rows
            if re.match(r"^(Serial\s+Date|Date\s+Description)", stripped, re.I):
                continue

            if section == SECTION_CHECKS:
                m = serial_row.match(stripped)
                if m:
                    serial   = m.group(1)
                    raw_date = m.group(2)
                    amt_str  = m.group(3)
                    d = parse_date(f"{raw_date}/{year}", year_hint=year)
                    if d:
                        amt = parse_amount(amt_str)
                        if amt is not None:
                            try:
                                txns.append(Transaction(
                                    date=d,
                                    description=f"Check {serial}",
                                    amount=-abs(amt),
                                ))
                            except Exception as e:
                                self.warn(f"TD Bank check row error: {e}")

            elif section == SECTION_ACCT:
                result = parse_tx_line(stripped)
                if result:
                    flush()
                    pend_date, pend_desc, pend_debit, pend_credit = result
                else:
                    # Continuation line — append to description of pending tx
                    if pend_date is not None:
                        # Only append pure-text continuations (no bare amounts)
                        if not amount_only.match(stripped):
                            # Strip any trailing amounts from continuation
                            cont = re.sub(r"\s*([\d,]+\.\d{2})\s*$", "", stripped).strip()
                            if cont:
                                pend_desc = (pend_desc or "") + " " + cont

        flush()
        return txns


def _year_from(text: str) -> int:
    m = re.search(r"\b(20\d{2})\b", text)
    # Also handle 2-digit years like 11/15/18
    if not m:
        m2 = re.search(r"\b(\d{1,2}/\d{1,2}/(\d{2}))\b", text)
        if m2:
            yy = int(m2.group(2))
            return 2000 + yy if yy <= 99 else yy
    return int(m.group(1)) if m else date.today().year


def _td_statement_year(text: str) -> int:
    """
    Extract the statement year from TD Bank text.

    Prefers the "Statement Date MM/DD/YY" pattern to avoid picking up
    incidental 4-digit years (like publication years in educational documents).
    Falls back to the first 4-digit year, then the first 2-digit year date.
    """
    # Try "Statement Date MM/DD/YY[YY]"
    m = re.search(r"Statement\s+Date\s+\d{1,2}/\d{1,2}/(\d{2,4})", text, re.I)
    if m:
        yy = int(m.group(1))
        return 2000 + yy if yy < 100 else yy
    # Try any 2-digit-year date near known TD Bank marker text
    m2 = re.search(r"\b(\d{1,2}/\d{1,2}/(\d{2}))\b", text)
    if m2:
        yy = int(m2.group(2))
        return 2000 + yy if yy <= 99 else yy
    # Fall back to first 4-digit year
    m3 = re.search(r"\b(20\d{2})\b", text)
    return int(m3.group(1)) if m3 else date.today().year
