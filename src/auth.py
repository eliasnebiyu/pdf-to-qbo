"""
API key authentication, SQLite storage, and usage metering.

Tiers
-----
free    :  10 conversions / 30-day rolling period  (no Stripe required)
starter : 100 conversions / 30-day rolling period  ($9/month)
pro     :  unlimited                               ($29/month)

Database
--------
SQLite at DB_PATH env var (default: data/api_keys.db).
WAL mode is enabled for better write concurrency under uvicorn workers.

Admin bypass
------------
Set ADMIN_API_KEY=<secret> in the environment.  Any request carrying that
key skips quota checks entirely — useful for testing and seeding data.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

# Process-level lock: makes quota check + increment atomic for single-process deploys.
# For multi-process (multiple uvicorn workers / Railway replicas) upgrade to
# a PostgreSQL advisory lock or Redis INCR+EXPIRE once you need horizontal scale.
_quota_lock = threading.Lock()


def _hash_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw API key.  This is what we store in the DB."""
    return hashlib.sha256(raw_key.encode()).hexdigest()

# ── Configuration ──────────────────────────────────────────────────────────────

_DB_PATH = Path(os.getenv("DB_PATH", "data/api_keys.db"))

PLANS: dict[str, dict] = {
    "free":    {"monthly_limit": 10,   "label": "Free"},
    "starter": {"monthly_limit": 100,  "label": "Starter ($9/mo)"},
    "pro":     {"monthly_limit": None, "label": "Pro ($29/mo)"},  # None = unlimited
}

_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# ── Database ───────────────────────────────────────────────────────────────────

def _ensure_db() -> None:
    """Create the DB file and schema if they don't already exist."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS api_keys (
                key                    TEXT PRIMARY KEY,
                email                  TEXT NOT NULL,
                plan                   TEXT NOT NULL DEFAULT 'free',
                stripe_customer_id     TEXT,
                stripe_subscription_id TEXT,
                stripe_session_token   TEXT,
                status                 TEXT NOT NULL DEFAULT 'active',
                conversions_used       INTEGER NOT NULL DEFAULT 0,
                period_start           TEXT NOT NULL,
                created_at             TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stripe_sub
                ON api_keys (stripe_subscription_id);
            CREATE INDEX IF NOT EXISTS idx_email
                ON api_keys (email);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_token
                ON api_keys (stripe_session_token)
                WHERE stripe_session_token IS NOT NULL;
            CREATE TABLE IF NOT EXISTS conversion_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                key_hash        TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                bank_name       TEXT,
                parser_used     TEXT,
                transaction_count INTEGER,
                file_hash       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_log_key
                ON conversion_log (key_hash);
            CREATE TABLE IF NOT EXISTS qbo_tokens (
                key_hash                  TEXT PRIMARY KEY,
                realm_id                  TEXT NOT NULL,
                access_token              TEXT NOT NULL,
                refresh_token             TEXT NOT NULL,
                access_token_expires_at   TEXT NOT NULL,
                refresh_token_expires_at  TEXT NOT NULL,
                created_at                TEXT NOT NULL,
                updated_at                TEXT NOT NULL
            );
        """)


@contextmanager
def _get_conn():
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ── Key lifecycle ──────────────────────────────────────────────────────────────

def create_api_key(email: str, plan: str = "free") -> str:
    """
    Generate a new API key, persist its SHA-256 hash, and return the raw key.

    The raw key is returned exactly once and is never written to persistent
    storage.  All subsequent lookups hash the incoming key before querying.

    Raises HTTPException 409 if an active key already exists for this email.
    """
    _ensure_db()
    email = email.lower().strip()
    # Prevent duplicate registrations: check for existing active key
    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT key FROM api_keys WHERE email = ? AND status = 'active' LIMIT 1",
            (email,),
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An active API key already exists for this email address. "
                    "Check your inbox for the original key, or contact support@statably.org "
                    "to rotate your key."
                ),
            )

    raw_key  = "lf_" + secrets.token_hex(24)   # 51-char key
    key_hash = _hash_key(raw_key)
    now      = datetime.now(timezone.utc)
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO api_keys
               (key, email, plan, status, conversions_used, period_start, created_at)
               VALUES (?, ?, ?, 'active', 0, ?, ?)""",
            (key_hash, email, plan,
             now.date().isoformat(), now.isoformat()),
        )
    return raw_key


def get_key_record(raw_key: str) -> Optional[dict]:
    """
    Return the DB row for *raw_key* as a plain dict, or None if not found.
    The key is hashed before the DB lookup — the hash is what we store.
    """
    _ensure_db()
    key_hash = _hash_key(raw_key)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key = ?", (key_hash,)
        ).fetchone()
        return dict(row) if row else None

# ── Plan management (called by billing.py webhook handlers) ───────────────────

def update_plan(
    key: str,
    plan: str,
    customer_id: str | None = None,
    subscription_id: str | None = None,
) -> None:
    """
    Upgrade / downgrade a key's plan.

    *key* is the value from Stripe session metadata — which is record["key"]
    (the SHA-256 hash) placed there by create_checkout_session.  We therefore
    use it directly as the DB key without re-hashing.
    """
    _ensure_db()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE api_keys
               SET plan = ?,
                   stripe_customer_id     = COALESCE(?, stripe_customer_id),
                   stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                   status = 'active'
               WHERE key = ?""",
            (plan, customer_id, subscription_id, key),
        )


def suspend_by_subscription(subscription_id: str) -> None:
    _ensure_db()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE api_keys SET status = 'suspended' WHERE stripe_subscription_id = ?",
            (subscription_id,),
        )


def reactivate_by_subscription(subscription_id: str) -> None:
    _ensure_db()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE api_keys SET status = 'active' WHERE stripe_subscription_id = ?",
            (subscription_id,),
        )


def cancel_by_subscription(subscription_id: str) -> None:
    """Downgrade to free and clear Stripe IDs when a subscription is deleted."""
    _ensure_db()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE api_keys
               SET status = 'cancelled', plan = 'free',
                   stripe_subscription_id = NULL
               WHERE stripe_subscription_id = ?""",
            (subscription_id,),
        )


def store_session_token(raw_key: str, token: str) -> None:
    """
    Associate a Stripe checkout session token with an API key.

    The token (an opaque random UUID) is stored in Stripe session metadata
    instead of the key hash, so no credential-derived value reaches Stripe.
    """
    _ensure_db()
    key_hash = _hash_key(raw_key)
    with _get_conn() as conn:
        conn.execute(
            "UPDATE api_keys SET stripe_session_token = ? WHERE key = ?",
            (token, key_hash),
        )


def get_key_hash_for_session(token: str) -> Optional[str]:
    """Return the key hash associated with a Stripe session token, or None."""
    _ensure_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT key FROM api_keys WHERE stripe_session_token = ?", (token,)
        ).fetchone()
        return row["key"] if row else None


def rotate_by_email(email: str) -> Optional[str]:
    """
    Rotate the active API key for a given email address.

    Used in the account-recovery flow where the user has lost their key.
    Returns the new raw key if an active key existed, or None if no key
    was found for this email.  The old key is immediately revoked.
    """
    _ensure_db()
    email = email.lower().strip()
    with _get_conn() as conn:
        old = conn.execute(
            "SELECT * FROM api_keys WHERE email = ? AND status = 'active' LIMIT 1",
            (email,),
        ).fetchone()
        if not old:
            return None

        new_raw  = "lf_" + secrets.token_hex(24)
        new_hash = _hash_key(new_raw)
        now      = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO api_keys
               (key, email, plan, stripe_customer_id, stripe_subscription_id,
                status, conversions_used, period_start, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (new_hash, old["email"], old["plan"],
             old["stripe_customer_id"], old["stripe_subscription_id"],
             old["conversions_used"], old["period_start"], now),
        )
        conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE key = ?", (old["key"],)
        )
    return new_raw


def rotate_key(raw_key: str) -> str:
    """
    Atomically generate a new API key and revoke the old one.

    All subscription data (plan, Stripe IDs, usage) is transferred to the
    new key.  The old key is immediately marked 'revoked'.

    Returns the new raw key (shown once; never stored).
    """
    _ensure_db()
    old_hash = _hash_key(raw_key)
    with _get_conn() as conn:
        old = conn.execute(
            "SELECT * FROM api_keys WHERE key = ?", (old_hash,)
        ).fetchone()
        if not old:
            raise HTTPException(status_code=401, detail="Invalid API key.")
        if old["status"] == "revoked":
            raise HTTPException(status_code=401, detail="API key is already revoked.")

        new_raw  = "lf_" + secrets.token_hex(24)
        new_hash = _hash_key(new_raw)
        now      = datetime.now(timezone.utc).isoformat()

        # Insert new key with the same plan and Stripe subscription
        conn.execute(
            """INSERT INTO api_keys
               (key, email, plan, stripe_customer_id, stripe_subscription_id,
                status, conversions_used, period_start, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (new_hash, old["email"], old["plan"],
             old["stripe_customer_id"], old["stripe_subscription_id"],
             old["conversions_used"], old["period_start"], now),
        )
        # Revoke the old key
        conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE key = ?", (old_hash,)
        )
    return new_raw


def revoke_key(raw_key: str) -> bool:
    """
    Permanently revoke an API key.

    Sets the key's status to 'revoked' so all future requests with that key
    will be rejected with 401.  Returns True if a row was updated, False if
    the key did not exist.

    This does NOT cancel any associated Stripe subscription — do that
    separately via Stripe's API or dashboard before revoking the key.
    """
    _ensure_db()
    key_hash = _hash_key(raw_key)
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE key = ?",
            (key_hash,),
        )
        return cur.rowcount > 0

def log_conversion(
    raw_key: str,
    bank_name: str = "",
    parser_used: str = "",
    transaction_count: int = 0,
    file_hash: str = "",
) -> None:
    """
    Write an audit record for a completed conversion.

    Stores: key hash, timestamp, bank name, parser, transaction count, and
    a hash of the PDF (not the content) for provenance.  Never stores PDF
    contents or parsed financial data.
    """
    _ensure_db()
    key_hash = _hash_key(raw_key)
    now      = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO conversion_log
               (key_hash, timestamp, bank_name, parser_used, transaction_count, file_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key_hash, now, bank_name, parser_used, transaction_count, file_hash),
        )


# ── Usage metering ─────────────────────────────────────────────────────────────

def _maybe_reset_period(record: dict) -> dict:
    """
    Reset the monthly counter when 30+ days have elapsed since period_start.
    Updates the DB row in place and returns the refreshed record dict.
    """
    today        = date.today()
    period_start = date.fromisoformat(record["period_start"])
    if (today - period_start).days >= 30:
        with _get_conn() as conn:
            conn.execute(
                "UPDATE api_keys SET conversions_used = 0, period_start = ? WHERE key = ?",
                (today.isoformat(), record["key"]),
            )
        return {**record, "conversions_used": 0, "period_start": today.isoformat()}
    return record


def increment_usage(key: str, count: int = 1) -> None:
    """
    Increment usage counter by *count* with NO quota check.

    *key* must be the raw API key (prefix 'lf_').  It is always hashed
    before the DB update — passing an already-hashed value is not supported
    and will simply produce a hash-of-hash lookup that finds nothing.
    """
    key_hash = _hash_key(key)
    with _get_conn() as conn:
        conn.execute(
            "UPDATE api_keys SET conversions_used = conversions_used + ? WHERE key = ?",
            (count, key_hash),
        )


def validate_and_check_quota(key: str, count: int = 1) -> dict:
    """
    Validate the key, check (but do NOT increment) quota for *count* conversions.

    Raises HTTPException (401 / 402 / 429) on failure.
    Returns the (possibly period-reset) record dict on success.
    """
    # Admin bypass — timing-safe comparison prevents timing oracle attacks
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if admin_key and hmac.compare_digest(key, admin_key):
        return {
            "key": key, "email": "admin", "plan": "pro", "status": "active",
            "conversions_used": 0, "monthly_limit": None,
            "period_start": date.today().isoformat(),
            "stripe_customer_id": None, "stripe_subscription_id": None,
        }

    _ensure_db()
    record = get_key_record(key)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid API key.")

    record = _maybe_reset_period(record)

    if record["status"] != "active":
        raise HTTPException(
            status_code=402,
            detail=(
                f"API key is {record['status']}. "
                "Check your subscription status at GET /auth/usage."
            ),
        )

    plan_info = PLANS.get(record["plan"], PLANS["free"])
    limit     = plan_info["monthly_limit"]
    if limit is not None:
        remaining = limit - record["conversions_used"]
        if count > remaining:
            # 402 Payment Required (not 429) — this is a billing/entitlement limit,
            # not a rate limit. Using 429 causes API clients and Zapier to retry
            # indefinitely, wasting resources.
            days_left = 30 - (date.today() - date.fromisoformat(record["period_start"])).days
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly quota exceeded. "
                    f"You have {remaining} conversion(s) remaining on the "
                    f"{plan_info['label']} plan ({limit}/month). "
                    f"Quota resets in approximately {max(0, days_left)} day(s). "
                    "Upgrade your plan at POST /auth/checkout."
                ),
                headers={"Retry-After": str(max(0, days_left) * 86400)},
            )
    return record


def check_and_increment(key: str, count: int = 1) -> dict:
    """
    Atomically validate, quota-check, and increment in one locked section.

    The _quota_lock ensures that two concurrent requests with the same key
    cannot both pass the quota check before either has incremented the counter
    (race condition that would allow double-spending quota).
    """
    with _quota_lock:
        record = validate_and_check_quota(key, count)
        # Admin bypass already returned above — skip increment for admin
        if record.get("email") != "admin":
            increment_usage(key, count)
            record = {**record, "conversions_used": record["conversions_used"] + count}
    return record

# ── FastAPI dependencies ───────────────────────────────────────────────────────

def require_api_key(api_key: str = Security(_KEY_HEADER)) -> dict:
    """
    FastAPI dependency for single-conversion endpoints.
    Validates the X-API-Key header and increments usage by 1.
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail=(
                "API key required. Add the header:  X-API-Key: <your-key>  "
                "Get a free key at POST /auth/register."
            ),
        )
    return check_and_increment(api_key, count=1)


def verify_key_only(api_key: str = Security(_KEY_HEADER)) -> dict:
    """
    FastAPI dependency for info/checkout endpoints.
    Validates the key without touching the usage counter.
    Supports the ADMIN_API_KEY bypass so admin can use all endpoints.
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required.")
    # Admin bypass — timing-safe comparison
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if admin_key and hmac.compare_digest(api_key, admin_key):
        return {
            "key": api_key, "email": "admin", "plan": "pro", "status": "active",
            "conversions_used": 0, "monthly_limit": None,
            "period_start": date.today().isoformat(),
            "stripe_customer_id": None, "stripe_subscription_id": None,
        }
    _ensure_db()
    record = get_key_record(api_key)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    if record["status"] not in ("active",):
        raise HTTPException(
            status_code=402,
            detail=(
                f"API key is {record['status']}. "
                "Check your subscription or register a new key at POST /auth/register."
            ),
        )
    return _maybe_reset_period(record)


# ── QBO token encryption ───────────────────────────────────────────────────────
# Tokens (access_token, refresh_token) are live OAuth credentials; if the
# SQLite file is extracted they would grant immediate QBO access.  We encrypt
# them with Fernet (AES-128-CBC + HMAC-SHA256) keyed to TOKEN_ENCRYPTION_KEY.
#
# To generate a key:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Set that value as TOKEN_ENCRYPTION_KEY in your Railway environment.
#
# If the env var is absent, tokens are stored in plaintext (backward compat for
# local dev without a key).  Set TOKEN_ENCRYPTION_KEY in production.

class _TokenDecryptionError(Exception):
    """Raised when a stored token cannot be decrypted (e.g. after key rotation)."""


@lru_cache(maxsize=1)
def _get_fernet():
    """Return a Fernet instance, or None if TOKEN_ENCRYPTION_KEY is not set."""
    key = os.getenv("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:
        import logging
        logging.getLogger("statably.auth").error(
            "Invalid TOKEN_ENCRYPTION_KEY — QBO tokens will not be encrypted: %s", exc
        )
        return None


def _enc(value: str) -> str:
    """Encrypt *value*; returns plaintext when no key is configured."""
    f = _get_fernet()
    return f.encrypt(value.encode()).decode() if f else value


def _dec(value: str) -> str:
    """
    Decrypt *value*.

    Raises _TokenDecryptionError when TOKEN_ENCRYPTION_KEY is set but the
    stored value cannot be decrypted (e.g. plaintext token from before
    encryption was enabled, or after a key rotation).  Callers should treat
    this as "QBO disconnected" and delete the stale record.
    """
    f = _get_fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        raise _TokenDecryptionError(
            "QBO token decryption failed — token may have been stored before "
            "TOKEN_ENCRYPTION_KEY was set. User must reconnect QBO."
        )


# ── QBO token storage ──────────────────────────────────────────────────────────

def store_qbo_tokens(key_hash: str, token_data: dict) -> None:
    """
    Upsert the QBO OAuth tokens for the given key_hash.

    token_data must contain: realm_id, access_token, refresh_token,
    access_token_expires_at, refresh_token_expires_at.
    """
    _ensure_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO qbo_tokens
                (key_hash, realm_id, access_token, refresh_token,
                 access_token_expires_at, refresh_token_expires_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_hash) DO UPDATE SET
                realm_id                 = excluded.realm_id,
                access_token             = excluded.access_token,
                refresh_token            = excluded.refresh_token,
                access_token_expires_at  = excluded.access_token_expires_at,
                refresh_token_expires_at = excluded.refresh_token_expires_at,
                updated_at               = excluded.updated_at
            """,
            (
                key_hash,
                token_data["realm_id"],
                _enc(token_data["access_token"]),
                _enc(token_data["refresh_token"]),
                token_data["access_token_expires_at"],
                token_data["refresh_token_expires_at"],
                now,
                now,
            ),
        )


def get_qbo_tokens(key_hash: str) -> Optional[dict]:
    """
    Return the stored QBO token record for key_hash, or None.

    Returns None (and deletes the stale DB row) when TOKEN_ENCRYPTION_KEY is
    set but the stored tokens cannot be decrypted — this happens after the key
    is first configured while plaintext tokens already exist.  The user will
    need to reconnect QBO.
    """
    _ensure_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM qbo_tokens WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        if not row:
            return None
        record = dict(row)
    try:
        record["access_token"]  = _dec(record["access_token"])
        record["refresh_token"] = _dec(record["refresh_token"])
    except _TokenDecryptionError:
        delete_qbo_tokens(key_hash)
        return None
    return record


def update_qbo_tokens(key_hash: str, partial: dict) -> None:
    """
    Update only the fields present in partial (used after a token refresh).

    partial may contain: access_token, access_token_expires_at,
    refresh_token, refresh_token_expires_at.
    """
    _ensure_db()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE qbo_tokens SET
                access_token             = ?,
                access_token_expires_at  = ?,
                refresh_token            = COALESCE(?, refresh_token),
                refresh_token_expires_at = COALESCE(?, refresh_token_expires_at),
                updated_at               = ?
            WHERE key_hash = ?
            """,
            (
                _enc(partial["access_token"]),
                partial["access_token_expires_at"],
                _enc(partial["refresh_token"]) if partial.get("refresh_token") else None,
                partial.get("refresh_token_expires_at"),
                now,
                key_hash,
            ),
        )


def delete_qbo_tokens(key_hash: str) -> None:
    """Remove QBO tokens (on disconnect)."""
    _ensure_db()
    with _get_conn() as conn:
        conn.execute("DELETE FROM qbo_tokens WHERE key_hash = ?", (key_hash,))
