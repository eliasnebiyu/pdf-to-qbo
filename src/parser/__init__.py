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
from src.utils.ocr import is_scanned_pdf, ocr_pdf

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


def _check_pdf_password(pdf_path: Path, password: str | None = None) -> None:
    """
    Raise a clear ValueError if the PDF is password-protected and no
    password (or the wrong password) was supplied.

    pdfplumber wraps pdfminer which raises PDFPasswordIncorrect when it
    encounters an encrypted PDF.  We catch that and surface a friendly,
    actionable message instead of a raw traceback.
    """
    import pdfplumber
    try:
        kw = {"password": password} if password else {}
        with pdfplumber.open(pdf_path, **kw) as pdf:
            # Force pdfminer to actually attempt decryption by reading page 1
            _ = pdf.pages[0].extract_text() if pdf.pages else None
    except Exception as exc:
        msg = str(exc).lower()
        if any(k in msg for k in ("password", "encrypt", "incorrect", "decrypt")):
            if password:
                raise ValueError(
                    "The password you supplied is incorrect for this PDF. "
                    "Please check the password and try again."
                ) from None
            raise ValueError(
                "This PDF is password-protected. "
                "Please provide the password via the ?password= query parameter, "
                "or remove the password in your PDF reader (File → Export/Save as PDF "
                "without password) before uploading."
            ) from None
        # Not a password error — re-raise so the caller sees the real problem
        raise


def detect_and_parse(pdf_path: str | Path, password: str | None = None) -> ParsedStatement:
    """
    Open a PDF, detect the bank, and return a fully parsed statement.

    Parameters
    ----------
    pdf_path : path to the PDF file
    password : optional decryption password for password-protected PDFs

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

    # ── Password check ────────────────────────────────────────────────────────
    # Raises ValueError with a clear message if the PDF is encrypted and no
    # password (or the wrong password) was given.  BaseParser.open() forwards
    # the password to pdfplumber so all parsers can read the decrypted file.
    _check_pdf_password(pdf_path, password=password)

    # ── OCR pre-check ─────────────────────────────────────────────────────────
    # If the PDF is scanned (image-based), pdfplumber extracts no text and all
    # rule-based parsers will return 0 transactions.  Run OCR first so the
    # extracted text can flow through the normal parser pipeline.
    _ocr_engine: str = ""
    if is_scanned_pdf(pdf_path):
        ocr_text, _ocr_engine = ocr_pdf(pdf_path)
        if not ocr_text:
            # No OCR available — raise a clear, actionable error
            raise ValueError(
                "This PDF appears to be a scanned image with no extractable text. "
                "To process scanned statements set ANTHROPIC_API_KEY (uses Claude "
                "vision) or install the tesseract OCR engine."
            )
        # Inject OCR text into a temporary text file so the LLM parser can use it
        # (rule-based parsers need pdfplumber's per-page extraction)
        from src.parser.banks.llm_parser import LLMParser as _LLM
        llm = _LLM(pdf_path, injected_text=ocr_text, password=password)
        with llm:
            if llm.can_parse():
                stmt = llm.extract()
                if stmt:
                    stmt.warnings.insert(0, f"Scanned PDF — text extracted via OCR ({_ocr_engine}).")
                    return stmt
        raise ValueError("Scanned PDF: OCR extracted text but no transactions could be parsed.")

    zero_tx_stmt: ParsedStatement | None = None   # best rule-based result so far

    for ParserClass in _RULE_PARSERS:
        parser = ParserClass(pdf_path, password=password)
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
    llm = LLMParser(pdf_path, password=password)
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
