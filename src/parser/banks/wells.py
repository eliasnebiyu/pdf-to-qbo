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
    "wellsfargo.com",
    "wells fargo bank, n.a.",
    "wells fargo bank na",
    "wells fargo advisors",
    "1-800-to-wells",
    "wellsfargobank",
]

# Secondary markers — require ≥2 hits to avoid false-positives
# when a transaction description merely mentions Wells Fargo
_WF_SECONDARY = [
    "wells fargo",
    "wf bank",
]


class WellsFargoParser(BaseParser):
    bank_name = "Wells Fargo"

    def can_parse(self) -> bool:
        text_lower = self.full_text.lower()
        # Any strong marker → confirmed
        if any(m in text_lower for m in _WF_MARKERS):
            return True
        # Weak marker needs additional corroborating evidence
        hits = sum(1 for m in _WF_SECONDARY if m in text_lower)
        return hits >= 2

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self._build_wf_account(text)
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

    def _build_wf_account(self, text: str):
        """Build account with Wells Fargo-specific balance patterns."""
        from src.models import BankAccount
        account = self.build_account(text, bank_name="Wells Fargo")
        # If standard patterns didn't find balances, try WF-specific format:
        # "Balance on MM/DD $N,NNN.NN"
        if account.opening_balance is None or account.closing_balance is None:
            bal_pat = re.compile(
                r"Balance\s+on\s+\d{2}/\d{2}\s+\$?([\d,]+\.\d{2})", re.I
            )
            balances = [parse_amount(m.group(1)) for m in bal_pat.finditer(text)]
            balances = [b for b in balances if b is not None]
            if balances and account.opening_balance is None:
                account.opening_balance = balances[0]
            if len(balances) >= 2 and account.closing_balance is None:
                account.closing_balance = balances[-1]
        return account

    def _extract_transactions(self) -> list[Transaction]:
        txns = []
        year = date.today().year
        ym   = re.search(r"\b(20\d{2})\b", self.full_text)
        if ym:
            year = int(ym.group(1))

        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                parsed = self._parse_wf_table(table, year)
                for tx in parsed:
                    tx.source_page = page_num + 1
                txns.extend(parsed)

        if not txns:
            self.warn("Wells Fargo: no table transactions found, trying line parse.")
            txns = self._from_lines(self.full_text, year)

        if not txns:
            self.warn("Wells Fargo: trying sectioned layout parse.")
            txns = self._parse_sectioned_layout(self.full_text, year)

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

    def _parse_sectioned_layout(self, text: str, year: int) -> list[Transaction]:
        """
        Parse the 2002-era Wells Fargo sectioned layout:
          - Deposits and interest section: lines begin MM/DD, amount may be on same
            line or the continuation line (multi-line transactions).
          - Checks section: two-column NNNN MM/DD AMOUNT  NNNN MM/DD AMOUNT
          - Other withdrawals section: same multi-line pattern as deposits.

        Multi-line example (deposits / other withdrawals):
          04/30 The Insurance Co.     ← no amount on this line
          Monthly Deposit 40.76       ← continuation; amount is at end
          04/04 Lake Tahoe Resorts    ← same pattern
          Smith $433.60
        Single-line example:
          04/29 Deposit $31.20
          05/03 Interest Payment 0.32
        """
        from decimal import Decimal
        txns: list[Transaction] = []

        # Patterns
        # A line starting with MM/DD (possibly followed by text and/or an amount)
        date_line   = re.compile(r"^(\d{2}/\d{2})\s+(.*)")
        # Amount at end of any line (with or without leading $)
        amt_at_end  = re.compile(r"\$?([\d,]+\.\d{2})\s*$")
        # Two-column check line: NNNN[*] MM/DD AMOUNT  [NNNN[*] MM/DD AMOUNT]
        # The * suffix indicates a gap in check sequence (Wells Fargo convention).
        check_pat   = re.compile(
            r"(\d{3,6})\*?\s+(\w*\d{2}/\d{2})\s+([\d,]+\.\d{2})"
        )
        # Section header patterns — match the *exact* section headers
        # "Deposits and interest" appears in the activity summary too (with amount),
        # so only treat it as a header when it has no trailing digits.
        deposits_hdr = re.compile(r"^deposits and interest\s*$", re.I)
        checks_hdr   = re.compile(r"^Checks\s*$", re.I)
        other_wd_hdr = re.compile(r"^Other withdrawals", re.I)
        total_pat    = re.compile(r"^Total\b", re.I)
        daily_bal    = re.compile(r"daily balance", re.I)

        SECTION_NONE     = 0
        SECTION_DEPOSITS = 1
        SECTION_CHECKS   = 2
        SECTION_OTHER_WD = 3

        section = SECTION_NONE
        lines   = text.splitlines()

        # Pending transaction state
        pending_date   = None
        pending_desc   = None
        pending_sign   = None   # +1 or -1
        pending_amount = None   # Decimal or None

        def flush_pending():
            nonlocal pending_date, pending_desc, pending_sign, pending_amount
            if pending_date is not None and pending_amount is not None:
                amt = pending_sign * abs(pending_amount)
                try:
                    txns.append(Transaction(
                        date=pending_date,
                        description=(pending_desc or "").strip(),
                        amount=amt,
                    ))
                except Exception as e:
                    self.warn(f"WF sectioned row error: {e}")
            pending_date = pending_desc = pending_sign = pending_amount = None

        def try_extract_amount(s: str):
            """Extract trailing amount from string, return (desc_part, amount)."""
            m = amt_at_end.search(s)
            if m:
                amt = parse_amount(m.group(1))
                desc = s[:m.start()].strip()
                return desc, amt
            return s, None

        for line in lines:
            stripped = line.strip()

            # Skip dot-leaders, separators, and blank lines
            if not stripped or re.match(r"^[.\-\s]+$", stripped):
                continue

            # --- Section transition detection ---
            if deposits_hdr.match(stripped):
                flush_pending()
                section = SECTION_DEPOSITS
                continue
            if checks_hdr.match(stripped):
                flush_pending()
                section = SECTION_CHECKS
                continue
            if other_wd_hdr.match(stripped):
                flush_pending()
                section = SECTION_OTHER_WD
                continue
            if total_pat.match(stripped) or daily_bal.search(stripped):
                flush_pending()
                section = SECTION_NONE
                continue

            # Skip column header rows
            if re.match(r"^(Date\s+Description|Number\s+Date)", stripped, re.I):
                continue

            # ---- DEPOSITS ----
            if section == SECTION_DEPOSITS:
                dm = date_line.match(stripped)
                if dm:
                    flush_pending()
                    raw_date = dm.group(1)
                    rest     = dm.group(2).strip()
                    parsed_d = parse_date(f"{raw_date}/{year}", year_hint=year)
                    if parsed_d:
                        desc_part, amt = try_extract_amount(rest)
                        pending_date   = parsed_d
                        pending_desc   = desc_part
                        pending_sign   = 1
                        pending_amount = amt
                else:
                    # Continuation line: may contain the description suffix + amount
                    if pending_date is not None:
                        desc_part, amt = try_extract_amount(stripped)
                        if pending_amount is None and amt is not None:
                            # Amount found on continuation — close the transaction
                            if desc_part:
                                pending_desc = (
                                    (pending_desc or "") + " " + desc_part
                                ).strip()
                            pending_amount = amt
                        else:
                            # Pure text continuation (no amount yet)
                            if not re.search(r"[\d]", stripped):
                                pending_desc = (
                                    (pending_desc or "") + " " + stripped
                                ).strip()

            # ---- CHECKS (two-column) ----
            elif section == SECTION_CHECKS:
                for cm in check_pat.finditer(stripped):
                    raw_date_str   = cm.group(2)
                    raw_date_clean = re.sub(r"^[A-Za-z]+", "", raw_date_str)
                    parsed_d = parse_date(f"{raw_date_clean}/{year}", year_hint=year)
                    if not parsed_d:
                        continue
                    amt = parse_amount(cm.group(3))
                    if amt is None:
                        continue
                    try:
                        txns.append(Transaction(
                            date=parsed_d,
                            description=f"Check {cm.group(1)}",
                            amount=-abs(amt),
                        ))
                    except Exception as e:
                        self.warn(f"WF check row error: {e}")

            # ---- OTHER WITHDRAWALS ----
            elif section == SECTION_OTHER_WD:
                dm = date_line.match(stripped)
                if dm:
                    flush_pending()
                    raw_date = dm.group(1)
                    rest     = dm.group(2).strip()
                    parsed_d = parse_date(f"{raw_date}/{year}", year_hint=year)
                    if parsed_d:
                        desc_part, amt = try_extract_amount(rest)
                        pending_date   = parsed_d
                        pending_desc   = desc_part
                        pending_sign   = -1
                        pending_amount = amt
                else:
                    # Continuation line
                    if pending_date is not None:
                        desc_part, amt = try_extract_amount(stripped)
                        if pending_amount is None and amt is not None:
                            if desc_part:
                                pending_desc = (
                                    (pending_desc or "") + " " + desc_part
                                ).strip()
                            pending_amount = amt
                        else:
                            if not re.search(r"[\d]", stripped):
                                pending_desc = (
                                    (pending_desc or "") + " " + stripped
                                ).strip()

        flush_pending()
        return txns