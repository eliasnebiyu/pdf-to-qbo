"""
Wells Fargo statement parser.
Wells Fargo uses a two-column layout: withdrawals and deposits are
in separate columns, not a single signed amount.
"""
from __future__ import annotations
import re
from datetime import date
from src.models import ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_amount, parse_date
from src.utils.dedup import clean

_WF_MARKERS = [
    "wells fargo",
    "wellsfargo.com",
    "wf bank",
]


class WellsFargoParser(BaseParser):
    bank_name = "Wells Fargo"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        return any(m in text_lower for m in _WF_MARKERS)

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self.build_account(text, bank_name="Wells Fargo")
        txns    = self._extract_transactions()

        txns = clean(txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="wells_fargo",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    def _extract_transactions(self) -> list[Transaction]:
        txns = []
        year = date.today().year
        ym   = re.search(r"\b(20\d{2})\b", self.full_text)
        if ym:
            year = int(ym.group(1))

        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                txns.extend(self._parse_wf_table(table, year))

        if not txns:
            self.warn("Wells Fargo: no table transactions found, trying line parse.")
            txns = self._from_lines(self.full_text, year)

        return txns

    def _parse_wf_table(
        self, table: list[list[str]], year: int
    ) -> list[Transaction]:
        """
        Wells Fargo tables typically look like:
        Date | Description | Withdrawals | Deposits | Balance
        """
        if not table or len(table) < 2:
            return []

        # Identify header row
        header = None
        start  = 0
        for i, row in enumerate(table[:4]):
            joined = " ".join(c.lower() for c in row if c)
            if "withdrawal" in joined or "deposit" in joined:
                header = [c.lower() for c in row]
                start  = i + 1
                break

        if header is None:
            return []

        # Map columns
        def col(keywords):
            for idx, h in enumerate(header):
                if any(k in h for k in keywords):
                    return idx
            return None

        c_date  = col(["date"])
        c_desc  = col(["desc", "detail", "memo", "narr"])
        c_wd    = col(["withdraw", "debit"])
        c_dep   = col(["deposit", "credit"])
        c_bal   = col(["balance"])

        if c_date is None or c_desc is None:
            return []

        txns = []
        for row in table[start:]:
            if not row or len(row) <= max(
                c_date, c_desc, c_wd or 0, c_dep or 0
            ):
                continue

            date_raw = (row[c_date] or "").strip()
            if not re.match(r"\d{1,2}/\d{1,2}", date_raw):
                continue

            parsed_date = parse_date(date_raw, year_hint=year)
            if not parsed_date:
                continue

            desc = (row[c_desc] or "").strip()
            if not desc:
                continue

            wd  = parse_amount(row[c_wd])  if c_wd  and c_wd  < len(row) else None
            dep = parse_amount(row[c_dep]) if c_dep and c_dep < len(row) else None
            bal = parse_amount(row[c_bal]) if c_bal and c_bal < len(row) else None

            if wd:
                amount = -abs(wd)
            elif dep:
                amount = abs(dep)
            else:
                continue

            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=desc,
                    amount=amount,
                    balance=bal,
                ))
            except Exception as e:
                self.warn(f"WF row error: {e}")

        return txns

    def _from_lines(self, text: str, year: int) -> list[Transaction]:
        """Simple line parser fallback for Wells Fargo text mode."""
        txns = []
        pattern = re.compile(
            r"^(\d{2}/\d{2})\s+(.+?)\s{2,}"
            r"([\d,]+\.\d{2})?\s*([\d,]+\.\d{2})?\s*([\d,]+\.\d{2})?$"
        )
        for line in text.splitlines():
            m = pattern.match(line.strip())
            if not m:
                continue
            parsed_date = parse_date(f"{m.group(1)}/{year}", year_hint=year)
            if not parsed_date:
                continue
            # Heuristic: if two amounts, treat first as debit, second as balance
            a1 = parse_amount(m.group(3)) if m.group(3) else None
            a2 = parse_amount(m.group(4)) if m.group(4) else None
            if a1 is None:
                continue
            amount  = -abs(a1)
            balance = a2
            try:
                txns.append(Transaction(
                    date=parsed_date,
                    description=m.group(2).strip(),
                    amount=amount,
                    balance=balance,
                ))
            except Exception as e:
                self.warn(f"WF line error: {e}")
        return txns