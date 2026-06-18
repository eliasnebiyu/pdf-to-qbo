"""
Fifth Third Bank (5/3 Bank) statement parser.

Two known statement layouts are handled:

1. Consumer / "Green By Nature" (retail checking) layout
   Section headers on separate lines:
     "Withdrawals / Debits"   →  MM/DD   amount   description
     "Deposits / Credits"     →  MM/DD   amount   description
     "Checks"                 →  check_no   MM/DD   amount

2. Business checking layout
   Section header: "Checking Account Activity" / "Account Activity"
   Transaction line: MM/DD   description   [debit]   [credit]   balance
                  or MM/DD   description   signed_amount   balance

Strategy order:
  1. pdfplumber table extraction (works when the PDF has detectable table borders)
  2. Section-aware line parsing   (consumer layout — most reliable for retail PDFs)
  3. Business-layout line parsing  (original logic kept as final fallback)
  4. pypdf blob parsing           (fallback when pdfplumber misses pages entirely;
                                   pdfminer/pdfplumber is known to skip pages 1-2 of
                                   certain Fifth Third consumer PDFs while pypdf reads
                                   all pages correctly)
"""
from __future__ import annotations

import re
from datetime import date

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
    "p.o. box 630900",          # Fifth Third Cincinnati HQ
    "877-534-2264",             # Fifth Third business support
]

# ── Transaction line: MM/DD  amount  description  (consumer layout) ───────────
# Matches lines where the second token is a dollar amount (no leading sign).
_TX_WD = re.compile(
    r"^(?P<date>\d{2}/\d{2})\s+"
    r"\$?(?P<amount>[\d,]+\.\d{2})\s+"
    r"(?P<desc>.+)$"
)

# ── Check entry: check_no [type_flag]  MM/DD  amount
# Fifth Third puts an optional single-letter type flag after the check number
# (i = electronic image, e = electronic, s = substitute).
# Multiple checks can appear per line in a 3-up column layout, so we use
# findall instead of match.
_CHECK_ENTRY = re.compile(
    r"(?P<check_num>\d{3,})\s*[a-z*]?\s+"
    r"(?P<date>\d{2}/\d{2}(?:/\d{2,4})?)\s+"
    r"\$?(?P<amount>[\d,]+\.\d{2})"
)

# ── pypdf blob patterns ───────────────────────────────────────────────────────
# In pypdf-extracted text from certain Fifth Third PDFs, transactions inside a
# section are concatenated with no line breaks:
#   "MM/DD amountDESCRIPTION  MM/DD amountDESCRIPTION  …"
# (2-space gap between transactions; amount runs directly into description).
_PYPDF_TX = re.compile(
    r"(\d{2}/\d{2})\s+([\d,]+\.\d{2})(.+?)(?=\d{2}/\d{2}\s+[\d,]+\.\d{2}|\Z)",
    re.DOTALL,
)

# Check entries in pypdf format: "NNNNflag?MM/DDamount" (no spaces around date/amount).
# Checks with a gap indicator (*) and image flag (i) appear as "NNNN*iMM/DD…"
# so we allow up to 3 flag characters instead of the usual 1.
_PYPDF_CHECK = re.compile(
    r"(\d{3,})\s*[a-z*]{0,3}\s*(\d{2}/\d{2})([\d,]+\.\d{2})"
)


def _compact(s: str) -> str:
    """
    Remove ALL whitespace and lowercase — used for section-header detection.

    Fifth Third's PDF font inserts spaces between individual characters in
    section headers, so "Withdrawals / Debits" may arrive from pdfplumber as
    "W it h d r a w a ls / D e b it s".  Stripping every space before
    comparing lets a single check work for both normal and spaced renderings.
    """
    return re.sub(r"\s+", "", s).lower()

# ── Business layout: MM/DD  description  [debit]  [credit]  balance ──────────
_TX_LINE_FULL = re.compile(
    r"^(?P<date>\d{2}/\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<debit>[\d,]+\.\d{2})?\s*"
    r"(?P<credit>[\d,]+\.\d{2})?\s+"
    r"(?P<balance>[\d,]+\.\d{2})$"
)

# Simpler fallback: MM/DD  description  signed_amount  balance
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

        # Strategy 1: pdfplumber table extraction
        txns = self._from_tables()

        # Strategy 2: consumer section-aware line parsing
        if not txns:
            txns = self._parse_consumer_sections(text)

        # Strategy 3: original business-layout line parsing
        if not txns:
            txns = self._parse_business_lines(text)

        # Strategy 4: pypdf fallback
        # pdfplumber/pdfminer is known to miss pages 1-2 of certain Fifth Third
        # consumer PDFs (the transaction pages).  pypdf reads all pages correctly
        # but produces a concatenated blob rather than line-per-transaction text.
        if not txns:
            txns = self._parse_pypdf_consumer(self._pypdf_text())

        txns = clean(txns, warn=self.warn)

        if not txns:
            self.warn(
                "Fifth Third: no transactions found. "
                "The PDF may be a balance-summary-only export; "
                "please upload the complete multi-page statement."
            )

        stmt = ParsedStatement(
            account=account,
            transactions=txns,
            raw_page_count=self.page_count,
            parser_used="fifth_third",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Account metadata ──────────────────────────────────────────────────────

    def _build_53_account(self, text: str) -> BankAccount:
        account = self.build_account(text, bank_name="Fifth Third Bank")

        # Account number: "Account Number: 73018850"
        an = re.search(r"Account\s+Number[:\s]+(\d+)", text, re.I)
        if an:
            raw = an.group(1)
            account.account_id = f"****{raw[-4:]}" if len(raw) > 4 else raw

        # Statement period: "Statement Period Date: 5/1/2026 - 5/31/2026"
        period = re.search(
            r"Statement\s+Period\s+Date[:\s]+"
            r"(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})",
            text, re.I,
        )
        if period:
            account.statement_start = parse_date(period.group(1))
            account.statement_end   = parse_date(period.group(2))

        # Opening / closing balance from Account Summary table.
        # pdfplumber may miss the page that contains these values (it only reads
        # the Daily Balance Summary page on some Fifth Third PDFs), so fall back
        # to a pypdf pass when they are not found in the pdfplumber text.
        ob = re.search(r"Beginning\s+Balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        cb = re.search(r"Ending\s+Balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        if not ob or not cb:
            pypdf_txt = self._pypdf_text()
            if not ob:
                ob = re.search(r"Beginning\s+Balance\$?([\d,]+\.\d{2})", pypdf_txt, re.I)
            if not cb:
                cb = re.search(r"Ending\s+Balance\$?([\d,]+\.\d{2})", pypdf_txt, re.I)
        if ob:
            account.opening_balance = parse_amount(ob.group(1))
        if cb:
            account.closing_balance = parse_amount(cb.group(1))

        return account

    # ── Strategy 1: pdfplumber table extraction ───────────────────────────────

    def _from_tables(self) -> list[Transaction]:
        txns = []
        year = self._year_hint()
        for page_num in range(self.page_count):
            for table in self.extract_tables_from_page(page_num):
                for tx in self._parse_table(table, year, page_num):
                    txns.append(tx)
        return txns

    def _parse_table(self, table: list[list[str]], year: int, page_num: int) -> list[Transaction]:
        if not table or len(table) < 2:
            return []
        txns: list[Transaction] = []

        # Infer column roles from the header row
        header = [str(c or "").lower().strip() for c in table[0]]
        date_col = desc_col = amt_col = debit_col = credit_col = bal_col = None
        for i, h in enumerate(header):
            if h in ("date", "date paid"):
                date_col = i
            elif any(x in h for x in ("description", "memo", "transaction", "payee")):
                desc_col = i
            elif any(x in h for x in ("debit", "withdrawal", "charge")):
                debit_col = i
            elif any(x in h for x in ("credit", "deposit", "payment")):
                credit_col = i
            elif "balance" in h:
                bal_col = i
            elif "amount" in h:
                amt_col = i

        if date_col is None:
            return []

        for row in table[1:]:
            if not row or len(row) <= date_col:
                continue
            date_raw = (row[date_col] or "").strip()
            if not re.match(r"\d{1,2}/\d{1,2}", date_raw):
                continue
            tx_date = parse_date(date_raw, year_hint=year)
            if not tx_date:
                continue

            amount = balance = None

            if debit_col is not None or credit_col is not None:
                debit  = parse_amount(row[debit_col])  if debit_col  is not None and debit_col  < len(row) else None
                credit = parse_amount(row[credit_col]) if credit_col is not None and credit_col < len(row) else None
                balance = parse_amount(row[bal_col]) if bal_col is not None and bal_col < len(row) else None
                if debit:
                    amount = -abs(debit)
                elif credit:
                    amount = abs(credit)
            elif amt_col is not None and amt_col < len(row):
                amount  = parse_amount(row[amt_col])
                balance = parse_amount(row[bal_col]) if bal_col is not None and bal_col < len(row) else None

            if amount is None:
                continue

            # Build description from identified desc column, else all remaining cells
            if desc_col is not None and desc_col < len(row):
                desc = (row[desc_col] or "").strip()
            else:
                desc = " ".join(
                    str(c or "").strip()
                    for i, c in enumerate(row)
                    if i != date_col and i != amt_col and i != debit_col
                    and i != credit_col and i != bal_col and (c or "").strip()
                )
            if not desc:
                continue

            try:
                txns.append(Transaction(
                    date=tx_date, description=desc,
                    amount=amount, balance=balance,
                    source_page=page_num + 1,
                ))
            except Exception as e:
                self.warn(f"5/3 table row error: {e}")

        return txns

    # ── Strategy 2: consumer section-aware line parsing ───────────────────────

    def _parse_consumer_sections(self, text: str) -> list[Transaction]:
        """
        Parse retail statements that use named sections:
          "Withdrawals / Debits"   —  MM/DD   amount   description
          "Deposits / Credits"     —  MM/DD   amount   description
          "Checks"                 —  check_no [flag]  MM/DD  amount  (multiple per row)

        Fifth Third's PDF font inserts spaces between individual characters in
        section headers, so we use _compact() (strip all whitespace + lowercase)
        for header matching rather than regex on the raw line.

        The "Account Summary" at the top lists "Beginning/Ending Balance" but
        these are NOT transaction sections — we stay in NONE until we see a
        recognised section header.  The only stop condition is "Daily Balance
        Summary" (end of statement), which is also detected via compact match.
        """
        # Quick exit: compact-check avoids processing PDFs that have no consumer sections
        compact_full = _compact(text)
        if "withdrawals" not in compact_full and "deposits" not in compact_full:
            return []

        year = self._year_hint()
        txns: list[Transaction] = []

        NONE, WITHDRAWALS, DEPOSITS, CHECKS = 0, 1, 2, 3
        section = NONE

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # ── Section header detection (compact match handles spaced fonts) ──
            # Only check short lines that don't start with a date — avoids
            # accidentally matching section keywords inside long descriptions.
            if not re.match(r"\d{2}/\d{2}", stripped) and len(stripped) < 100:
                c = _compact(stripped)

                if "withdrawals" in c and "debit" in c:
                    section = WITHDRAWALS
                    continue
                if "deposit" in c and "credit" in c:
                    section = DEPOSITS
                    continue
                if c in ("checks", "checkspaid"):
                    section = CHECKS
                    continue
                # Daily Balance Summary = end of transaction data
                if "dailybalance" in c:
                    section = NONE
                    continue

            if section == NONE:
                continue

            # ── Skip column-header and total lines ──
            if re.match(r"^(date|number|description|amount|total|balance)\b", stripped, re.I):
                continue
            c_stripped = _compact(stripped)
            if "total" in c_stripped and re.search(r"\d+\.\d{2}", stripped):
                continue
            # "(31 items totaling $4,246.41)" type lines
            if re.match(r"^\d+\s+items?\s+totaling", stripped, re.I):
                continue

            # ── Parse transaction / check lines ──
            if section in (WITHDRAWALS, DEPOSITS):
                m = _TX_WD.match(stripped)
                if m:
                    tx_date = parse_date(m.group("date"), year_hint=year)
                    if not tx_date:
                        continue
                    amount = parse_amount(m.group("amount"))
                    if amount is None:
                        continue
                    amount = -abs(amount) if section == WITHDRAWALS else abs(amount)
                    try:
                        txns.append(Transaction(
                            date=tx_date,
                            description=m.group("desc").strip(),
                            amount=amount,
                        ))
                    except Exception as e:
                        self.warn(f"5/3 consumer parse error: {e}")

            elif section == CHECKS:
                # Multiple checks can appear on one row (3-up column layout).
                # _CHECK_ENTRY uses findall to extract every check on the line.
                for m in _CHECK_ENTRY.finditer(stripped):
                    tx_date = parse_date(m.group("date"), year_hint=year)
                    if not tx_date:
                        continue
                    amount = parse_amount(m.group("amount"))
                    if amount is None:
                        continue
                    try:
                        txns.append(Transaction(
                            date=tx_date,
                            description=f"Check #{m.group('check_num')}",
                            amount=-abs(amount),
                        ))
                    except Exception as e:
                        self.warn(f"5/3 check parse error: {e}")

        return txns

    # ── Strategy 3: original business-layout line parsing ─────────────────────

    def _parse_business_lines(self, text: str) -> list[Transaction]:
        """Legacy parser for business checking with 'Account Activity' header."""
        txns: list[Transaction] = []
        year = self._year_hint()
        in_tx_section = False

        for page_num in range(self.page_count):
            page_text = self.get_page_text(page_num)
            for line in page_text.splitlines():
                line = line.strip()
                if not line:
                    continue

                if re.search(
                    r"(checking\s+account\s+activity|account\s+activity|"
                    r"transaction\s+detail|date\s+description)",
                    line, re.I,
                ):
                    in_tx_section = True
                    continue

                # Only stop at the Daily Balance Summary — not at "Beginning Balance"
                # (which appears in the Account Summary header above the transactions)
                if re.search(r"daily\s+balance\s+summary", line, re.I):
                    break

                if not in_tx_section:
                    continue

                if re.match(r"^(date|description|withdrawal|deposit|balance|total)", line, re.I):
                    continue

                # Full 5-column: Date  Desc  Debit  Credit  Balance
                m = _TX_LINE_FULL.match(line)
                if m:
                    tx_date = parse_date(m.group("date"), year_hint=year)
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

                # Simple: Date  Desc  signed_amount  Balance
                m2 = _TX_LINE_SIMPLE.match(line)
                if m2:
                    tx_date = parse_date(m2.group("date"), year_hint=year)
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

        return txns

    # ── Strategy 4: pypdf blob parsing ───────────────────────────────────────

    def _pypdf_text(self) -> str:
        """
        Extract the full text of all PDF pages using pypdf.

        pdfplumber/pdfminer is known to silently skip certain pages (e.g. the
        transaction pages in some Fifth Third consumer statements).  pypdf reads
        all pages correctly and is used as a fallback text source.
        """
        try:
            import pypdf  # lazy import — keeps the dependency soft
            reader = pypdf.PdfReader(str(self.pdf_path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception as e:
            self.warn(f"5/3 pypdf fallback failed: {e}")
            return ""

    def _parse_pypdf_consumer(self, text: str) -> list[Transaction]:
        """
        Parse the concatenated text blob produced by pypdf for Fifth Third
        consumer statements.

        pypdf extracts each page as a long single-line blob where transactions
        inside a section are separated by two spaces:

          Withdrawals/Debits NNN items…Date Amount Description
          MM/DD amountDESCRIPTION  MM/DD amountDESCRIPTION  …
          Withdrawals/Debits-continuedDate Amount Description
          MM/DD amountDESCRIPTION  …
          Deposits/Credits NNN items…Date Amount Description
          MM/DD amountDESCRIPTION  …
          Checks NNN checks totaling…Number DatePaid Amount…
          NNNN[flag]MM/DDamount NNNN[flag]MM/DDamount …
        """
        if not text:
            return []

        txns: list[Transaction] = []
        year = self._year_hint()  # pdfplumber still reads the account-summary page

        # ── helpers ──────────────────────────────────────────────────────────

        def _clean_blob(blob: str) -> str:
            """Strip page-stamp/header junk so the TX regex doesn't eat it."""
            # Remove FTCSTMT document-ID lines (e.g. "FTCSTMT002 002 20260531 …")
            blob = re.sub(r"FTCSTMT\S+.*?(?=\n|\Z)", "", blob)
            # Remove "Page N of N" markers
            blob = re.sub(r"\bPage\s+\d+\s+of\s+\d+\b", "", blob)
            # Remove "Withdrawals/Debits-continued" sub-headers
            blob = re.sub(r"Withdrawals/Debits\s*-\s*continued", "", blob, flags=re.I)
            # Remove "Date Amount Description" column headers
            blob = re.sub(r"\bDate\s+Amount\s+Description\b", "", blob, flags=re.I)
            return blob

        def _txns_from_blob(blob: str, *, debit: bool) -> list[Transaction]:
            result = []
            for m in _PYPDF_TX.finditer(blob):
                tx_date = parse_date(m.group(1), year_hint=year)
                amount  = parse_amount(m.group(2))
                desc    = m.group(3).strip()
                if tx_date and amount is not None and desc:
                    try:
                        result.append(Transaction(
                            date=tx_date,
                            description=desc,
                            amount=-abs(amount) if debit else abs(amount),
                        ))
                    except Exception as e:
                        self.warn(f"5/3 pypdf tx error: {e}")
            return result

        # ── locate section boundaries ─────────────────────────────────────────
        chk_m = re.search(r"Checks\s+\d+\s+checks",  text, re.I)
        wd_m  = re.search(r"Withdrawals/Debits\b",    text, re.I)
        dep_m = re.search(r"Deposits/Credits",         text, re.I)
        end_m = re.search(r"Daily\s*Balance",          text, re.I)

        eof     = len(text)
        end_pos = end_m.start() if end_m else eof
        dep_pos = dep_m.start() if dep_m else end_pos
        wd_pos  = wd_m.start()  if wd_m  else dep_pos
        chk_pos = chk_m.start() if chk_m else wd_pos

        # ── checks ────────────────────────────────────────────────────────────
        if chk_m:
            chk_end  = wd_pos if wd_m else dep_pos
            chk_blob = text[chk_pos:chk_end]
            for m in _PYPDF_CHECK.finditer(chk_blob):
                tx_date = parse_date(m.group(2), year_hint=year)
                amount  = parse_amount(m.group(3))
                if tx_date and amount is not None:
                    try:
                        txns.append(Transaction(
                            date=tx_date,
                            description=f"Check #{m.group(1).lstrip('0') or '0'}",
                            amount=-abs(amount),
                        ))
                    except Exception as e:
                        self.warn(f"5/3 pypdf check error: {e}")

        # ── withdrawals / debits (includes "…-continued" page-2 section) ─────
        if wd_m:
            wd_blob = _clean_blob(text[wd_pos:dep_pos])
            txns.extend(_txns_from_blob(wd_blob, debit=True))

        # ── deposits / credits ────────────────────────────────────────────────
        if dep_m:
            dep_blob = _clean_blob(text[dep_pos:end_pos])
            txns.extend(_txns_from_blob(dep_blob, debit=False))

        return txns

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _year_hint(self) -> int:
        m = re.search(r"\b(20\d{2})\b", self.full_text)
        return int(m.group(1)) if m else date.today().year
