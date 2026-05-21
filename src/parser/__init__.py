"""
Parser router — opens a PDF and returns the right bank-specific parser.
Add new bank parsers here; they are tried in priority order before
the generic fallback.
"""
from __future__ import annotations
from pathlib import Path

from src.parser.base import BaseParser
from src.parser.banks.chase import ChaseParser
from src.parser.banks.bofa import BofAParser
from src.parser.banks.wells import WellsFargoParser
from src.parser.banks.generic import GenericParser
from src.models import ParsedStatement
from src.parser.banks.citi import CitiParser

_PARSERS: list[type[BaseParser]] = [
    ChaseParser,
    BofAParser,
    WellsFargoParser,
    CitiParser,       # ← add this line before GenericParser
    GenericParser,
]



def detect_and_parse(pdf_path: str | Path) -> ParsedStatement:
    """
    Open a PDF, detect the bank, and return a fully parsed statement.

    This is the main entry point for all parsing logic.
    Raises ValueError if no parser can handle the file.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {pdf_path.suffix}")

    for ParserClass in _PARSERS:
        parser = ParserClass(pdf_path)
        with parser:
            if parser.can_parse():
                return parser.extract()

    raise ValueError(f"No parser could handle: {pdf_path.name}")


def list_supported_banks() -> list[str]:
    """Return human-readable names of all supported banks."""
    names = []
    for cls in _PARSERS:
        name = getattr(cls, "bank_name", cls.__name__)
        if name != "Generic Bank":
            names.append(name)
    names.append("Generic (any standard layout)")
    return names
