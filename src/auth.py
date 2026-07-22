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
                status                 TEXT NOT NULL DEFAULT 'active',
                conversions_used       INTEGER NOT NULL DEFAULT 0,
                period_start           TEXT NOT NULL,
                created_at             TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stripe_sub
                ON api_keys (stripe_subscription_id);
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
    """
    _ensure_db()
    raw_key  = "lf_" + secrets.token_hex(24)   # 51-char key
    key_hash = _hash_key(raw_key)
    now      = datetime.now(timezone.utc)
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO api_keys
               (key, email, plan, status, conversions_used, period_start, created_at)
               VALUES (?, ?, ?, 'active', 0, ?, ?)""",
            (key_hash, email.lower().strip(), plan,
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
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Monthly quota exceeded. "
                    f"You have {remaining} conversion(s) remaining on the "
                    f"{plan_info['label']} plan ({limit}/month). "
                    "Upgrade your plan at POST /auth/checkout."
                ),
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
    return _maybe_reset_period(record)
