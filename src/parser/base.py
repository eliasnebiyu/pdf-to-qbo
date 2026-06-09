"""
Abstract base class for all bank-specific PDF parsers.
Every bank parser inherits from this and implements extract().
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import pdfplumber
from src.models import ParsedStatement, BankAccount, Transaction
from src.utils.amount_parser import parse_date, extract_date_range, parse_amount


class BaseParser(ABC):
    """
    Base class for bank statement PDF parsers.

    Subclass this and implement:
      - bank_name       : str
      - can_parse()     : return True if this parser recognises the PDF
      - extract()       : return ParsedStatement

    The base class provides helpers for common extraction tasks.
    """

    bank_name: str = "Generic Bank"

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        self._pdf: Optional[pdfplumber.PDF] = None
        self.warnings: list[str] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(self):
        self._pdf = pdfplumber.open(self.pdf_path)
        return self

    def close(self):
        if self._pdf:
            self._pdf.close()
            self._pdf = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def can_parse(self) -> bool:
        """Return True if this parser can handle the given PDF."""
        ...

    @abstractmethod
    def extract(self) -> ParsedStatement:
        """Extract and return a fully parsed statement."""
        ...

    # ── Helpers available to all subclasses ───────────────────────────────────

    @property
    def full_text(self) -> str:
        """Full text of the entire PDF, concatenated across pages."""
        if not self._pdf:
            raise RuntimeError("Parser not opened. Use as context manager.")
        return "\n".join(
            page.extract_text() or "" for page in self._pdf.pages
        )

    @property
    def page_count(self) -> int:
        return len(self._pdf.pages) if self._pdf else 0

    def get_page_text(self, page_num: int) -> str:
        """Get text from a specific page (0-indexed)."""
        if not self._pdf or page_num >= len(self._pdf.pages):
            return ""
        return self._pdf.pages[page_num].extract_text() or ""

    def find_text(self, pattern: str, flags: int = re.IGNORECASE) -> Optional[re.Match]:
        """Search for a regex pattern across the full text."""
        return re.search(pattern, self.full_text, flags)

    def extract_tables_from_page(self, page_num: int) -> list[list[list[str]]]:
        """Extract all tables from a specific page."""
        if not self._pdf or page_num >= len(self._pdf.pages):
            return []
        page   = self._pdf.pages[page_num]
        tables = page.extract_tables()
        return [
            [[cell or "" for cell in row] for row in table if any(cell for cell in row)]
            for table in (tables or [])
        ]

    def extract_account_id(self, text: str) -> str:
        """
        Try to extract account number and mask it to last 4 digits.

        We always return only the last 4 digits — never a full account
        number — so the exported OFX/CSV doesn't expose sensitive data.
        """
        patterns = [
            r"account\s+(?:number|#|no\.?)[:\s]+[*x]+(\d{4})",
            r"account\s+(?:number|#|no\.?)[:\s]+(\d{4,17})",
            r"acct\.?\s+[*x]+(\d{4})",
            r"\*{3,}(\d{4})",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                raw = re.sub(r"[\s\-]", "", m.group(1))
                return f"****{raw[-4:]}" if len(raw) > 4 else raw
        return ""

    def warn(self, msg: str):
        self.warnings.append(msg)

    def build_account(
        self,
        text: str,
        bank_name: Optional[str] = None,
    ) -> BankAccount:
        """Build a BankAccount from statement text using common patterns."""
        start, end = extract_date_range(text)
        acct_id    = self.extract_account_id(text)

        # Opening/closing balance
        open_bal = close_bal = None
        ob = re.search(r"opening\s+balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        cb = re.search(r"(?:closing|ending)\s+balance[:\s]+\$?([\d,]+\.\d{2})", text, re.I)
        if ob:
            open_bal = parse_amount(ob.group(1))
        if cb:
            close_bal = parse_amount(cb.group(1))

        return BankAccount(
            bank_name=bank_name or self.bank_name,
            account_id=acct_id,
            statement_start=start,
            statement_end=end,
            opening_balance=open_bal,
            closing_balance=close_bal,
        )
