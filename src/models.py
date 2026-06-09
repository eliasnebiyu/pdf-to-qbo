"""
Core data models for the PDF-to-QBO converter.
All financial data flows through these models to ensure consistency.
"""
from __future__ import annotations
import hashlib
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, field_validator


class TransactionType(str, Enum):
    DEBIT       = "DEBIT"
    CREDIT      = "CREDIT"
    INT         = "INT"         # interest earned or paid
    DIV         = "DIV"         # dividend
    FEE         = "FEE"         # financial institution fee
    CHECK       = "CHECK"       # paper check
    ATM         = "ATM"         # ATM withdrawal / deposit
    POS         = "POS"         # point-of-sale
    DIRECTDEP   = "DIRECTDEP"   # direct deposit (payroll, ACH credit)
    DIRECTDEBIT = "DIRECTDEBIT" # merchant-initiated ACH debit
    XFER        = "XFER"        # account transfer
    PAYMENT     = "PAYMENT"     # electronic bill payment
    OTHER       = "OTHER"


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
    source_page: Optional[int]    = None  # 1-indexed PDF page this tx was found on
    category:    Optional[str]    = None  # suggested QBO expense category

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
        """
        Infer OFX TRNTYPE from amount sign and description keywords.

        OFX 1.02 TRNTYPE values used here:
          CREDIT      — generic deposit / credit
          DEBIT       — generic withdrawal / debit
          INT         — interest earned or paid
          DIV         — dividend
          FEE         — financial institution fee
          CHECK       — paper check
          ATM         — ATM withdrawal or deposit
          POS         — point-of-sale debit
          DIRECTDEP   — direct deposit (payroll / ACH credit)
          DIRECTDEBIT — merchant-initiated ACH debit
          XFER        — account-to-account transfer
          PAYMENT     — electronic bill payment
        """
        desc_upper = self.description.upper()

        # Interest / dividends
        if "INTEREST" in desc_upper:
            return TransactionType.INT
        if "DIVIDEND" in desc_upper:
            return TransactionType.DIV

        # Fees
        if any(w in desc_upper for w in ("FEE", "SERVICE CHG", "PENALTY", "OVERDRAFT")):
            return TransactionType.FEE

        # Checks
        if any(w in desc_upper for w in ("CHECK #", "CHECK#", "CHK #", "CHK#", "CHEQUE")):
            return TransactionType.CHECK

        # ATM
        if "ATM" in desc_upper:
            return TransactionType.ATM

        # Transfers
        if any(w in desc_upper for w in ("TRANSFER", "XFER", "ZELLE", "VENMO", "PAYPAL")):
            return TransactionType.XFER

        # Direct deposit (payroll, government payments)
        if self.amount > 0 and any(
            w in desc_upper
            for w in ("DIRECT DEP", "DIRECTDEP", "PAYROLL", "ACH CREDIT",
                      "ACH DEP", "TAX REFUND", "MOBILE DEP")
        ):
            return TransactionType.DIRECTDEP

        # ACH debit / bill payment
        if self.amount < 0 and any(
            w in desc_upper
            for w in ("ACH DEBIT", "BILL PAY", "PAYMENT", "ACH PMT", "ONLINE PMT")
        ):
            return TransactionType.PAYMENT

        # POS / card purchases
        if any(w in desc_upper for w in ("POS ", "PURCHASE", "CARD PURCHASE", "DEBIT CARD")):
            return TransactionType.POS

        # Fall back to sign-based
        return TransactionType.CREDIT if self.amount >= 0 else TransactionType.DEBIT

    def generate_fit_id(self) -> str:
        """
        Generate a content-stable unique ID for the OFX FITID field.

        The ID is a SHA-256 hash (truncated to 16 hex chars) of the
        canonical fields that identify a transaction:
            date + amount (normalised) + description (lower-cased, stripped)

        This means the same transaction always gets the same FITID regardless
        of upload order, so QBO's duplicate-detection works correctly when a
        statement is re-imported or overlaps with another file.

        Collision handling: if two transactions share the same hash (same date,
        amount, and description — e.g. two identical coffee purchases on the
        same day) assign_fit_ids() appends a suffix (-1, -2 …) to each
        successive duplicate so every FITID in a statement is unique.
        """
        canonical = (
            self.date.isoformat()
            + "|" + str(self.amount.normalize())
            + "|" + self.description.strip().lower()
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"{self.date.strftime('%Y%m%d')}-{digest}"

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
        """
        Assign a unique FITID to every transaction (required by OFX spec)
        and infer the TRNTYPE for all transactions.

        FITID stability guarantee
        ─────────────────────────
        Each FITID is a content hash of (date, amount, description) so the
        same transaction always gets the same ID regardless of upload order.
        QBO uses FITIDs to skip duplicates — stable IDs mean re-importing a
        statement or uploading an overlapping statement never creates dupes.

        Collision handling
        ──────────────────
        Two transactions with the same date, amount, and description (e.g.
        two identical $4.50 coffee purchases on the same day) would hash to
        the same base ID.  We detect these and append a counter suffix so
        every FITID in the statement is unique:
            20240115-abc123def456abcd       ← first occurrence
            20240115-abc123def456abcd-1     ← second occurrence
            20240115-abc123def456abcd-2     ← third occurrence …
        """
        seen: dict[str, int] = {}
        for tx in self.transactions:
            # Always infer type — never leave transactions as OTHER
            tx.tx_type = tx.infer_type()

            if tx.fit_id is None:
                base  = tx.generate_fit_id()
                count = seen.get(base, 0)
                seen[base] = count + 1
                tx.fit_id = f"{base}-{count}" if count else base
