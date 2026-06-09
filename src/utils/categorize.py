"""
Transaction categorizer — maps transaction descriptions to QBO expense categories.

Two modes:
  1. Keyword-based (always available, free, instant)
  2. LLM batch (Claude API, requires ANTHROPIC_API_KEY, higher accuracy)

Usage:
    from src.utils.categorize import categorize_transactions
    txns = categorize_transactions(txns)                       # keyword only
    txns = categorize_transactions(txns, use_llm=True)        # + LLM for unknowns
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from src.models import Transaction


# ── QBO category taxonomy ─────────────────────────────────────────────────────
# These match QuickBooks Online's default chart of accounts.
# Each tuple: (list_of_keywords, category_name)
# Keywords are matched case-insensitively against the FULL description.

_KEYWORD_RULES: list[tuple[list[str], str]] = [
    # ── Fuel & Auto ───────────────────────────────────────────────────────────
    (["shell oil", "exxon", " bp ", "chevron", "sunoco", "marathon gas",
      "valero", "mobil ", "speedway", "circle k", "wawa ", "kwik trip",
      "pilot flying j", "flying j", "loves travel", "casey's gen",
      "getgo gas", "sheetz", "racetrac", "murphy oil", "fuel pump"],
     "Fuel"),

    (["jiffy lube", "midas ", "autozone", "advance auto", "o'reilly auto",
      "napa auto", "pep boys", "firestone", "goodyear tire", "discount tire",
      "tires plus", "big o tires", "oil change", "car wash", "auto repair",
      "transmission", "muffler"],
     "Auto Maintenance"),

    (["enterprise rent-a-car", "hertz ", "avis ", "budget car", "national car",
      "alamo rent", "zipcar", "turo "],
     "Vehicle Rental"),

    # ── Food & Dining ─────────────────────────────────────────────────────────
    (["mcdonald", "burger king", "wendy's", "taco bell", "chipotle",
      "subway ", "chick-fil-a", "chick fil a", "kfc ", "popeyes",
      "domino's", "dominos ", "pizza hut", "papa john", "five guys",
      "shake shack", "panera bread", "panera ", "dunkin'", "dunkin ",
      "starbucks", "tim horton", "krispy kreme", "sonic drive",
      "dairy queen", "arby's", "hardee's", "carl's jr", "in-n-out",
      "whataburger", "culver's", "noodles & company", "panda express",
      "wingstop", "buffalo wild", "olive garden", "applebee's",
      "chili's grill", "ihop ", "denny's", "waffle house", "cracker barrel",
      "texas roadhouse", "outback steak", "red lobster", "longhorn steak",
      "doordash", "uber eats", "grubhub", "postmates", "seamless "],
     "Meals & Entertainment"),

    # ── Travel & Lodging ──────────────────────────────────────────────────────
    (["marriott", "hilton ", "hyatt ", "sheraton", "holiday inn",
      "best western", "hampton inn", "radisson", "embassy suites",
      "doubletree", "wyndham", "choice hotel", "la quinta", "comfort inn",
      "airbnb", "vrbo ", "hotels.com", "expedia", "booking.com",
      "priceline", "hotwire"],
     "Travel"),

    (["delta air", "american airlines", "united airlines", "southwest airlines",
      "spirit airlines", "frontier airlines", "jetblue", "alaska airlines",
      "allegiant air", "hawaiian air", "sun country"],
     "Travel"),

    (["uber trip", "lyft ", "taxi ", "yellow cab", "limousine service",
      "amtrak", "greyhound"],
     "Travel"),

    (["marriott hotel", "hotel ", "inn at ", "resort "],
     "Travel"),

    # ── Utilities ────────────────────────────────────────────────────────────
    (["duke energy", "dominion energy", "southern company", "pg&e",
      "con edison", "national grid", "centerpoint energy", "xcel energy",
      "dte energy", "firstenergy", "aep ", "entergy", "pplu",
      "columbia gas", "spire gas", "nipsco ", "water bill",
      "electric bill", "utility bill"],
     "Utilities"),

    (["at&t ", "verizon wireless", "t-mobile", "sprint ", "comcast",
      "xfinity", "spectrum ", "cox commun", "charter comm", "dish network",
      "directv", "hulu live", "sling tv", "fubo tv"],
     "Utilities"),

    # ── Office & Technology ───────────────────────────────────────────────────
    (["amazon web services", "aws ", "google cloud", "microsoft azure",
      "digitalocean", "linode ", "heroku ", "cloudflare", "twilio",
      "sendgrid", "mailchimp", "stripe ", "braintree", "paypal fee"],
     "Computer & Internet Expenses"),

    (["microsoft 365", "microsoft office", "office 365", "google workspace",
      "google one", "dropbox ", "box.com", "zoom.us", "slack ", "notion ",
      "adobe ", "figma ", "canva ", "loom ", "calendly", "typeform",
      "hubspot", "salesforce", "freshdesk", "zendesk", "intercom",
      "quickbooks", "intuit ", "xero ", "freshbooks", "wave account"],
     "Computer & Internet Expenses"),

    (["amazon.com", "amazon mktplace", "amazon prime", "best buy",
      "staples ", "office depot", "officemax", "costco.com",
      "newegg ", "b&h photo", "adorama"],
     "Office Supplies & Software"),

    # ── Insurance ────────────────────────────────────────────────────────────
    (["state farm", "geico ", "progressive ", "allstate ", "usaa ",
      "liberty mutual", "farmers ins", "nationwide ins", "travelers ins",
      "aig ", "cigna ", "aetna ", "humana ", "united health",
      "blue cross", "blueshield", "kaiser perm"],
     "Insurance"),

    # ── Professional Services ─────────────────────────────────────────────────
    (["fedex ", "ups ", "usps ", "dhl ", "pitney bowes", "stamps.com",
      "shipping label"],
     "Office Supplies & Software"),

    (["attorney", "lawyers", "law firm", "legal fee", "notary",
      "accounting firm", "cpa ", "bookkeeping", "payroll service",
      "adp payroll", "paychex", "gusto payroll", "rippling pay"],
     "Professional Fees"),

    # ── Advertising & Marketing ───────────────────────────────────────────────
    (["facebook ads", "meta ads", "google ads", "linkedin ads",
      "twitter ads", "instagram ads", "bing ads", "youtube ads",
      "tiktok ads", "pinterest ads", "snapchat ads",
      "marketing ", "advertising"],
     "Advertising & Marketing"),

    # ── Payroll ───────────────────────────────────────────────────────────────
    (["adp payroll", "paychex", "gusto ", "bamboohr", "rippling",
      "payroll direct", "direct deposit payroll", "payroll funding"],
     "Payroll"),

    # ── Home / Property ───────────────────────────────────────────────────────
    (["home depot", "lowe's", "lowes ", "ace hardware", "true value",
      "menards ", "habitat restore", "84 lumber", "fastenal"],
     "Repairs & Maintenance"),

    (["rent ", " rent ", "lease payment", "property mgmt", "property management"],
     "Rent & Lease"),

    # ── Banking / Finance ─────────────────────────────────────────────────────
    (["interest charge", "interest fee", "late fee", "overdraft fee",
      "nsf fee", "atm fee", "wire fee", "monthly fee", "service fee",
      "maintenance fee", "annual fee", "foreign transaction"],
     "Bank Charges & Fees"),

    # ── Healthcare ────────────────────────────────────────────────────────────
    (["cvs pharmacy", "walgreens", "rite aid", "pharmacy",
      "hospital", "medical center", "health care", "wellness",
      "dental ", "vision care", "eyeglass", "urgent care",
      "quest diagn", "labcorp"],
     "Health & Medical"),

    # ── Taxes & Licenses ──────────────────────────────────────────────────────
    (["irs payment", "irs usa tax", "state tax", "county tax",
      "city tax", "property tax", "sales tax", "estimated tax",
      "license fee", "permit fee", "registration fee"],
     "Taxes & Licenses"),

    # ── Subscriptions ────────────────────────────────────────────────────────
    (["netflix", "spotify", "apple music", "youtube premium", "hulu ",
      "disney+", "paramount+", "peacock tv", "amazon prime video",
      "audible ", "kindle unlimited", "scribd "],
     "Dues & Subscriptions"),

    (["association fee", "membership fee", "dues ", "subscription",
      "annual membership", "trade assoc"],
     "Dues & Subscriptions"),
]


def _keyword_category(description: str) -> Optional[str]:
    """Return the best matching keyword category, or None."""
    d = description.lower()
    for keywords, category in _KEYWORD_RULES:
        for kw in keywords:
            if kw in d:
                return category
    return None


def categorize_transactions(
    transactions: list[Transaction],
    use_llm: bool = False,
) -> list[Transaction]:
    """
    Assign a ``category`` hint to every transaction.

    1. Keyword pass — instant, free, covers most common merchants.
    2. LLM pass (optional) — sends remaining unmatched descriptions
       to Claude in a single batch request.  Only runs when
       ``use_llm=True`` AND ``ANTHROPIC_API_KEY`` is set.

    The ``category`` field is informational — it goes into the CSV
    export and the ReviewUI.  OFX does not support categories; the
    user still maps them inside QuickBooks after import.

    Returns the same list (mutated in-place) for convenience.
    """
    # ── Pass 1: keyword matching ──────────────────────────────────────────────
    unmatched: list[Transaction] = []
    for tx in transactions:
        cat = _keyword_category(tx.description)
        if cat:
            tx.category = cat
        else:
            unmatched.append(tx)

    # ── Pass 2: LLM batch (optional) ─────────────────────────────────────────
    if use_llm and unmatched and os.getenv("ANTHROPIC_API_KEY"):
        _llm_categorize(unmatched)

    return transactions


def _llm_categorize(transactions: list[Transaction]) -> None:
    """
    Call Claude API to batch-categorize up to 200 transactions.
    Mutates transaction.category in-place for matched results.
    """
    try:
        import anthropic
    except ImportError:
        return

    # Deduplicate descriptions to save tokens
    unique_descs = list({tx.description for tx in transactions})[:200]

    categories = [
        "Advertising & Marketing", "Auto Maintenance", "Bank Charges & Fees",
        "Computer & Internet Expenses", "Dues & Subscriptions", "Equipment Rental",
        "Fuel", "Health & Medical", "Insurance", "Meals & Entertainment",
        "Office Supplies & Software", "Other Business Expenses", "Payroll",
        "Professional Fees", "Rent & Lease", "Repairs & Maintenance",
        "Taxes & Licenses", "Travel", "Utilities", "Vehicle Rental",
    ]

    prompt = (
        "You are a bookkeeper. Categorize each transaction description below into "
        "exactly one of these QBO expense categories:\n"
        + "\n".join(f"- {c}" for c in categories)
        + "\n\nRespond with a JSON object mapping each description to its category. "
        "If unsure, use 'Other Business Expenses'.\n\nDescriptions:\n"
        + "\n".join(f"- {d}" for d in unique_descs)
    )

    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=os.getenv("PDF_PARSER_LLM_MODEL", "claude-3-5-haiku-20241022"),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Extract JSON block if wrapped in markdown fences
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
        if m:
            raw = m.group(1).strip()
        mapping: dict[str, str] = json.loads(raw)

        # Apply categories
        valid = set(categories)
        for tx in transactions:
            cat = mapping.get(tx.description)
            if cat and cat in valid:
                tx.category = cat
            elif tx.category is None:
                tx.category = "Other Business Expenses"

    except Exception:
        # LLM categorization is best-effort — never fail the whole request
        pass
