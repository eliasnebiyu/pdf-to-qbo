"""
OCR support for scanned / image-based bank statement PDFs.

Strategy (in priority order):
  1. Claude Vision API   — best accuracy, no system deps, needs ANTHROPIC_API_KEY
  2. pytesseract         — free, offline, needs tesseract installed on the OS
  3. Graceful failure    — returns empty string with a clear warning

``is_scanned_pdf()`` detects whether a PDF is image-based by checking
whether pdfplumber extracts very little text relative to the page count.
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
from pathlib import Path
from typing import Optional


# ── Detection ─────────────────────────────────────────────────────────────────

def is_scanned_pdf(pdf_path: str | Path) -> bool:
    """
    Return True if the PDF appears to be a scanned image rather than
    a digital / text-based document.

    Heuristic: fewer than 80 characters of text per page on average.
    """
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return False
            total_chars = sum(
                len(p.extract_text() or "") for p in pdf.pages
            )
            avg_chars_per_page = total_chars / len(pdf.pages)
            return avg_chars_per_page < 80
    except Exception:
        return False


# ── OCR engines ───────────────────────────────────────────────────────────────

def _ocr_with_claude(pdf_path: Path, api_key: str) -> str:
    """
    Extract text from a scanned PDF using Claude's vision API.

    Converts each page to a PNG image, sends it to Claude, and
    concatenates the extracted text.
    """
    try:
        from pdf2image import convert_from_path
        import anthropic
    except ImportError as e:
        raise RuntimeError(f"Missing dependency for Claude OCR: {e}") from e

    client = anthropic.Anthropic(api_key=api_key)
    model  = os.getenv("PDF_PARSER_LLM_MODEL", "claude-3-5-haiku-20241022")

    pages  = convert_from_path(str(pdf_path), dpi=200, fmt="png")
    texts: list[str] = []

    for page_img in pages:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            page_img.save(tmp.name, "PNG")
            img_bytes = Path(tmp.name).read_bytes()
            Path(tmp.name).unlink(missing_ok=True)

        img_b64 = base64.standard_b64encode(img_bytes).decode()

        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a page from a bank statement. "
                            "Extract ALL text exactly as it appears, "
                            "preserving columns and spacing as best you can. "
                            "Output ONLY the extracted text, nothing else."
                        ),
                    },
                ],
            }],
        )
        texts.append(resp.content[0].text)

    return "\n--- PAGE BREAK ---\n".join(texts)


def _ocr_with_tesseract(pdf_path: Path) -> str:
    """Extract text using pytesseract (requires tesseract OS package)."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        raise RuntimeError(f"Missing dependency for tesseract OCR: {e}") from e

    pages = convert_from_path(str(pdf_path), dpi=200)
    texts = [pytesseract.image_to_string(page) for page in pages]
    return "\n--- PAGE BREAK ---\n".join(texts)


# ── Public entry point ────────────────────────────────────────────────────────

def ocr_pdf(pdf_path: str | Path) -> tuple[str, str]:
    """
    Attempt to extract text from a scanned PDF.

    Returns:
        (text, engine_used)  where engine_used is one of:
          "claude", "tesseract", or "none" (with text = "")

    Never raises — errors are returned as (empty_string, "none").
    """
    pdf_path = Path(pdf_path)

    # Try Claude Vision first (better accuracy, no OS dependency)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            text = _ocr_with_claude(pdf_path, api_key)
            if text.strip():
                return text, "claude"
        except Exception:
            pass  # fall through to tesseract

    # Try pytesseract
    try:
        import pytesseract
        import subprocess
        # Check tesseract is installed (pytesseract silently fails if not)
        result = subprocess.run(["tesseract", "--version"], capture_output=True)
        if result.returncode == 0:
            text = _ocr_with_tesseract(pdf_path)
            if text.strip():
                return text, "tesseract"
    except Exception:
        pass

    return "", "none"
