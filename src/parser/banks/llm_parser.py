"""
LLM-powered fallback parser using the Anthropic Claude API.

This parser handles two scenarios that rule-based parsers cannot:
  1. A known bank whose PDF layout has changed  (rule-based returns 0 tx)
  2. A bank we haven't built a dedicated parser for yet

It sends the full PDF text to Claude and uses *tool use* to get
back a strictly typed JSON response — tool use is far more reliable
than asking the model to format raw JSON itself.

Activation:
  Set the ANTHROPIC_API_KEY environment variable.  Without it,
  can_parse() returns False and this parser is silently skipped.

Model:
  Defaults to claude-3-5-haiku-20241022 (fast + cheap).
  Override with the PDF_PARSER_LLM_MODEL environment variable.

Token budget:
  Up to 50 000 characters of PDF text are sent (~12 500 tokens).
  For multi-page statements that overflow this limit, the text is
  split into chunks and the transactions are merged + deduplicated.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from src.models import BankAccount, AccountType, ParsedStatement, Transaction
from src.parser.base import BaseParser
from src.utils.amount_parser import parse_date
from src.utils.dedup import clean

# ── Anthropic import (optional dependency) ────────────────────────────────────

try:
    import anthropic as _anthropic          # noqa: F401  (used at runtime)
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────

_DEFAULT_MODEL  = "claude-3-5-haiku-20241022"
_MAX_CHUNK_CHARS = 50_000   # ~12 500 tokens — fits comfortably in context

# ── Tool schema ───────────────────────────────────────────────────────────────
# Claude's tool use guarantees the output matches this schema exactly.

_TOOL = {
    "name": "extract_bank_statement",
    "description": (
        "Extract all account information and every transaction from a bank "
        "statement.  Include every single transaction — do not skip any."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "bank_name": {
                "type": "string",
                "description": "Name of the bank or financial institution",
            },
            "account_id": {
                "type": "string",
                "description": "Account number or last 4 digits if fully masked",
            },
            "account_type": {
                "type": "string",
                "enum": ["checking", "savings", "credit", "unknown"],
                "description": "Type of account",
            },
            "statement_start": {
                "type": "string",
                "description": "Statement period start date in YYYY-MM-DD format, or empty string if unknown",
            },
            "statement_end": {
                "type": "string",
                "description": "Statement period end date in YYYY-MM-DD format, or empty string if unknown",
            },
            "opening_balance": {
                "type": "number",
                "description": "Opening / beginning balance in dollars, or null if not shown",
            },
            "closing_balance": {
                "type": "number",
                "description": "Closing / ending balance in dollars, or null if not shown",
            },
            "transactions": {
                "type": "array",
                "description": "All transactions found in the statement",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Transaction date in YYYY-MM-DD format",
                        },
                        "description": {
                            "type": "string",
                            "description": "Transaction description / payee",
                        },
                        "amount": {
                            "type": "number",
                            "description": (
                                "Transaction amount in dollars.  "
                                "NEGATIVE for debits/withdrawals/charges/purchases.  "
                                "POSITIVE for credits/deposits/payments."
                            ),
                        },
                        "balance": {
                            "type": "number",
                            "description": "Running balance after this transaction, or null if not shown",
                        },
                    },
                    "required": ["date", "description", "amount"],
                },
            },
        },
        "required": ["bank_name", "account_type", "transactions"],
    },
}

_USER_MSG = (
    "Please extract all account information and every transaction from the "
    "bank statement text below.  Use the extract_bank_statement tool.\n\n"
    "Sign convention:\n"
    "  • Withdrawals / purchases / charges → NEGATIVE amount\n"
    "  • Deposits / payments / credits     → POSITIVE amount\n\n"
    "Statement text:\n\n{text}"
)


class LLMParser(BaseParser):
    """
    Claude API-powered parser.  Used as a fallback when:
      • No rule-based parser matched (unknown bank)
      • A rule-based parser matched but returned 0 transactions (layout change)
      • The PDF is scanned (OCR text injected via injected_text)
    """

    bank_name = "LLM (Claude)"

    def __init__(self, pdf_path, injected_text: str | None = None, password: str | None = None):
        super().__init__(pdf_path, password=password)
        self._injected_text = injected_text

    def can_parse(self) -> bool:
        """Available only when the SDK is installed and an API key is set."""
        return _SDK_AVAILABLE and bool(os.environ.get("ANTHROPIC_API_KEY"))

    def extract(self) -> ParsedStatement:
        text = self._injected_text if self._injected_text is not None else self.full_text

        if not text.strip():
            self.warn("LLM parser: PDF produced no extractable text (scanned/image PDF?).")
            return self._empty_statement()

        # Split into chunks if needed, extract from each, then merge
        chunks  = _split_text(text, _MAX_CHUNK_CHARS)
        results = [self._extract_chunk(chunk, idx, len(chunks))
                   for idx, chunk in enumerate(chunks)]

        account = results[0]["account"] if results else self._empty_account()
        all_txns: list[Transaction] = []
        for r in results:
            all_txns.extend(r["transactions"])

        all_txns = clean(all_txns, warn=self.warn)

        stmt = ParsedStatement(
            account=account,
            transactions=all_txns,
            raw_page_count=self.page_count,
            parser_used="llm",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_chunk(
        self, text: str, chunk_idx: int, total_chunks: int
    ) -> dict[str, Any]:
        """Call Claude API for one chunk; return {account, transactions}."""
        import anthropic

        model  = os.environ.get("PDF_PARSER_LLM_MODEL", _DEFAULT_MODEL)
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        context = ""
        if total_chunks > 1:
            context = (
                f"[This is chunk {chunk_idx + 1} of {total_chunks} of the statement.  "
                "Extract only the transactions visible in THIS chunk.]\n\n"
            )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                tools=[_TOOL],
                tool_choice={"type": "any"},   # force tool use
                messages=[{
                    "role": "user",
                    "content": _USER_MSG.format(text=context + text),
                }],
            )
        except Exception as exc:
            self.warn(f"LLM API error (chunk {chunk_idx + 1}): {exc}")
            return {"account": self._empty_account(), "transactions": []}

        # Extract tool_use block
        data: dict = {}
        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_bank_statement":
                data = block.input or {}
                break

        if not data:
            self.warn(f"LLM chunk {chunk_idx + 1}: no tool_use block in response.")
            return {"account": self._empty_account(), "transactions": []}

        # Build account (only from chunk 0 to avoid overwrite by continuation pages)
        account = self._empty_account()
        if chunk_idx == 0:
            account = BankAccount(
                bank_name=data.get("bank_name") or "Unknown Bank",
                account_id=data.get("account_id") or "",
                statement_start=_safe_date(data.get("statement_start")),
                statement_end=_safe_date(data.get("statement_end")),
                opening_balance=_safe_decimal(data.get("opening_balance")),
                closing_balance=_safe_decimal(data.get("closing_balance")),
            )
            acct_type = (data.get("account_type") or "").lower()
            if acct_type == "credit":
                account.account_type = AccountType.CREDIT
            elif acct_type == "savings":
                account.account_type = AccountType.SAVINGS

        # Build transactions
        txns: list[Transaction] = []
        for raw in data.get("transactions") or []:
            try:
                d = _safe_date(raw.get("date"))
                if d is None:
                    continue
                amount = _safe_decimal(raw.get("amount"))
                if amount is None:
                    continue
                txns.append(Transaction(
                    date=d,
                    description=(raw.get("description") or "Transaction").strip(),
                    amount=amount,
                    balance=_safe_decimal(raw.get("balance")),
                ))
            except Exception as exc:
                self.warn(f"LLM tx parse error: {exc}")

        return {"account": account, "transactions": txns}

    def _empty_account(self) -> BankAccount:
        return BankAccount(bank_name="Unknown Bank", account_id="")

    def _empty_statement(self) -> ParsedStatement:
        stmt = ParsedStatement(
            account=self._empty_account(),
            transactions=[],
            raw_page_count=self.page_count,
            parser_used="llm",
            warnings=self.warnings,
        )
        stmt.assign_fit_ids()
        return stmt


# ── Module-level helpers ──────────────────────────────────────────────────────

def _split_text(text: str, chunk_size: int) -> list[str]:
    """Split text into chunks of at most chunk_size characters, on line boundaries."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, chunk_size)
        if split_at == -1:
            split_at = chunk_size
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _safe_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return parse_date(str(value).strip())
    except Exception:
        return None


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
