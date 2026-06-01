"""
Core data models for the PDF-to-QBO converter.
All financial data flows through these models to ensure consistency.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, field_validator


class TransactionType(str, Enum):
    DEBIT  = "DEBIT"
    CREDIT = "CREDIT"
    INT    = "INT"      # interest
    DIV    = "DIV"      # dividend
    FEE    = "FEE"
    OTHER  = "OTHER"


class AccountType(str, Enum):
    CHECKING = "CHECKING"
    SAVINGS  = "SAVINGS"
    CREDIT   = "CREDITLINE"
    MONEY    = "MONEYMRKT"


class Transaction(BaseModel):
    """A single bank transaction parsed from a PDF statement."""
    date:        date
    description: str
    amount:      Decimal          # negative = debit, positive = credit
    balance:     Optional[Decimal] = None
    tx_type:     TransactionType  = TransactionType.OTHER
    fit_id:      Optional[str]    = None  # unique ID for OFX deduplication
    memo:        Optional[str]    = None
    check_num:   Optional[str]    = None
    source_page: Optional[int]   = None  # 1-indexed PDF page this tx was found on

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v):
        if isinstance(v, str):
            v = v.replace(",", "").replace("$", "").strip()
            if v.startswith("(") and v.endswith(")"):
                v = "-" + v[1:-1]
        return Decimal(str(v))

    @field_validator("description")
    @classmethod
    def clean_description(cls, v):
        return " ".join(v.split())  # collapse whitespace

    def infer_type(self) -> TransactionType:
        """Infer transaction type from amount and description."""
        desc_upper = self.description.upper()
        if "INTEREST" in desc_upper:
            return TransactionType.INT
        if "DIVIDEND" in desc_upper:
            return TransactionType.DIV
        if any(w in desc_upper for w in ("FEE", "CHARGE", "PENALTY")):
            return TransactionType.FEE
        return TransactionType.CREDIT if self.amount >= 0 else TransactionType.DEBIT

    def generate_fit_id(self, index: int) -> str:
        """Generate a stable unique ID for OFX FITID field."""
        date_str = self.date.strftime("%Y%m%d")
        amt_str  = str(abs(self.amount)).replace(".", "")
        return f"{date_str}-{amt_str}-{index:04d}"

    class Config:
        use_enum_values = True


class BankAccount(BaseModel):
    """Account metadata extracted from the statement."""
    bank_name:    str            = "Unknown Bank"
    account_id:   str            = ""          # last 4 digits or full
    routing_id:   str            = ""
    account_type: AccountType    = AccountType.CHECKING
    currency:     str            = "USD"
    statement_start: Optional[date] = None
    statement_end:   Optional[date] = None
    opening_balance: Optional[Decimal] = None
    closing_balance: Optional[Decimal] = None


class ParsedStatement(BaseModel):
    """Complete parsed bank statement — the central data structure."""
    account:      BankAccount
    transactions: list[Transaction]
    raw_page_count: int          = 0
    parser_used:    str          = "generic"
    warnings:       list[str]    = []

    @property
    def total_debits(self) -> Decimal:
        return sum(abs(t.amount) for t in self.transactions if t.amount < 0)

    @property
    def total_credits(self) -> Decimal:
        return sum(t.amount for t in self.transactions if t.amount >= 0)

    @property
    def transaction_count(self) -> int:
        return len(self.transactions)

    def assign_fit_ids(self):
        """Assign unique FITID to every transaction (required by OFX spec)."""
        seen: dict[str, int] = {}
        for i, tx in enumerate(self.transactions):
            if tx.fit_id is None:
                base = tx.generate_fit_id(i)
                count = seen.get(base, 0)
                seen[base] = count + 1
                tx.fit_id = f"{base}-{count}" if count else base
                tx.tx_type = tx.infer_type()
