"""
Statably REST API  v1.2
=========================

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
import hashlib as _hashlib
import os
import re
import tempfile
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from fastapi.security import APIKeyHeader as _APIKeyHeader

from src.auth import (
    PLANS,
    _KEY_HEADER as _AUTH_KEY_HEADER,
    check_and_increment,
    create_api_key,
    log_conversion,
    require_api_key,
    revoke_key,
    rotate_key,
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

# ── Sentry error tracking ──────────────────────────────────────────────────────
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration


def _sentry_before_send(event: dict, hint: dict) -> dict:
    """
    Strip local-variable values from every exception frame before sending to Sentry.

    send_default_pii=False does NOT suppress exception locals — they are
    included in stack frames and may contain parsed financial data (transaction
    amounts, bank account numbers, etc.).  This hook removes them entirely.
    """
    for exc_val in (event.get("exception") or {}).get("values") or []:
        for frame in (exc_val.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)   # local variable bindings
    # Also remove any user PII that might have been attached
    if event.get("user"):
        event["user"] = {}
    return event


_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.getenv("ENVIRONMENT", "development"),
        traces_sample_rate=0.2,   # 20 % of requests traced — adjust up in prod
        profiles_sample_rate=0.1,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        send_default_pii=False,
        before_send=_sentry_before_send,
    )

# ── Rate limiter ───────────────────────────────────────────────────────────────


def _rate_key(request: Request) -> str:
    """Rate-limit by API key when present, otherwise by client IP."""
    key = request.headers.get("x-api-key", "").strip()
    return f"key:{key}" if key else f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_key, headers_enabled=True)

# ── App ────────────────────────────────────────────────────────────────────────

_IS_PROD = os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod")

app = FastAPI(
    title="Statably",
    description=(
        "Convert bank statement PDFs to OFX/QFX/CSV files compatible with "
        "QuickBooks® and other accounting software. "
        "QuickBooks® is a registered trademark of Intuit Inc. "
        "Statably is not affiliated with or endorsed by Intuit Inc."
    ),
    version="1.2.0",
    # Disable auto-generated API docs in production to prevent schema reconnaissance.
    # Access /docs in development (ENVIRONMENT != production) only.
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
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
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ],
)


# ── Content-Security-Policy middleware ────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from starlette.requests import Request as _Request


class _CSPMiddleware(_BaseHTTPMiddleware):
    """Add Content-Security-Policy and other security headers to all responses."""
    async def dispatch(self, request: _Request, call_next):
        response = await call_next(request)
        # Restrictive CSP: prevents XSS from financial data rendered in DOM.
        # The SPA loads its own assets from 'self'; Sentry uses worker-src.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "                    # no unsafe-inline; Vite prod build has no inline scripts
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self' https://*.sentry.io https://api.stripe.com; "
            "frame-src https://js.stripe.com; "
            "worker-src blob:; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(_CSPMiddleware)

# ── Auth dependency that returns the raw key string ─────────────────────────
def _require_raw_api_key(api_key: str = Security(_AUTH_KEY_HEADER)) -> str:
    """
    FastAPI dependency: validates presence of X-API-Key header and returns the
    raw key string.  Used by batch endpoints that need the raw key for quota
    management while using the standard FastAPI Depends/Security pattern.
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Include it as the X-API-Key header.",
        )
    return api_key


# ── Shared helpers ────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024


_PDF_MAGIC = b"%PDF-"


def _validate_pdf_bytes(contents: bytes, filename: str) -> None:
    """
    Raise HTTP 400 if *contents* is not a valid PDF.

    Checks:
      1. File extension is .pdf (case-insensitive).
      2. Magic bytes start with '%PDF-' — rejects renamed executables and
         other files that could exploit pdfplumber's underlying parsers.
    """
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    if not contents[:5] == _PDF_MAGIC:
        raise HTTPException(
            status_code=400,
            detail="File does not appear to be a valid PDF (missing %PDF- header).",
        )


def _save_upload(contents: bytes) -> Path:
    """Write validated PDF bytes to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(contents)
    tmp.close()
    return Path(tmp.name)


def _filter_by_date(
    txns: list[Transaction],
    start: Optional[str],
    end: Optional[str],
) -> list[Transaction]:
    """
    Filter transactions to the given inclusive date range.

    Raises HTTP 400 for dates that are not valid ISO-8601 (YYYY-MM-DD).
    Silently ignoring bad dates would produce confusing empty result-sets;
    an explicit error tells the caller exactly what went wrong.
    """
    if not start and not end:
        return txns
    try:
        s = date_type.fromisoformat(start) if start else None
        e = date_type.fromisoformat(end)   if end   else None
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format ({exc}). Use YYYY-MM-DD (e.g. 2024-01-31).",
        )
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

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "statably", "version": "1.2.0"}


@app.get("/api/banks")
def banks():
    return {"supported_banks": list_supported_banks()}


# ── Auth: register  (public, rate-limited per IP) ─────────────────────────────

class RegisterRequest(BaseModel):
    email:        str
    company_name: Optional[str] = None  # accountant / firm name (optional)


@app.post("/api/auth/register", status_code=201)
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
    # api_key intentionally omitted — credentials must never appear in request bodies


def _sanitize_report_field(value: str, max_len: int = 200) -> str:
    """Strip HTML tags, normalise whitespace, and enforce a max length."""
    cleaned = re.sub(r"<[^>]+>", "", value)   # strip HTML
    cleaned = " ".join(cleaned.split())         # collapse whitespace
    return cleaned[:max_len]


@app.post("/api/report-error", status_code=200)
@limiter.limit("10/hour")
def report_error(request: Request, body: ErrorReportRequest):
    """
    Accept a user-submitted parsing error report and forward it to support.
    No authentication required — we want to hear from free-tier users too.
    """
    description = _sanitize_report_field(body.description, max_len=2000)
    bank        = _sanitize_report_field(body.bank,        max_len=100)
    email       = body.email.strip()[:254]

    if len(description) < 10:
        raise HTTPException(status_code=422, detail="Please describe the issue (10+ characters).")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=422, detail="Invalid email address.")

    send_parsing_error_report(
        user_email=email,
        bank=bank,
        description=description,
        api_key="",  # never forward credentials to email
    )
    return {
        "received": True,
        "message":  "Thanks — we'll investigate and improve the parser.",
    }


# ── Auth: usage info ──────────────────────────────────────────────────────────

@app.get("/api/auth/usage")
@limiter.limit("10/minute")
def usage(request: Request, record: dict = Depends(verify_key_only)):
    """Return the current plan, usage counter, and remaining quota for the key."""
    plan_info = PLANS.get(record["plan"], PLANS["free"])
    limit     = plan_info["monthly_limit"]
    used      = record["conversions_used"]
    days_into_period = (date_type.today() - date_type.fromisoformat(record["period_start"])).days
    return {
        "plan":                  record["plan"],
        "plan_label":            plan_info["label"],
        "monthly_limit":         limit,
        "conversions_used":      used,
        "conversions_remaining": (limit - used) if limit is not None else None,
        "period_start":          record["period_start"],
        "period_resets_in_days": max(0, 30 - days_into_period),
        "status":                record["status"],
        # stripe_customer_id intentionally omitted — internal infrastructure ID
    }


# ── Auth: Stripe checkout ─────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan:        Literal["starter", "pro"]
    success_url: str
    cancel_url:  str


@app.post("/api/auth/checkout")
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
    # Pass the raw key from the header (not the hash from the record)
    # so billing.py can store an opaque session token against it.
    raw_key = request.headers.get("x-api-key", "").strip()
    url = create_checkout_session(
        raw_key=raw_key,
        email=record["email"],
        plan=body.plan,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        allowed_origins=_allowed_origins,
    )
    return {"checkout_url": url, "plan": body.plan}


# ── Auth: revoke key ─────────────────────────────────────────────────────────

class RevokeRequest(BaseModel):
    confirm: bool = False  # must be True to prevent accidental revocation


@app.post("/api/auth/revoke", status_code=200)
@limiter.limit("5/hour")
def revoke(
    request: Request,
    body:    RevokeRequest,
    record:  dict = Depends(verify_key_only),
):
    """
    Permanently revoke the authenticated API key.

    This action is irreversible — the key cannot be restored.
    Set ``confirm: true`` in the request body to proceed.
    Any active Stripe subscription must be cancelled separately via your
    billing portal before revoking.

    After revocation, register a new key at ``POST /auth/register``,
    or use ``POST /auth/rotate`` to atomically create a replacement key
    that inherits your existing subscription.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Set "confirm": true to confirm key revocation. This action is permanent.',
        )
    api_key = request.headers.get("x-api-key", "").strip()
    revoke_key(api_key)
    return {
        "revoked": True,
        "message": (
            "API key has been permanently revoked. "
            "Register a new key at POST /auth/register."
        ),
    }


# ── Auth: rotate key ──────────────────────────────────────────────────────────

@app.post("/api/auth/rotate", status_code=200)
@limiter.limit("3/hour")
def rotate(
    request: Request,
    record:  dict = Depends(verify_key_only),
):
    """
    Atomically rotate the authenticated API key.

    Generates a new key, transfers the existing plan and Stripe subscription
    to it, and immediately revokes the old key.  The new key is returned once
    and never stored — save it securely.

    Use this when a key is suspected compromised.  Unlike revoke + re-register,
    rotation preserves your subscription and usage history.
    """
    api_key = request.headers.get("x-api-key", "").strip()
    new_key = rotate_key(api_key)
    return {
        "api_key": new_key,
        "plan":    record["plan"],
        "message": (
            "Old key revoked. Save this new key — it will not be shown again. "
            "Your subscription and usage history have been transferred."
        ),
    }


# ── Stripe webhook  (called by Stripe, not the frontend) ─────────────────────

@app.post("/api/webhook/stripe", include_in_schema=False)
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

@app.post("/api/convert")
@limiter.limit("20/minute")
async def convert(
    request:    Request,
    file:       UploadFile = File(..., description="Bank statement PDF"),
    format:     Literal["ofx", "qfx", "csv"] = Query(
        default="ofx",
        description="Output format: ofx (QuickBooks® import), qfx (Quicken®), csv",
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
    _auth:      dict = Depends(require_api_key),  # validates key + increments by 1
):
    """
    Upload a single bank statement PDF and receive a QBO-compatible file.

    Requires a valid API key (``X-API-Key`` header).
    Counts as **1 conversion** against your monthly quota.

    For password-protected PDFs send the password in the ``X-PDF-Password``
    request header (not as a query parameter, to keep it out of server logs).
    """
    # PDF password — read from header to keep it out of URL / server logs
    password = request.headers.get("x-pdf-password") or None

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {os.getenv('MAX_UPLOAD_MB', 50)} MB.",
        )
    _validate_pdf_bytes(contents, file.filename or "")

    tmp_path = _save_upload(contents)
    try:
        statement = detect_and_parse(tmp_path, password=password)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not parse the PDF. Check that it is a supported bank "
                "statement and is not corrupted. If the problem persists, use "
                "POST /report-error to send us a report."
            ),
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    # Audit-log every successful conversion (key hash, bank, parser, tx count)
    raw_key = request.headers.get("x-api-key", "").strip()
    log_conversion(raw_key, statement.account.bank_name, statement.parser_used,
                   len(statement.transactions),
                   file_hash=_hashlib.sha256(contents).hexdigest())

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

@app.post("/api/batch")
@limiter.limit("10/minute")
async def batch_convert(
    request:    Request,
    files:      List[UploadFile] = File(..., description="One or more bank statement PDFs"),
    format:     Literal["ofx", "qfx", "csv"] = Query(default="ofx"),
    start_date: Optional[str]   = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str]   = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool             = Query(default=True),
    api_key:    str              = Depends(_require_raw_api_key),
):
    """
    Upload multiple PDFs at once (e.g. 12 months of statements).

    Each successfully parsed PDF counts as **1 conversion** against your quota.
    Transactions are merged, sorted, cross-statement duplicates removed, then
    exported as a single file.

    For password-protected PDFs send the password in the ``X-PDF-Password``
    request header (applied to all files in the batch).
    """
    # PDF password — header only, never a URL query parameter
    password = request.headers.get("x-pdf-password") or None

    # Early count check before reading files into memory
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per batch.")

    pdf_count = sum(
        1 for f in files
        if f.filename and f.filename.lower().endswith(".pdf")
    )
    # Pre-check (no increment yet) — fast fail if quota already exhausted
    validate_and_check_quota(api_key, count=max(1, pdf_count))

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    statements:   list        = []
    all_warnings: list[str]  = []
    tmp_paths:    list[Path] = []

    for upload in files:
        fname = upload.filename or ""
        if not fname.lower().endswith(".pdf"):
            all_warnings.append(f"Skipped non-PDF: {fname}")
            continue

        contents = await upload.read()
        if len(contents) > _MAX_UPLOAD_BYTES:
            all_warnings.append(f"Skipped oversized file: {fname}")
            continue
        if not contents[:5] == _PDF_MAGIC:
            all_warnings.append(f"Skipped invalid PDF (bad magic bytes): {fname}")
            continue

        tmp_path = _save_upload(contents)
        tmp_paths.append(tmp_path)

        try:
            stmt = detect_and_parse(tmp_path, password=password)
            statements.append(stmt)
            all_warnings.extend([f"{fname}: {w}" for w in stmt.warnings])
        except Exception:
            all_warnings.append(f"Failed to parse {fname}: could not extract transactions.")
        # Note: tmp_path cleanup is in the finally-like loop below;
        # the path is already appended, so it will be cleaned regardless.

    for p in tmp_paths:
        p.unlink(missing_ok=True)

    if not statements:
        raise HTTPException(
            status_code=422,
            detail="No PDFs could be parsed. " + "; ".join(all_warnings),
        )

    # Atomically re-check quota and increment for the files we actually parsed.
    # This closes the TOCTOU race between the pre-check at the top and now.
    check_and_increment(api_key, count=len(statements))

    # Audit-log the batch operation (one entry summarising all files parsed)
    primary_bank = statements[0].account.bank_name if statements else "Unknown"
    log_conversion(api_key, primary_bank, "batch",
                   sum(len(s.transactions) for s in statements))

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

@app.post("/api/batch-preview")
@limiter.limit("10/minute")
async def batch_preview(
    request:    Request,
    files:      List[UploadFile] = File(..., description="One or more bank statement PDFs"),
    start_date: Optional[str]   = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str]   = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool             = Query(default=True, description="Add category suggestions"),
    api_key:    str              = Depends(_require_raw_api_key),
):
    """
    Upload multiple PDFs and get a **single merged JSON** response with all
    transactions de-duplicated server-side (same logic as ``/batch``).

    Unlike calling ``/preview`` per file and merging in the browser, this
    endpoint runs the full cross-statement dedup pipeline and returns one
    clean transaction list ready for the ReviewUI.

    Each successfully parsed PDF counts as **1 conversion** against quota.
    For password-protected PDFs send the password in ``X-PDF-Password`` header.
    """
    password = request.headers.get("x-pdf-password") or None

    # Early count check before reading files into memory
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per batch.")

    pdf_count = sum(
        1 for f in files
        if f.filename and f.filename.lower().endswith(".pdf")
    )
    validate_and_check_quota(api_key, count=max(1, pdf_count))

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    statements:   list       = []
    all_warnings: list[str] = []
    tmp_paths:    list[Path] = []

    for upload in files:
        fname = upload.filename or ""
        if not fname.lower().endswith(".pdf"):
            all_warnings.append(f"Skipped non-PDF: {fname}")
            continue
        contents = await upload.read()
        if len(contents) > _MAX_UPLOAD_BYTES:
            all_warnings.append(f"Skipped oversized file: {fname}")
            continue
        if not contents[:5] == _PDF_MAGIC:
            all_warnings.append(f"Skipped invalid PDF (bad magic bytes): {fname}")
            continue
        tmp_path = _save_upload(contents)
        tmp_paths.append(tmp_path)
        try:
            stmt = detect_and_parse(tmp_path, password=password)
            statements.append(stmt)
            all_warnings.extend([f"{fname}: {w}" for w in stmt.warnings])
        except Exception:
            # Generic message — never expose internal exception details in API responses.
            all_warnings.append(f"Failed to parse {fname}: could not extract transactions.")

    for p in tmp_paths:
        p.unlink(missing_ok=True)

    if not statements:
        raise HTTPException(
            status_code=422,
            detail="No PDFs could be parsed. " + "; ".join(all_warnings),
        )

    # Atomically re-check quota and increment for the files we actually parsed.
    check_and_increment(api_key, count=len(statements))

    # Audit-log the batch-preview operation
    preview_bank = statements[0].account.bank_name if statements else "Unknown"
    log_conversion(api_key, preview_bank, "batch-preview",
                   sum(len(s.transactions) for s in statements))

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
        "account_type":      primary.account_type.value if hasattr(primary.account_type, "value") else str(primary.account_type),
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

@app.post("/api/preview")
@limiter.limit("20/minute")
async def preview(
    request:    Request,
    file:       UploadFile = File(..., description="Bank statement PDF"),
    start_date: Optional[str] = Query(default=None, description="Filter start date YYYY-MM-DD"),
    end_date:   Optional[str] = Query(default=None, description="Filter end date YYYY-MM-DD"),
    categorize: bool          = Query(default=True, description="Add category suggestions"),
    _auth:      dict          = Depends(require_api_key),
):
    """
    Upload a PDF and get a JSON summary of extracted transactions.
    Used by the ReviewUI for previewing and editing before export.

    Counts as **1 conversion** against your monthly quota.
    For password-protected PDFs send the password in ``X-PDF-Password`` header.
    """
    password = request.headers.get("x-pdf-password") or None

    contents = await file.read()

    # Size + magic-byte checks must happen BEFORE writing to disk
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {os.getenv('MAX_UPLOAD_MB', 50)} MB.",
        )
    _validate_pdf_bytes(contents, file.filename or "")

    tmp_path = _save_upload(contents)

    try:
        statement = detect_and_parse(tmp_path, password=password)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not parse the PDF. Check that it is a supported bank "
                "statement and is not corrupted. If the problem persists, use "
                "POST /report-error to send us a report."
            ),
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    # Audit-log every successful preview (same schema as /convert)
    raw_key = request.headers.get("x-api-key", "").strip()
    log_conversion(raw_key, statement.account.bank_name, statement.parser_used,
                   len(statement.transactions),
                   file_hash=_hashlib.sha256(contents).hexdigest())

    statement.transactions = _filter_by_date(statement.transactions, start_date, end_date)

    if categorize:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        categorize_transactions(statement.transactions, use_llm=use_llm)

    return {
        "bank":              statement.account.bank_name,
        "account_id":        statement.account.account_id,
        "account_type":      statement.account.account_type.value if hasattr(statement.account.account_type, "value") else str(statement.account.account_type),
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


@app.post("/api/export")
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
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Invalid transaction data. Check date formats (YYYY-MM-DD) and amounts.",
        )

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

    # Audit log — export events are tracked even though no quota is consumed.
    raw_key = request.headers.get("x-api-key", "").strip()
    log_conversion(raw_key, req.bank, "export", len(txns))

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


# ── /v1/ versioned API router ─────────────────────────────────────────────────
# Mount all existing routes under /v1/ so Intuit-marketplace integrations and
# partner integrations have a stable, version-tagged base URL.
# The un-prefixed routes above remain for backwards compatibility.
from fastapi import APIRouter as _APIRouter

_v1 = _APIRouter(prefix="/v1")

# Re-register all auth + conversion routes under /v1/
_v1.add_api_route("/health",          health,              methods=["GET"])
_v1.add_api_route("/banks",           banks,               methods=["GET"])
_v1.add_api_route("/auth/register",   register,            methods=["POST"], status_code=201)
_v1.add_api_route("/auth/usage",      usage,               methods=["GET"])
_v1.add_api_route("/auth/checkout",   checkout,            methods=["POST"])
_v1.add_api_route("/auth/revoke",     revoke,              methods=["POST"])
_v1.add_api_route("/auth/rotate",     rotate,              methods=["POST"])
_v1.add_api_route("/convert",         convert,             methods=["POST"])
_v1.add_api_route("/preview",         preview,             methods=["POST"])
_v1.add_api_route("/batch",           batch_convert,       methods=["POST"])
_v1.add_api_route("/batch-preview",   batch_preview,       methods=["POST"])
_v1.add_api_route("/export",          export_transactions, methods=["POST"])
_v1.add_api_route("/report-error",    report_error,        methods=["POST"])

app.include_router(_v1)

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

    @app.get("/favicon.svg", include_in_schema=False)
    async def _favicon_svg() -> Response:
        svg = _DIST / "favicon.svg"
        return FileResponse(svg, media_type="image/svg+xml") if svg.exists() else Response(status_code=204)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str = "") -> FileResponse:
        """Catch-all: return index.html so React Router handles client-side paths."""
        return FileResponse(_DIST / "index.html")
