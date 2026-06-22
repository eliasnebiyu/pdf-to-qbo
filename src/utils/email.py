"""
Email delivery via Resend (https://resend.com).

Set RESEND_API_KEY in .env to enable.  If the key is absent the functions
are no-ops — useful during local dev without a Resend account configured.

Free tier: 3,000 emails/month, 100/day.  Enough to run the early months.
Sign up at https://resend.com and add a verified sending domain, then add
  RESEND_FROM=noreply@yourdomain.com
  RESEND_API_KEY=re_...
to your Railway environment variables.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_RESEND_KEY  = os.getenv("RESEND_API_KEY", "")
_FROM        = os.getenv("RESEND_FROM", "Parsify <noreply@parsify.io>")
_APP_URL     = os.getenv("APP_URL", "https://parsify.io")


def _client():
    """Lazy-import resend so missing package doesn't crash at import time."""
    try:
        import resend
        resend.api_key = _RESEND_KEY
        return resend
    except ImportError:
        return None


def send_api_key_email(email: str, api_key: str, plan: str = "free") -> bool:
    """
    Send the newly-issued API key to the registrant's inbox.

    Returns True if the email was dispatched, False if Resend is not
    configured or the send fails (callers should not block on this).
    """
    if not _RESEND_KEY:
        log.info("RESEND_API_KEY not set — skipping welcome email to %s", email)
        return False

    resend = _client()
    if resend is None:
        log.warning("resend package not installed — skipping email to %s", email)
        return False

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:system-ui,sans-serif;background:#f8fafc;padding:40px 20px;margin:0;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:40px;border:1px solid #e2e8f0;">

    <h1 style="font-size:22px;font-weight:800;color:#0b1120;margin:0 0 6px;">
      <span style="color:#10b981;">Parsify</span>
    </h1>
    <p style="color:#64748b;font-size:13px;margin:0 0 28px;">Bank statement converter for QuickBooks</p>

    <p style="color:#1e293b;font-size:15px;margin:0 0 20px;">
      Hi there — your free API key is ready. Copy it now; it won't be shown again.
    </p>

    <div style="background:#0b1120;border-radius:8px;padding:18px 20px;margin:0 0 24px;">
      <p style="color:#94a3b8;font-size:11px;margin:0 0 6px;letter-spacing:0.5px;text-transform:uppercase;font-weight:600;">Your API key</p>
      <code style="color:#10b981;font-size:15px;word-break:break-all;font-family:monospace;">{api_key}</code>
    </div>

    <p style="color:#475569;font-size:14px;margin:0 0 8px;">
      Your plan: <strong style="color:#1e293b;">{plan.title()}</strong>
      &nbsp;·&nbsp;
      {"10 conversions / month" if plan == "free" else "100 conversions / month" if plan == "starter" else "Unlimited"}
    </p>

    <a href="{_APP_URL}/app"
       style="display:inline-block;margin:20px 0 0;background:#10b981;color:#fff;
              padding:12px 24px;border-radius:8px;text-decoration:none;
              font-weight:700;font-size:15px;">
      Open the converter →
    </a>

    <hr style="border:none;border-top:1px solid #e2e8f0;margin:32px 0;">

    <h3 style="color:#1e293b;font-size:15px;font-weight:700;margin:0 0 12px;">Getting started</h3>
    <ol style="color:#475569;font-size:14px;margin:0;padding-left:20px;line-height:1.8;">
      <li>Go to <a href="{_APP_URL}/app" style="color:#10b981;">{_APP_URL}/app</a></li>
      <li>Click <strong>Add key</strong> (top right) and paste your key</li>
      <li>Upload any bank statement PDF</li>
      <li>Review, edit, and export to QuickBooks</li>
    </ol>

    <p style="color:#94a3b8;font-size:12px;margin:28px 0 0;line-height:1.7;">
      Need help or want to report a parsing issue?
      Reply to this email or visit
      <a href="{_APP_URL}" style="color:#10b981;">{_APP_URL}</a>.
    </p>
  </div>
</body>
</html>
"""

    try:
        resend.Emails.send({
            "from":    _FROM,
            "to":      [email],
            "subject": "Your Parsify API key",
            "html":    html,
        })
        log.info("Welcome email sent to %s", email)
        return True
    except Exception as exc:
        log.warning("Resend delivery failed for %s: %s", email, exc)
        return False


def send_parsing_error_report(
    user_email: str,
    bank: str,
    description: str,
    api_key: str = "",
) -> bool:
    """Forward a user-submitted parsing error report to the support inbox."""
    if not _RESEND_KEY:
        return False

    resend = _client()
    if resend is None:
        return False

    support = os.getenv("SUPPORT_EMAIL", "support@parsify.io")
    body    = f"""
Parsing error report from {user_email}

Bank:        {bank or "unknown"}
API key:     {api_key[-6:] if api_key else "n/a"} (last 6)
Description:

{description}
""".strip()

    try:
        resend.Emails.send({
            "from":       _FROM,
            "to":         [support],
            "reply_to":   user_email,
            "subject":    f"[Parser error] {bank or 'unknown bank'} — {user_email}",
            "text":       body,
        })
        return True
    except Exception as exc:
        log.warning("Error report relay failed: %s", exc)
        return False
