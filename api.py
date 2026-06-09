"""
PDF-to-QBO REST API

Endpoints:
  POST /convert          — upload single PDF, get OFX/QFX/CSV back
  POST /batch            — upload multiple PDFs, get merged OFX/QFX/CSV
  POST /preview          — upload PDF, get JSON transaction list (for ReviewUI)
  POST /export           — accept reviewed JSON transactions, get OFX/QFX/CSV
  GET  /health           — health check for Railway
  GET  /banks            — list supported banks

Deploy on Railway:
  Set ALLOWED_ORIGINS=https://your-frontend.railway.app
  Set ANTHROPIC_API_KEY=sk-ant-...  (enables LLM fallback + OCR)
  railway up
"""
import os
import tempfile
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from src.parser import detect_and_parse, list_supported_banks
from src.exporter import to_ofx, to_csv
from src.models import Transaction, BankAccount, ParsedStatement, TransactionType
from src.utils.categorize import categorize_transactions
from src.utils.dedup import clean, merge_statements

app = FastAPI(
    title="PDF to QBO Converter",
    description="Convert bank statement PDFs to QuickBooks-compatible OFX/QFX/CSV",
    version="1.1.0",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Default: localhost dev servers.
# Production: set ALLOWED_ORIGINS=https://your-frontend.com in Railway env vars.
# Multiple origins: comma-separated  e.g. "https://app.example.com,https://www.example.com"
_default_origins = [
    "http://localhost:5173",
    "http://localhost:4173",
    "http://localhost:3000",
]
_env_origins = os.getenv("ALLOWED_ORIGINS", "")
_allowed_origins: list[str] = (
    [o.strip() for o in _env_origins.split(",") if o.strip()]
    if _env_origins
    else _default_origins
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Transaction-Count",
        "X-Bank-Name",
        "X-Parser-Used",
        "X-Warnings",
        "Content-Disposition",
    ],
)

# ── Shared helpers ────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024


def _save_upload(contents: bytes) -> Path:
    """Write uploaded bytes to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(contents)
    tmp.close()
    return Path(tmp.name)


def _filter_by_date(
    txns: list[Transaction],
    start: Optional[str],
    end: Optional[str],
) -> list[Transaction]:
    """Filter transactions to the given inclusive date range."""
    if not start and not end:
        return txns
    try:
        s = date_type.fromisoformat(start) if start else None
        e = date_type.fromisoformat(end)   if end   else None
    except ValueError:
        return txns
    return [
        tx for tx in txns
        if (s is None or tx.date >= s) and (e is None or tx.date <= e)
    ]


def _tx_to_dict(tx: Transaction) -> dict:
    return {
        "date":        str(tx.date),
        "description": tx.description,
        "amount":      float(tx.amount),
        "balance":     float(tx.balance) if tx.balance else None,
        "type":        str(tx.tx_type),
        "category":    tx.category or "",
        "fit_id":      tx.fit_id,
        "source_page": tx.source_page,
    }


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "pdf-to-qbo", "version": "1.1.0"}


# ── Supported banks ───────────────────────────────────────────────────────────

@app.get("/banks")
def banks():
    return {"supported_banks": list_supported_banks()}


# ── Single-file conversion ────────────────────────────────────────────────────

@app.post("/convert")
async def convert(
    file: UploadFile = File(..., description="Bank statement PDF"),
    format: Literal["ofx", "qfx", "csv"] = Query(
        default="ofx",
        description="Output format: ofx (QBO import), qfx (Quicken), csv",
    ),
    start_date: Optional[str] = Query(
        default=None,
        description="Filter: only include transactions on/after this date (YYYY-MM-DD)",
    ),
    end_date: Optional[str] = Query(
        default=None,
        description="Filter: only include transactions on/before this date (YYYY-MM-DD)",
    ),
    categorize: bool = Query(
        default=True,
        description="Add QBO category suggestions to transactions",
    ),
    password: Optional[str] = Query(
        default=None,
        description="Password to decrypt a password-protected PDF",
    ),
):
    """
    Upload a single bank statement PDF and receive a QBO-compatible file.

    The returned file can be imported into QuickBooks Online via
    Banking → Upload transactions.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {os.getenv('MAX_UPLOAD_MB', 50)}MB.")

    tmp_path = _save_upload(contents)
    try:
        statement = detect_and_parse(tmp_path, password=password)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    # Date range filter
    statement.transactions = _filter_by_date(statement.transactions, start_date, end_date)

    # Category suggestions
    if categorize:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        categorize_transactions(statement.transactions, use_llm=use_llm)

    base_name = Path(file.filename).stem
    fmt = format.lower()

    if fmt in ("ofx", "qfx"):
        content    = to_ofx(statement, is_qfx=(fmt == "qfx"))
        media_type = "application/x-ofx"
        filename   = f"{base_name}.{fmt}"
    else:
        content    = to_csv(statement)
        media_type = "text/csv"
        filename   = f"{base_name}.csv"

    return Response(
        content=content.encode("ascii", errors="replace"),
        media_type=media_type,
        headers={
            "X-Transaction-Count": str(statement.transaction_count),
            "X-Bank-Name":         statement.account.bank_name,
            "X-Parser-Used":       statement.parser_used,
            "X-Warnings":          "; ".join(statement.warnings) or "none",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ── Batch conversion ──────────────────────────────────────────────────────────

@app.post("/batch")
async def batch_convert(
    files: List[UploadFile] = File(..., description="One or more bank statement PDFs"),
    format: Literal["ofx", "qfx", "csv"] = Query(default="ofx"),
    start_date: Optional[str] = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str] = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool          = Query(default=True),
    password:   Optional[str] = Query(default=None, description="Password applied to all uploaded PDFs"),
):
    """
    Upload multiple PDFs at once (e.g. 12 months of statements).

    Transactions are merged, sorted by date, cross-statement duplicates
    removed (handles statement boundary overlap), then exported as a
    single file.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per batch.")

    statements = []
    all_warnings: list[str] = []
    tmp_paths: list[Path] = []

    for upload in files:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            all_warnings.append(f"Skipped non-PDF file: {upload.filename}")
            continue

        contents = await upload.read()
        if len(contents) > _MAX_UPLOAD_BYTES:
            all_warnings.append(f"Skipped oversized file: {upload.filename}")
            continue

        tmp_path = _save_upload(contents)
        tmp_paths.append(tmp_path)

        try:
            stmt = detect_and_parse(tmp_path, password=password)
            statements.append(stmt)
            all_warnings.extend([f"{upload.filename}: {w}" for w in stmt.warnings])
        except Exception as e:
            all_warnings.append(f"Failed to parse {upload.filename}: {e}")

    for p in tmp_paths:
        p.unlink(missing_ok=True)

    if not statements:
        raise HTTPException(status_code=422, detail="No PDFs could be parsed. " + "; ".join(all_warnings))

    # Merge + cross-statement dedup
    warns: list[str] = []
    merged_txns = merge_statements(statements, warn=warns.append)
    all_warnings.extend(warns)

    # Date range filter
    merged_txns = _filter_by_date(merged_txns, start_date, end_date)

    # Category suggestions
    if categorize:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        categorize_transactions(merged_txns, use_llm=use_llm)

    # Build a merged statement using the first statement's account info
    primary = statements[0].account
    merged_stmt = ParsedStatement(
        account=BankAccount(
            bank_name=primary.bank_name,
            account_id=primary.account_id,
            account_type=primary.account_type,
            statement_start=min(
                s.account.statement_start for s in statements if s.account.statement_start
            ) if any(s.account.statement_start for s in statements) else None,
            statement_end=max(
                s.account.statement_end for s in statements if s.account.statement_end
            ) if any(s.account.statement_end for s in statements) else None,
        ),
        transactions=merged_txns,
        raw_page_count=sum(s.raw_page_count for s in statements),
        parser_used="batch",
        warnings=all_warnings,
    )
    merged_stmt.assign_fit_ids()

    fmt = format.lower()
    if fmt in ("ofx", "qfx"):
        content    = to_ofx(merged_stmt, is_qfx=(fmt == "qfx"))
        media_type = "application/x-ofx"
        filename   = f"batch_export.{fmt}"
    else:
        content    = to_csv(merged_stmt)
        media_type = "text/csv"
        filename   = "batch_export.csv"

    return Response(
        content=content.encode("ascii", errors="replace"),
        media_type=media_type,
        headers={
            "X-Transaction-Count": str(len(merged_txns)),
            "X-File-Count":        str(len(statements)),
            "X-Warnings":          "; ".join(all_warnings) or "none",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ── Statement preview (JSON) ──────────────────────────────────────────────────

@app.post("/preview")
async def preview(
    file: UploadFile = File(..., description="Bank statement PDF"),
    start_date: Optional[str] = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str] = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool          = Query(default=True, description="Add category suggestions"),
    password:   Optional[str] = Query(default=None, description="Password for a password-protected PDF"),
):
    """
    Upload a PDF and get a JSON summary of extracted transactions.
    Used by the ReviewUI for previewing and editing before export.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files supported.")

    contents = await file.read()
    tmp_path = _save_upload(contents)

    try:
        statement = detect_and_parse(tmp_path, password=password)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    # Date range filter
    statement.transactions = _filter_by_date(statement.transactions, start_date, end_date)

    # Category suggestions
    if categorize:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        categorize_transactions(statement.transactions, use_llm=use_llm)

    return {
        "bank":              statement.account.bank_name,
        "account_id":        statement.account.account_id,
        "account_type":      str(statement.account.account_type),
        "statement_start":   str(statement.account.statement_start),
        "statement_end":     str(statement.account.statement_end),
        "transaction_count": statement.transaction_count,
        "total_debits":      float(statement.total_debits),
        "total_credits":     float(statement.total_credits),
        "opening_balance":   float(statement.account.opening_balance) if statement.account.opening_balance else None,
        "closing_balance":   float(statement.account.closing_balance) if statement.account.closing_balance else None,
        "parser_used":       statement.parser_used,
        "warnings":          statement.warnings,
        "transactions":      [_tx_to_dict(tx) for tx in statement.transactions],
    }


# ── Export reviewed transactions ──────────────────────────────────────────────

class ExportTransaction(BaseModel):
    date:        str
    description: str
    amount:      float
    balance:     Optional[float] = None
    type:        str             = "OTHER"
    category:    Optional[str]  = None


class ExportRequest(BaseModel):
    format:          Literal["ofx", "qfx", "csv"] = "ofx"
    bank:            str           = "Unknown"
    account_id:      str           = "unknown"
    account_type:    str           = "CHECKING"
    statement_start: Optional[str] = None
    statement_end:   Optional[str] = None
    closing_balance: Optional[float] = None
    transactions:    List[ExportTransaction]


@app.post("/export")
async def export_transactions(req: ExportRequest):
    """
    Accept reviewed/edited transactions as JSON and return an OFX/QFX/CSV file.
    Called by the ReviewUI after the user has corrected any flagged transactions.
    """
    from src.models import AccountType

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
                category=tx.category,
            ))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid transaction data: {e}")

    try:
        acct_type = AccountType(req.account_type)
    except ValueError:
        acct_type = AccountType.CHECKING

    account = BankAccount(
        bank_name=req.bank,
        account_id=req.account_id,
        account_type=acct_type,
        statement_start=date_type.fromisoformat(req.statement_start) if req.statement_start else None,
        statement_end=date_type.fromisoformat(req.statement_end)   if req.statement_end   else None,
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
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Transaction-Count": str(len(txns)),
        },
    )
