"""
Parser router — opens a PDF and returns the right bank-specific parser.

Priority order:
  1. Rule-based parsers (fast, free, deterministic)
  2. LLM fallback (Claude API) — activated when:
       a. No rule-based parser recognised the bank (unknown bank)
       b. A rule-based parser matched but returned 0 transactions
          (e.g. the bank changed its PDF layout)

The LLM fallback is silently skipped when ANTHROPIC_API_KEY is not set.
"""
from __future__ import annotations
from pathlib import Path

from src.parser.base import BaseParser
from src.parser.banks.chase import ChaseParser
from src.parser.banks.bofa import BofAParser
from src.parser.banks.wells import WellsFargoParser
from src.parser.banks.citi import CitiParser
from src.parser.banks.kemba import KembaParser
from src.parser.banks.us_bank import USBankParser
from src.parser.banks.pnc import PNCParser
from src.parser.banks.amex import AmexParser
from src.parser.banks.fifth_third import FifthThirdParser
from src.parser.banks.generic import GenericParser
from src.parser.banks.llm_parser import LLMParser
from src.models import ParsedStatement

# Rule-based parsers tried in priority order
_RULE_PARSERS: list[type[BaseParser]] = [
    ChaseParser,
    BofAParser,
    CitiParser,
    AmexParser,          # before US Bank — Amex PDFs mention "US bank" in boilerplate
    USBankParser,
    WellsFargoParser,    # after US Bank — US Bank PDFs may contain "Wells Fargo" in tx descriptions
    KembaParser,
    PNCParser,
    FifthThirdParser,
    GenericParser,
]

# Full list including the LLM fallback (used for list_supported_banks())
_PARSERS = _RULE_PARSERS + [LLMParser]


def detect_and_parse(pdf_path: str | Path) -> ParsedStatement:
    """
    Open a PDF, detect the bank, and return a fully parsed statement.

    Strategy
    --------
    1. Try each rule-based parser in order.  Return on the first one that
       both recognises the file AND extracts at least one transaction.
    2. If a rule-based parser recognises the file but returns 0 transactions
       (likely a layout change), try the LLM fallback.
    3. If no rule-based parser recognises the file at all, try the LLM
       fallback as a catch-all for unknown banks.
    4. If the LLM is not configured (no API key), return the best result
       we have — even if it's 0 transactions with a warning.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {pdf_path.suffix}")

    zero_tx_stmt: ParsedStatement | None = None   # best rule-based result so far

    for ParserClass in _RULE_PARSERS:
        parser = ParserClass(pdf_path)
        with parser:
            if not parser.can_parse():
                continue

            stmt = parser.extract()

            if stmt.transactions:
                # ✓ Rule-based parser succeeded
                return stmt

            # Parser matched the bank but found no transactions.
            # Save it and try the LLM fallback below.
            if zero_tx_stmt is None:
                zero_tx_stmt = stmt
            break   # One bank matched — don't try other rule-based parsers

    # ── LLM fallback ─────────────────────────────────────────────────────────
    llm = LLMParser(pdf_path)
    with llm:
        if llm.can_parse():
            llm_stmt = llm.extract()

            if llm_stmt.transactions:
                if zero_tx_stmt is not None:
                    # Annotate: rule-based parser found the bank but got 0 tx
                    llm_stmt.warnings.insert(
                        0,
                        f"Rule-based parser ({zero_tx_stmt.parser_used}) recognised "
                        f"the bank but extracted 0 transactions (possible layout "
                        f"change). LLM fallback was used instead.",
                    )
                return llm_stmt

    # ── Nothing worked ────────────────────────────────────────────────────────
    if zero_tx_stmt is not None:
        # Return the rule-based 0-tx result — at least it has bank/period info
        return zero_tx_stmt

    raise ValueError(
        f"No parser could handle: {pdf_path.name}. "
        "Set ANTHROPIC_API_KEY to enable the LLM fallback for unknown banks."
    )


def list_supported_banks() -> list[str]:
    """Return human-readable names of all supported banks."""
    names = []
    for cls in _RULE_PARSERS:
        name = getattr(cls, "bank_name", cls.__name__)
        if name not in ("Generic Bank",):
            names.append(name)
    names.append("Generic (any standard layout)")
    names.append("Any bank via LLM fallback (requires ANTHROPIC_API_KEY)")
    return names
