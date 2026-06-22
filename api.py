"""
PDF-to-QBO REST API  v1.2
==========================

Public endpoints  (no API key required)
----------------------------------------
  GET  /health              Health check
  GET  /banks               List supported banks
  POST /auth/register       Issue a free API key

Auth-required endpoints  (X-API-Key: <key> header)
----------------------------------------------------
  POST /convert             Upload single PDF → OFX/QFX/CSV      (1 conversion)
  POST /batch               Upload multiple PDFs → merged export  (N conversions)
  POST /preview             Upload PDF → JSON transaction list    (1 conversion)
  POST /export              Reviewed JSON → OFX/QFX/CSV          (free — no PDF)
  GET  /auth/usage          Plan, usage counter, quota info
  POST /auth/checkout       Create Stripe checkout to upgrade plan

Stripe webhook  (Stripe calls this directly — no API key needed)
-----------------------------------------------------------------
  POST /stripe/webhook

Subscription tiers
-------------------
  free     :  10 conversions / 30-day period
  starter  : 100 conversions / 30-day period  ($9/month)
  pro      :  unlimited                       ($29/month)

Rate limits  (per API key when authenticated, per IP otherwise)
----------------------------------------------------------------
  /auth/register           3 / hour
  /convert, /preview      20 / minute
  /batch                  10 / minute
  /export                 30 / minute
  /auth/usage, /checkout  10 / minute

Deploy on Railway
-----------------
  ALLOWED_ORIGINS=https://your-frontend.railway.app
  ANTHROPIC_API_KEY=sk-ant-...     (LLM fallback + OCR)
  STRIPE_SECRET_KEY=sk_live_...
  STRIPE_WEBHOOK_SECRET=whsec_...
  STRIPE_PRICE_STARTER=price_...
  STRIPE_PRICE_PRO=price_...
  ADMIN_API_KEY=<long-random-secret>   (optional: bypasses all quotas)
"""
import os
import re
import tempfile
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.auth import (
    PLANS,
    check_and_increment,
    create_api_key,
    increment_usage,
    require_api_key,
    validate_and_check_quota,
    verify_key_only,
)
from src.utils.email import send_api_key_email, send_parsing_error_report
from src.billing import create_checkout_session, handle_webhook
from src.exporter import to_csv, to_ofx
from src.models import BankAccount, ParsedStatement, Transaction, TransactionType
from src.parser import detect_and_parse, list_supported_banks
from src.utils.categorize import categorize_transactions
from src.utils.dedup import merge_statements

# ── Rate limiter ───────────────────────────────────────────────────────────────


def _rate_key(request: Request) -> str:
    """Rate-limit by API key when present, otherwise by client IP."""
    key = request.headers.get("x-api-key", "").strip()
    return f"key:{key}" if key else f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_key)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Parsify",
    description="Convert bank statement PDFs to QuickBooks-compatible OFX/QFX/CSV",
    version="1.2.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────

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


# ── Health & metadata  (no auth) ──────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "parsify", "version": "1.2.0"}


@app.get("/banks")
def banks():
    return {"supported_banks": list_supported_banks()}


# ── Auth: register  (public, rate-limited per IP) ─────────────────────────────

class RegisterRequest(BaseModel):
    email: str


@app.post("/auth/register", status_code=201)
@limiter.limit("3/hour")
def register(request: Request, body: RegisterRequest):
    """
    Issue a free API key tied to an email address.

    The key is returned **once** — save it securely.
    Passing it as the ``X-API-Key`` header authenticates all subsequent requests.

    Rate-limited to 3 registrations per hour per IP to prevent spam.
    """
    email = body.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=422, detail="Invalid email address.")

    key  = create_api_key(email, plan="free")
    plan = PLANS["free"]

    # Fire-and-forget: email the key to the registrant's inbox.
    # Gracefully skipped when RESEND_API_KEY is not configured.
    send_api_key_email(email, key, plan="free")

    return {
        "api_key":        key,
        "email":          email,
        "plan":           "free",
        "monthly_limit":  plan["monthly_limit"],
        "message": (
            "Save this key — it will not be shown again. "
            "A copy has been sent to your email. "
            "Include it as the X-API-Key header on every request."
        ),
    }


# ── Report a parsing error (public, rate-limited) ─────────────────────────────

class ErrorReportRequest(BaseModel):
    email:       str
    bank:        str = ""
    description: str
    api_key:     str = ""


@app.post("/report-error", status_code=200)
@limiter.limit("10/hour")
def report_error(request: Request, body: ErrorReportRequest):
    """
    Accept a user-submitted parsing error report and forward it to support.
    No authentication required — we want to hear from free-tier users too.
    """
    if len(body.description.strip()) < 10:
        raise HTTPException(status_code=422, detail="Please describe the issue (10+ characters).")

    sent = send_parsing_error_report(
        user_email=body.email.strip(),
        bank=body.bank.strip(),
        description=body.description.strip(),
        api_key=body.api_key,
    )
    return {
        "received": True,
        "emailed":  sent,
        "message":  "Thanks — we'll investigate and improve the parser.",
    }


# ── Auth: usage info ──────────────────────────────────────────────────────────

@app.get("/auth/usage")
@limiter.limit("10/minute")
def usage(request: Request, record: dict = Depends(verify_key_only)):
    """Return the current plan, usage counter, and remaining quota for the key."""
    plan_info = PLANS.get(record["plan"], PLANS["free"])
    limit     = plan_info["monthly_limit"]
    used      = record["conversions_used"]
    return {
        "plan":                  record["plan"],
        "plan_label":            plan_info["label"],
        "monthly_limit":         limit,
        "conversions_used":      used,
        "conversions_remaining": (limit - used) if limit is not None else None,
        "period_start":          record["period_start"],
        "status":                record["status"],
        "stripe_customer_id":    record.get("stripe_customer_id"),
    }


# ── Auth: Stripe checkout ─────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan:        Literal["starter", "pro"]
    success_url: str
    cancel_url:  str


@app.post("/auth/checkout")
@limiter.limit("10/minute")
def checkout(
    request: Request,
    body:    CheckoutRequest,
    record:  dict = Depends(verify_key_only),
):
    """
    Create a Stripe Checkout session to upgrade to a paid plan.

    Returns ``{"checkout_url": "https://checkout.stripe.com/..."}`` — redirect
    the user there to enter card details.  On successful payment Stripe calls
    ``POST /stripe/webhook`` and the API key is automatically upgraded.

    Requires ``STRIPE_SECRET_KEY``, ``STRIPE_PRICE_STARTER``, and
    ``STRIPE_PRICE_PRO`` environment variables to be configured.
    """
    url = create_checkout_session(
        api_key=record["key"],
        email=record["email"],
        plan=body.plan,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
    )
    return {"checkout_url": url, "plan": body.plan}


# ── Stripe webhook  (called by Stripe, not the frontend) ─────────────────────

@app.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    """
    Stripe posts events here.  The signature is verified against
    ``STRIPE_WEBHOOK_SECRET`` — requests with an invalid signature are
    rejected with 400.

    Handled events:
      * checkout.session.completed       → upgrade plan
      * invoice.payment_succeeded        → reactivate suspended key
      * invoice.payment_failed           → suspend key
      * customer.subscription.deleted   → cancel, revert to free
    """
    return await handle_webhook(request)


# ── Single-file conversion ────────────────────────────────────────────────────

@app.post("/convert")
@limiter.limit("20/minute")
async def convert(
    request:    Request,
    file:       UploadFile = File(..., description="Bank statement PDF"),
    format:     Literal["ofx", "qfx", "csv"] = Query(
        default="ofx",
        description="Output format: ofx (QBO import), qfx (Quicken), csv",
    ),
    start_date: Optional[str] = Query(
        default=None,
        description="Only include transactions on/after this date (YYYY-MM-DD)",
    ),
    end_date:   Optional[str] = Query(
        default=None,
        description="Only include transactions on/before this date (YYYY-MM-DD)",
    ),
    categorize: bool = Query(
        default=True,
        description="Add QBO category suggestions to transactions",
    ),
    password:   Optional[str] = Query(
        default=None,
        description="Password to decrypt a password-protected PDF",
    ),
    _auth:      dict = Depends(require_api_key),  # validates key + increments by 1
):
    """
    Upload a single bank statement PDF and receive a QBO-compatible file.

    Requires a valid API key (``X-API-Key`` header).
    Counts as **1 conversion** against your monthly quota.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {os.getenv('MAX_UPLOAD_MB', 50)} MB.",
        )

    tmp_path = _save_upload(contents)
    try:
        statement = detect_and_parse(tmp_path, password=password)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    statement.transactions = _filter_by_date(statement.transactions, start_date, end_date)

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
@limiter.limit("10/minute")
async def batch_convert(
    request:    Request,
    files:      List[UploadFile] = File(..., description="One or more bank statement PDFs"),
    format:     Literal["ofx", "qfx", "csv"] = Query(default="ofx"),
    start_date: Optional[str]   = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str]   = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool             = Query(default=True),
    password:   Optional[str]   = Query(default=None, description="Password applied to all PDFs"),
):
    """
    Upload multiple PDFs at once (e.g. 12 months of statements).

    Each successfully parsed PDF counts as **1 conversion** against your quota.
    Transactions are merged, sorted, cross-statement duplicates removed, then
    exported as a single file.
    """
    # ── Auth: validate key + pre-check quota for the file count ───────────────
    api_key = request.headers.get("x-api-key", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Include it as the X-API-Key header.",
        )

    pdf_count = sum(
        1 for f in files
        if f.filename and f.filename.lower().endswith(".pdf")
    )
    # Pre-check (no increment yet) — ensures they can afford the whole batch
    validate_and_check_quota(api_key, count=max(1, pdf_count))

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per batch.")

    statements:   list        = []
    all_warnings: list[str]  = []
    tmp_paths:    list[Path] = []

    for upload in files:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            all_warnings.append(f"Skipped non-PDF: {upload.filename}")
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
        raise HTTPException(
            status_code=422,
            detail="No PDFs could be parsed. " + "; ".join(all_warnings),
        )

    # Increment by the number of files we actually parsed
    increment_usage(api_key, count=len(statements))

    # ── Merge + dedup ─────────────────────────────────────────────────────────
    warns: list[str] = []
    merged_txns = merge_statements(statements, warn=warns.append)
    all_warnings.extend(warns)
    merged_txns = _filter_by_date(merged_txns, start_date, end_date)

    if categorize:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        categorize_transactions(merged_txns, use_llm=use_llm)

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


# ── Batch preview (JSON, server-side dedup) ───────────────────────────────────

@app.post("/batch-preview")
@limiter.limit("10/minute")
async def batch_preview(
    request:    Request,
    files:      List[UploadFile] = File(..., description="One or more bank statement PDFs"),
    start_date: Optional[str]   = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str]   = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool             = Query(default=True, description="Add category suggestions"),
    password:   Optional[str]   = Query(default=None, description="Password for all PDFs"),
):
    """
    Upload multiple PDFs and get a **single merged JSON** response with all
    transactions de-duplicated server-side (same logic as ``/batch``).

    Unlike calling ``/preview`` per file and merging in the browser, this
    endpoint runs the full cross-statement dedup pipeline and returns one
    clean transaction list ready for the ReviewUI.

    Each successfully parsed PDF counts as **1 conversion** against quota.
    """
    # ── Auth: validate key + pre-check quota ─────────────────────────────────
    api_key = request.headers.get("x-api-key", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Include it as the X-API-Key header.",
        )
    pdf_count = sum(
        1 for f in files
        if f.filename and f.filename.lower().endswith(".pdf")
    )
    validate_and_check_quota(api_key, count=max(1, pdf_count))

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per batch.")

    statements:   list       = []
    all_warnings: list[str] = []
    tmp_paths:    list[Path] = []

    for upload in files:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            all_warnings.append(f"Skipped non-PDF: {upload.filename}")
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
        raise HTTPException(
            status_code=422,
            detail="No PDFs could be parsed. " + "; ".join(all_warnings),
        )

    # Charge quota
    increment_usage(api_key, count=len(statements))

    # ── Server-side merge + dedup ─────────────────────────────────────────────
    warns: list[str] = []
    merged_txns = merge_statements(statements, warn=warns.append)
    all_warnings.extend(warns)
    merged_txns = _filter_by_date(merged_txns, start_date, end_date)

    if categorize:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        categorize_transactions(merged_txns, use_llm=use_llm)

    # Build a representative merged account
    primary = statements[0].account
    merged_start = (
        min(s.account.statement_start for s in statements if s.account.statement_start)
        if any(s.account.statement_start for s in statements) else None
    )
    merged_end = (
        max(s.account.statement_end for s in statements if s.account.statement_end)
        if any(s.account.statement_end for s in statements) else None
    )

    return {
        "bank":              primary.bank_name,
        "account_id":        primary.account_id,
        "account_type":      str(primary.account_type),
        "statement_start":   str(merged_start),
        "statement_end":     str(merged_end),
        "file_count":        len(statements),
        "transaction_count": len(merged_txns),
        "total_debits":      float(sum(t.amount for t in merged_txns if t.amount < 0)),
        "total_credits":     float(sum(t.amount for t in merged_txns if t.amount > 0)),
        "parser_used":       "batch-preview",
        "warnings":          all_warnings,
        "transactions":      [_tx_to_dict(tx) for tx in merged_txns],
    }


# ── Statement preview (JSON) ──────────────────────────────────────────────────

@app.post("/preview")
@limiter.limit("20/minute")
async def preview(
    request:    Request,
    file:       UploadFile = File(..., description="Bank statement PDF"),
    start_date: Optional[str] = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str] = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool          = Query(default=True, description="Add category suggestions"),
    password:   Optional[str] = Query(default=None, description="Password for encrypted PDF"),
    _auth:      dict          = Depends(require_api_key),
):
    """
    Upload a PDF and get a JSON summary of extracted transactions.
    Used by the ReviewUI for previewing and editing before export.

    Counts as **1 conversion** against your monthly quota.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    contents = await file.read()
    tmp_path = _save_upload(contents)

    try:
        statement = detect_and_parse(tmp_path, password=password)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    statement.transactions = _filter_by_date(statement.transactions, start_date, end_date)

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
    bank:            str             = "Unknown"
    account_id:      str             = "unknown"
    account_type:    str             = "CHECKING"
    statement_start: Optional[str]  = None
    statement_end:   Optional[str]  = None
    closing_balance: Optional[float] = None
    transactions:    List[ExportTransaction]


@app.post("/export")
@limiter.limit("30/minute")
async def export_transactions(
    request: Request,
    req:     ExportRequest,
    _auth:   dict = Depends(verify_key_only),  # auth required but quota NOT incremented
):
    """
    Accept reviewed/edited transactions as JSON and return an OFX/QFX/CSV file.
    Called by the ReviewUI after the user has corrected any flagged transactions.

    Does **not** count against your conversion quota (no PDF is parsed here).
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


# ── Frontend SPA (must come last — catch-all overwrites nothing registered above) ──

_DIST = Path(__file__).parent / "frontend" / "dist"

if _DIST.exists():
    # Vite outputs all JS/CSS chunks under dist/assets/
    _assets_dir = _DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="vite-assets")

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon() -> Response:
        ico = _DIST / "favicon.ico"
        return FileResponse(ico) if ico.exists() else Response(status_code=204)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str = "") -> FileResponse:
        """Catch-all: return index.html so React Router handles client-side paths."""
        return FileResponse(_DIST / "index.html")
