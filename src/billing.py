"""
Stripe billing integration.

Subscription tiers
------------------
starter  → STRIPE_PRICE_STARTER env var  ($9/month, 100 conversions)
pro      → STRIPE_PRICE_PRO     env var  ($29/month, unlimited)

Webhook events handled
----------------------
checkout.session.completed         Upgrade key to the purchased plan
invoice.payment_succeeded          Reactivate a previously suspended key
invoice.payment_failed             Suspend key until payment is resolved
customer.subscription.deleted      Cancel subscription, revert key to free

Setup
-----
1. Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET in the environment.
2. Create two recurring Prices in the Stripe dashboard (or CLI) and set
   STRIPE_PRICE_STARTER / STRIPE_PRICE_PRO to their price IDs.
3. Point the Stripe webhook to  POST /stripe/webhook  for these events:
     checkout.session.completed
     invoice.payment_succeeded
     invoice.payment_failed
     customer.subscription.deleted
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from src.auth import (
    cancel_by_subscription,
    reactivate_by_subscription,
    suspend_by_subscription,
    update_plan,
)

if TYPE_CHECKING:
    pass  # stripe imported lazily below

# ── Helpers ────────────────────────────────────────────────────────────────────

def _stripe():
    """Lazily import stripe and set the API key.  Raises 503 if unconfigured."""
    try:
        import stripe as _s
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="stripe package not installed. Run: pip install stripe",
        )
    secret = os.getenv("STRIPE_SECRET_KEY", "")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Stripe not configured. Set STRIPE_SECRET_KEY in the environment.",
        )
    _s.api_key = secret
    return _s


def _price_to_plan() -> dict[str, str]:
    """Return a mapping of Stripe price IDs → plan names from env vars."""
    mapping: dict[str, str] = {}
    for plan in ("starter", "pro"):
        price_id = os.getenv(f"STRIPE_PRICE_{plan.upper()}", "")
        if price_id:
            mapping[price_id] = plan
    return mapping

# ── Checkout ───────────────────────────────────────────────────────────────────

def create_checkout_session(
    api_key: str,
    email: str,
    plan: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """
    Create a Stripe Checkout session for *plan*.
    Embeds the api_key in session metadata so the webhook can upgrade it.
    Returns the Checkout URL to redirect the user to.
    """
    if plan not in ("starter", "pro"):
        raise HTTPException(
            status_code=400,
            detail="Invalid plan. Choose 'starter' ($9/mo) or 'pro' ($29/mo).",
        )

    price_id = os.getenv(f"STRIPE_PRICE_{plan.upper()}", "")
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Billing not configured: missing STRIPE_PRICE_{plan.upper()} "
                "environment variable."
            ),
        )

    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=email,
        # The webhook reads this to know which API key to upgrade
        metadata={"api_key": api_key},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url  # type: ignore[return-value]

# ── Webhook ────────────────────────────────────────────────────────────────────

async def handle_webhook(request: Request) -> dict:
    """
    Verify the Stripe signature and dispatch the event to the appropriate handler.
    Call this from  POST /stripe/webhook.
    """
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Webhook not configured. Set STRIPE_WEBHOOK_SECRET.",
        )

    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")
    stripe  = _stripe()

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stripe webhook signature.",
        )

    etype = event["type"]
    obj   = event["data"]["object"]

    if etype == "checkout.session.completed":
        _on_checkout_completed(obj, stripe)

    elif etype == "invoice.payment_succeeded":
        sub_id = obj.get("subscription")
        if sub_id:
            reactivate_by_subscription(sub_id)

    elif etype == "invoice.payment_failed":
        sub_id = obj.get("subscription")
        if sub_id:
            suspend_by_subscription(sub_id)

    elif etype == "customer.subscription.deleted":
        sub_id = obj.get("id")
        if sub_id:
            cancel_by_subscription(sub_id)

    return {"received": True, "type": etype}


def _on_checkout_completed(session: dict, stripe) -> None:
    """Upgrade the embedded API key to the plan whose price was purchased."""
    api_key         = (session.get("metadata") or {}).get("api_key")
    subscription_id = session.get("subscription")
    customer_id     = session.get("customer")

    if not api_key or not subscription_id:
        return  # nothing we can do without these

    # Determine which plan was purchased from the subscription's price ID
    price_map = _price_to_plan()
    try:
        sub      = stripe.Subscription.retrieve(subscription_id)
        price_id = sub["items"]["data"][0]["price"]["id"]
        plan     = price_map.get(price_id, "starter")
    except Exception:
        plan = "starter"  # safe default

    update_plan(
        api_key,
        plan,
        customer_id=customer_id,
        subscription_id=subscription_id,
    )
