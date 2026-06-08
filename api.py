"""
PDF-to-QBO REST API

Endpoints:
  POST /convert          — upload PDF, get OFX/QFX/CSV back
  GET  /health           — health check for Railway
  GET  /banks            — list supported banks

Deploy on Railway:
  railway init
  railway up
"""
import tempfile
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

from src.parser import detect_and_parse, list_supported_banks
from src.exporter import to_ofx, to_csv
from src.models import Transaction, BankAccount, ParsedStatement, TransactionType

app = FastAPI(
    title="PDF to QBO Converter",
    description="Convert bank statement PDFs to QuickBooks-compatible OFX/QFX/CSV",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health check (Railway requires this) ─────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "pdf-to-qbo"}


# ── Supported banks ───────────────────────────────────────────────────────────

@app.get("/banks")
def banks():
    return {"supported_banks": list_supported_banks()}


# ── Main conversion endpoint ──────────────────────────────────────────────────

@app.post("/convert")
async def convert(
    file: UploadFile = File(..., description="Bank statement PDF"),
    format: Literal["ofx", "qfx", "csv"] = Query(
        default="ofx",
        description="Output format: ofx (QBO import), qfx (Quicken), csv"
    ),
):
    """
    Upload a bank statement PDF and receive a QBO-compatible file.

    Returns:
      - OFX file (Content-Type: application/x-ofx) for ofx/qfx
      - CSV file (Content-Type: text/csv) for csv

    The returned file can be imported directly into QuickBooks Online via
    Banking → Upload transactions.
    """
    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported. Please upload a .pdf file.",
        )

    # Read uploaded file into a temp file (pdfplumber needs a file path)
    contents = await file.read()
    if len(contents) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(
            status_code=413,
            detail="File too large. Maximum size is 50MB.",
        )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        statement = detect_and_parse(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse PDF: {str(e)}. "
                   "Ensure the PDF is a text-based (not scanned) bank statement.",
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    # Generate output
    base_name = Path(file.filename).stem
    fmt        = format.lower()

    if fmt in ("ofx", "qfx"):
        content      = to_ofx(statement, is_qfx=(fmt == "qfx"))
        media_type   = "application/x-ofx"
        filename     = f"{base_name}.{fmt}"
    else:
        content      = to_csv(statement)
        media_type   = "text/csv"
        filename     = f"{base_name}.csv"

    # Include summary in response headers for client convenience
    headers = {
        "X-Transaction-Count":  str(statement.transaction_count),
        "X-Bank-Name":          statement.account.bank_name,
        "X-Parser-Used":        statement.parser_used,
        "X-Warnings":           "; ".join(statement.warnings) or "none",
        "Content-Disposition":  f'attachment; filename="{filename}"',
    }

    return Response(
        content=content.encode("ascii", errors="replace"),
        media_type=media_type,
        headers=headers,
    )


# ── Statement summary (JSON, no file download) ────────────────────────────────

@app.post("/preview")
async def preview(
    file: UploadFile = File(..., description="Bank statement PDF"),
):
    """
    Upload a PDF and get a JSON summary of extracted transactions.
    Useful for previewing before committing to a full conversion.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files supported.")

    contents = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        statement = detect_and_parse(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "bank":             statement.account.bank_name,
        "account_id":       statement.account.account_id,
        "statement_start":  str(statement.account.statement_start),
        "statement_end":    str(statement.account.statement_end),
        "transaction_count": statement.transaction_count,
        "total_debits":     float(statement.total_debits),
        "total_credits":    float(statement.total_credits),
        "opening_balance":  float(statement.account.opening_balance) if statement.account.opening_balance else None,
        "closing_balance":  float(statement.account.closing_balance) if statement.account.closing_balance else None,
        "parser_used":      statement.parser_used,
        "warnings":         statement.warnings,
        "transactions": [
            {
                "date":        str(tx.date),
                "description": tx.description,
                "amount":      float(tx.amount),
                "balance":     float(tx.balance) if tx.balance else None,
                "type":        tx.tx_type,
                "fit_id":      tx.fit_id,
                "source_page": tx.source_page,
            }
            for tx in statement.transactions
        ],
    }


# ── Export edited transactions ────────────────────────────────────────────────

class ExportTransaction(BaseModel):
    date: str
    description: str
    amount: float
    balance: Optional[float] = None
    type: str = "OTHER"

class ExportRequest(BaseModel):
    format: Literal["ofx", "qfx", "csv"] = "ofx"
    bank: str = "Unknown"
    account_id: str = "unknown"
    statement_start: Optional[str] = None
    statement_end: Optional[str] = None
    closing_balance: Optional[float] = None
    transactions: List[ExportTransaction]


@app.post("/export")
async def export_transactions(req: ExportRequest):
    """
    Accept reviewed/edited transactions as JSON and return an OFX/QFX/CSV file.
    Used by the review UI after the user has corrected any flagged transactions.
    """
    try:
        txns = []
        for tx in req.transactions:
            try:
                tx_type = TransactionType(tx.type)
            except ValueError:
                tx_type = TransactionType.OTHER
            txns.append(Transaction(
                date=date_type.fromisoformat(tx.date),
                description=tx.description,
                amount=Decimal(str(tx.amount)),
                balance=Decimal(str(tx.balance)) if tx.balance is not None else None,
                tx_type=tx_type,
            ))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid transaction data: {e}")

    account = BankAccount(
        bank_name=req.bank,
        account_id=req.account_id,
        statement_start=date_type.fromisoformat(req.statement_start) if req.statement_start else None,
        statement_end=date_type.fromisoformat(req.statement_end) if req.statement_end else None,
        closing_balance=Decimal(str(req.closing_balance)) if req.closing_balance is not None else None,
    )

    statement = ParsedStatement(account=account, transactions=txns)
    statement.assign_fit_ids()

    fmt = req.format.lower()
    if fmt in ("ofx", "qfx"):
        content    = to_ofx(statement, is_qfx=(fmt == "qfx"))
        media_type = "application/x-ofx"
        filename   = f"export.{fmt}"
    else:
        content    = to_csv(statement)
        media_type = "text/csv"
        filename   = "export.csv"

    return Response(
        content=content.encode("ascii", errors="replace"),
        media_type=media_type,
        headers={
            "Content-Disposition":  f'attachment; filename="{filename}"',
            "X-Transaction-Count":  str(len(txns)),
        },
    )
