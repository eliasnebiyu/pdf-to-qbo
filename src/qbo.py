"""
QuickBooks Online OAuth 2.0 + API push for Statably.

Environment variables:
  INTUIT_CLIENT_ID      — from developer.intuit.com (Keys & credentials)
  INTUIT_CLIENT_SECRET  — from developer.intuit.com (Keys & credentials)
  INTUIT_REDIRECT_URI   — must match the redirect URI registered in the developer portal
                          e.g. https://statably.org/api/auth/qbo/callback
  INTUIT_ENV            — "sandbox" (default) or "production"

OAuth flow:
  1. GET /api/auth/qbo/connect  → backend returns auth_url, frontend navigates there
  2. Intuit redirects to INTUIT_REDIRECT_URI with code, state, realmId
  3. GET /api/auth/qbo/callback → backend exchanges code for tokens, stores them, redirects to /?qbo=connected
  4. POST /api/qbo/push          → backend uses stored tokens to create Purchase/Deposit records in QBO

Token lifecycle:
  - Access token: 1 hour TTL (auto-refreshed 2 min before expiry)
  - Refresh token: 101 days TTL (stored encrypted-at-rest via SQLite; rotate on use)
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

_log = logging.getLogger("statably.qbo")


def _tid(resp: httpx.Response) -> str:
    """Extract intuit_tid from response headers for support tracing."""
    return resp.headers.get("intuit_tid", "n/a")

# ── Intuit OAuth / API endpoints ───────────────────────────────────────────────
_ENV        = os.getenv("INTUIT_ENV", "sandbox").lower()
IS_SANDBOX  = _ENV == "sandbox"

_AUTH_URL   = "https://appcenter.intuit.com/connect/oauth2"
_TOKEN_URL  = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"

QBO_BASE = (
    "https://sandbox-quickbooks.api.intuit.com/v3/company"
    if IS_SANDBOX else
    "https://quickbooks.api.intuit.com/v3/company"
)

_SCOPE     = "com.intuit.quickbooks.accounting"
_MINOR_VER = "65"          # latest QBO minor version

_CLIENT_ID     = os.getenv("INTUIT_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("INTUIT_CLIENT_SECRET", "")
_REDIRECT_URI  = os.getenv("INTUIT_REDIRECT_URI", "")

# ── CSRF state store (in-memory, single-process safe) ─────────────────────────
# For multi-process / multi-replica deploys, replace with a Redis SET or DB table.
_state_store: dict[str, dict] = {}
_STATE_TTL = 600  # 10 minutes


def _clean_states() -> None:
    now = time.time()
    expired = [k for k, v in _state_store.items() if v["exp"] < now]
    for k in expired:
        del _state_store[k]


# ── OAuth helpers ──────────────────────────────────────────────────────────────

def get_auth_url(key_hash: str) -> str:
    """
    Build the Intuit OAuth 2.0 authorization URL.

    A CSRF state token is stored in _state_store mapping state → key_hash
    so the callback can look up which user is completing the flow.
    """
    if not _CLIENT_ID or not _REDIRECT_URI:
        raise HTTPException(
            status_code=503,
            detail=(
                "QuickBooks® integration is not configured on this server. "
                "Set INTUIT_CLIENT_ID and INTUIT_REDIRECT_URI."
            ),
        )
    _clean_states()
    state = secrets.token_urlsafe(32)
    _state_store[state] = {"key_hash": key_hash, "exp": time.time() + _STATE_TTL}
    return f"{_AUTH_URL}?" + urlencode({
        "client_id":     _CLIENT_ID,
        "response_type": "code",
        "scope":         _SCOPE,
        "redirect_uri":  _REDIRECT_URI,
        "state":         state,
    })


def validate_state(state: str) -> str:
    """
    Validate CSRF state token; return the associated key_hash.
    Raises HTTP 400 on invalid / expired state.
    """
    _clean_states()
    entry = _state_store.pop(state, None)
    if not entry or entry["exp"] < time.time():
        raise HTTPException(400, "Invalid or expired OAuth state. Please try the connection again.")
    return entry["key_hash"]


def exchange_code(code: str, realm_id: str) -> dict:
    """
    Exchange the authorization code for access + refresh tokens.

    Returns a dict with:
      access_token, refresh_token, realm_id,
      access_token_expires_at, refresh_token_expires_at  (ISO-8601 UTC)
    """
    if not all([_CLIENT_ID, _CLIENT_SECRET, _REDIRECT_URI]):
        raise HTTPException(503, "QuickBooks® integration is not configured.")
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": _REDIRECT_URI,
        },
        auth=(_CLIENT_ID, _CLIENT_SECRET),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        tid = _tid(resp)
        _log.error("Intuit token exchange failed status=%s intuit_tid=%s body=%s",
                   resp.status_code, tid, resp.text[:300])
        raise HTTPException(502, f"Intuit token exchange failed ({resp.status_code}) [tid:{tid}]: {resp.text[:300]}")
    data = resp.json()
    now  = datetime.now(timezone.utc)
    return {
        "access_token":             data["access_token"],
        "refresh_token":            data["refresh_token"],
        "realm_id":                 realm_id,
        "access_token_expires_at":  (now + timedelta(seconds=data.get("expires_in", 3600))).isoformat(),
        "refresh_token_expires_at": (now + timedelta(seconds=data.get("x_refresh_token_expires_in", 8726400))).isoformat(),
    }


def refresh_tokens(refresh_token: str) -> dict:
    """
    Use the refresh token to obtain a new access token.

    Returns a partial dict (access_token + expiry, plus new refresh_token if rotated).
    """
    resp = httpx.post(
        _TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(_CLIENT_ID, _CLIENT_SECRET),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        tid = _tid(resp)
        _log.error("Intuit token refresh failed status=%s intuit_tid=%s body=%s",
                   resp.status_code, tid, resp.text[:300])
        raise HTTPException(502, f"Token refresh failed ({resp.status_code}) [tid:{tid}]: {resp.text[:300]}")
    data = resp.json()
    now  = datetime.now(timezone.utc)
    out: dict = {
        "access_token":            data["access_token"],
        "access_token_expires_at": (now + timedelta(seconds=data.get("expires_in", 3600))).isoformat(),
    }
    # Intuit may rotate the refresh token on use
    if "refresh_token" in data:
        out["refresh_token"] = data["refresh_token"]
    if "x_refresh_token_expires_in" in data:
        out["refresh_token_expires_at"] = (
            now + timedelta(seconds=data["x_refresh_token_expires_in"])
        ).isoformat()
    return out


def revoke(token: str) -> None:
    """Best-effort revocation of an access or refresh token."""
    try:
        httpx.post(
            _REVOKE_URL,
            data={"token": token},
            auth=(_CLIENT_ID, _CLIENT_SECRET),
            headers={"Accept": "application/json"},
            timeout=10,
        )
    except Exception:
        pass  # revocation is best-effort; don't crash disconnect flow


# ── Token freshness ────────────────────────────────────────────────────────────

def _ensure_fresh(token_record: dict) -> tuple[str, dict | None]:
    """
    Return (access_token, refreshed_partial_dict_or_None).

    Auto-refreshes the access token if it expires within 2 minutes.
    The caller is responsible for persisting `refreshed` if it is not None.
    """
    expires = datetime.fromisoformat(token_record["access_token_expires_at"])
    if datetime.now(timezone.utc) >= expires - timedelta(minutes=2):
        new = refresh_tokens(token_record["refresh_token"])
        return new["access_token"], new
    return token_record["access_token"], None


# ── QBO API push ───────────────────────────────────────────────────────────────

def _qbo_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def _find_accounts(
    client: httpx.Client,
    base_url: str,
    headers: dict,
    bank_name: str,
) -> tuple[dict, dict, dict]:
    """
    Query the QBO company for three account references:
      bank_ref    — the user's bank/checking account (matched by name or first found)
      expense_ref — the uncategorized expense account (for debit lines)
      income_ref  — the uncategorized income account (for credit lines)

    Returns safe fallbacks if the QBO queries fail.
    """
    bank_ref    = {"value": "1",  "name": "Checking"}
    expense_ref = {"value": "63", "name": "Uncategorized Expense"}
    income_ref  = {"value": "64", "name": "Other Income"}

    def _query(q: str) -> list:
        try:
            r = client.get(
                f"{base_url}/query",
                params={"query": q, "minorversion": _MINOR_VER},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                return r.json().get("QueryResponse", {}).get("Account", [])
        except Exception:
            pass
        return []

    # Bank account
    banks = _query("SELECT * FROM Account WHERE AccountType='Bank' MAXRESULTS 25")
    if banks:
        name_lo = bank_name.lower()
        match   = next((a for a in banks if name_lo in a.get("Name", "").lower()), banks[0])
        bank_ref = {"value": match["Id"], "name": match["Name"]}

    # Uncategorized expense
    expenses = _query("SELECT * FROM Account WHERE AccountSubType='UncategorizedExpense' MAXRESULTS 1")
    if expenses:
        expense_ref = {"value": expenses[0]["Id"], "name": expenses[0]["Name"]}

    # Uncategorized / other income
    incomes = _query(
        "SELECT * FROM Account WHERE AccountSubType='OtherMiscellaneousIncome' MAXRESULTS 1"
    )
    if incomes:
        income_ref = {"value": incomes[0]["Id"], "name": incomes[0]["Name"]}

    return bank_ref, expense_ref, income_ref


def push_transactions(
    token_record: dict,
    transactions: list[dict],
    bank_name: str = "Bank",
) -> tuple[dict, dict | None]:
    """
    Push a list of transaction dicts to the QBO company identified by token_record["realm_id"].

    Each transaction dict must contain: date (YYYY-MM-DD), description, amount (float).
    - amount < 0  →  Purchase  (debit / expense)
    - amount > 0  →  Deposit   (credit / income)
    - amount == 0 →  skipped

    Returns (result_summary, refreshed_token_data_or_None).
    The caller must persist refreshed_token_data if it is not None.
    """
    access_token, refreshed = _ensure_fresh(token_record)
    realm_id = token_record["realm_id"]
    base_url = f"{QBO_BASE}/{realm_id}"
    hdrs     = _qbo_headers(access_token)

    pushed = 0
    skipped = 0
    errors: list[str] = []

    with httpx.Client(timeout=30) as client:
        bank_ref, expense_ref, income_ref = _find_accounts(client, base_url, hdrs, bank_name)

        for tx in transactions:
            try:
                amount  = float(tx.get("amount", 0))
                date_s  = str(tx.get("date", ""))
                desc    = (tx.get("description") or "Import")[:4000]

                if amount == 0:
                    skipped += 1
                    continue

                if amount < 0:
                    # Debit → Purchase
                    body = {
                        "PaymentType": "Cash",
                        "AccountRef":  bank_ref,
                        "TxnDate":     date_s,
                        "PrivateNote": desc,
                        "TotalAmt":    abs(amount),
                        "Line": [{
                            "Amount":      abs(amount),
                            "DetailType":  "AccountBasedExpenseLineDetail",
                            "Description": desc,
                            "AccountBasedExpenseLineDetail": {"AccountRef": expense_ref},
                        }],
                    }
                    r = client.post(
                        f"{base_url}/purchase?minorversion={_MINOR_VER}",
                        json=body, headers=hdrs,
                    )
                else:
                    # Credit → Deposit
                    body = {
                        "TxnDate":             date_s,
                        "DepositToAccountRef": bank_ref,
                        "PrivateNote":         desc,
                        "TotalAmt":            abs(amount),
                        "Line": [{
                            "Amount":      abs(amount),
                            "DetailType":  "DepositLineDetail",
                            "Description": desc,
                            "DepositLineDetail": {"AccountRef": income_ref},
                        }],
                    }
                    r = client.post(
                        f"{base_url}/deposit?minorversion={_MINOR_VER}",
                        json=body, headers=hdrs,
                    )

                if r.status_code in (200, 201):
                    pushed += 1
                else:
                    tid = _tid(r)
                    _log.error("QBO push failed date=%s amount=%s status=%s intuit_tid=%s body=%s",
                               date_s, amount, r.status_code, tid, r.text[:120])
                    errors.append(f"{date_s} ${abs(amount):.2f}: HTTP {r.status_code} [tid:{tid}] — {r.text[:120]}")

            except Exception as exc:
                errors.append(str(exc)[:100])

    return {
        "pushed":       pushed,
        "skipped":      skipped,
        "errors":       len(errors),
        "error_sample": errors[:5],
        "environment":  _ENV,
        "realm_id":     realm_id,
    }, refreshed
