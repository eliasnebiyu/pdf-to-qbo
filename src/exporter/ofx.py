"""
OFX / QFX exporter.

OFX (Open Financial Exchange) is the format QuickBooks® uses for
bank statement import. QFX is Quicken®'s variant — structurally
identical, different header value. Both are accepted by QuickBooks®.

Spec reference: OFX 1.02 (SGML, not XML — the version QBO still uses)

Account-type routing
--------------------
- Checking / Savings / Money Market / CD → BANKMSGSRSV1 / STMTRS / BANKACCTFROM
- Credit Card                            → CREDITCARDMSGSRSV1 / CCSTMTRS / CCACCTFROM
- Investment                             → not supported in OFX 1.02 bank envelope;
                                           falls back to CHECKING with a warning.

CHARSET / ENCODING
------------------
OFX 1.02 header uses ENCODING:USASCII + CHARSET:0 for pure 7-bit ASCII output.
CHARSET:1252 (Windows-1252) would imply 8-bit characters, contradicting USASCII.
We encode all output as ASCII with '?' replacement for non-ASCII characters.

QBO import notes
----------------
QBO uses <BANKID> to match a statement to an existing bank account in the chart
of accounts.  Per OFX 1.02 §11.3.2 <BANKID> must be the ABA routing transit
number (9-digit numeric) for US banks.  We maintain a lookup table for the
20 supported banks; for unknown banks we fall back to the bank name, which
causes QBO to prompt for manual account mapping (acceptable degraded behaviour).

QFX FID table
-------------
Quicken uses a numeric Financial Institution ID (FID) from its internal registry.
We maintain a lookup table for supported banks; unknown banks fall back to FID 0
which causes Quicken to prompt for manual identification.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from src.models import ParsedStatement, Transaction, TransactionType, AccountType


# ── ABA routing number lookup (OFX BANKID) ───────────────────────────────────
# Source: publicly available ABA routing numbers for major US banks.
# Used only as a QBO account-matching hint when routing_id is not parsed.
_ROUTING_BY_BANK: dict[str, str] = {
    "Chase":              "021000021",
    "JPMorgan Chase":     "021000021",
    "Bank of America":    "026009593",
    "Wells Fargo":        "121042882",
    "Citibank":           "021000089",
    "Citi":               "021000089",
    "PNC Bank":           "043000096",
    "PNC":                "043000096",
    "US Bank":            "091000022",
    "TD Bank":            "011103093",
    "Capital One":        "056073502",
    "Fifth Third Bank":   "042000314",
    "Fifth Third":        "042000314",
    "American Express":   "124085066",
    "Amex":               "124085066",
    "Fidelity":           "101205681",
    "USAA":               "314074269",
    "Ally Bank":          "124003116",
    "Ally":               "124003116",
    "Charles Schwab":     "121202211",
    "Schwab":             "121202211",
    "Navy Federal":       "256074974",
    "Navy Federal CU":    "256074974",
    "Truist":             "053101121",
    "KEMBA":              "242279408",
    "KEMBA Financial":    "242279408",
}

# ── Quicken FID lookup (QFX) ──────────────────────────────────────────────────
# Quicken's internal Financial Institution ID registry.
_FID_BY_BANK: dict[str, str] = {
    "Chase":              "10898",
    "JPMorgan Chase":     "10898",
    "Bank of America":    "5959",
    "Wells Fargo":        "3048",
    "Citibank":           "24909",
    "Citi":               "24909",
    "PNC Bank":           "2179",
    "PNC":                "2179",
    "US Bank":            "10092",
    "TD Bank":            "15103",
    "Capital One":        "1001",
    "Fifth Third Bank":   "1419",
    "Fifth Third":        "1419",
    "American Express":   "3101",
    "Amex":               "3101",
    "Fidelity":           "7784",
    "USAA":               "67811",
    "Ally Bank":          "56085",
    "Ally":               "56085",
    "Charles Schwab":     "7803",
    "Schwab":             "7803",
    "Navy Federal":       "67845",
    "Navy Federal CU":    "67845",
    "Truist":             "9917",
}


# ── OFX header (SGML format, not XML) ────────────────────────────────────────
# ENCODING:USASCII + CHARSET:0 = pure 7-bit ASCII (OFX 1.02 §2.5.1).
# Do NOT use CHARSET:1252 with ENCODING:USASCII — they are mutually
# contradictory and QBO's validator may reject the file.
_OFX_HEADER = """\
OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:0
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

"""


def _dt(d) -> str:
    """Format a date or datetime as OFX DTYYYYMMDDHHMMSS[+0:GMT]."""
    if hasattr(d, "strftime"):
        return d.strftime("%Y%m%d120000") + "[+0:GMT]"
    return datetime.now(timezone.utc).strftime("%Y%m%d120000") + "[+0:GMT]"


def _amount(v: Decimal) -> str:
    """Format a Decimal as OFX amount string."""
    return f"{v:.2f}"


def _escape(s: str) -> str:
    """Escape characters that break OFX SGML parsing."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tx_block(tx: Transaction, warnings: list[str]) -> str:
    """Render a single OFX <STMTTRN> block."""
    # fit_id is always set by ParsedStatement.assign_fit_ids() before export
    fit_id  = tx.fit_id or "UNKNOWN"
    tx_type = tx.tx_type or TransactionType.OTHER

    # OFX NAME limit: 32 chars — truncate after escaping to avoid splitting entities
    escaped_full = _escape(tx.description)
    if len(escaped_full) > 32:
        name_escaped = escaped_full[:32]
        warnings.append(
            f"Transaction name truncated to 32 chars: '{tx.description[:32]}' "
            f"(full: '{tx.description}')"
        )
    else:
        name_escaped = escaped_full

    # Only emit MEMO when it differs from NAME (avoids redundant duplication)
    memo_text = tx.memo if tx.memo and tx.memo != tx.description else None
    lines = [
        "<STMTTRN>",
        f"<TRNTYPE>{tx_type}",
        f"<DTPOSTED>{_dt(tx.date)}",
        f"<TRNAMT>{_amount(tx.amount)}",
        f"<FITID>{fit_id}",
        f"<NAME>{name_escaped}",
    ]
    if memo_text:
        lines.append(f"<MEMO>{_escape(memo_text[:255])}")
    if tx.check_num:
        lines.append(f"<CHECKNUM>{tx.check_num}")
    lines.append("</STMTTRN>")
    return "\n".join(lines)


def _is_credit_card(statement: ParsedStatement) -> bool:
    """Return True if this statement should use the credit-card OFX envelope."""
    acct_type = statement.account.account_type
    if acct_type is None:
        return False
    val = acct_type.value if hasattr(acct_type, "value") else str(acct_type)
    return val == AccountType.CREDIT.value   # "CREDITLINE"


def _resolve_bank_id(acc) -> str:
    """
    Return the ABA routing number for QBO BANKID matching.

    Checks the parsed routing_id first; falls back to the routing-number
    lookup table keyed by bank name; ultimately falls back to the bank name
    (causes QBO to prompt for manual account mapping, but does not fail import).
    """
    if acc.routing_id:
        return acc.routing_id
    # Normalise bank name: strip suffixes like "Bank", "Federal Savings Bank"
    bank = acc.bank_name or ""
    for key in _ROUTING_BY_BANK:
        if key.lower() in bank.lower() or bank.lower() in key.lower():
            return _ROUTING_BY_BANK[key]
    # Fallback: bank name — QBO will prompt user to map the account
    return bank


def _resolve_fid(acc) -> str:
    """Return the Quicken FID for the given bank, or '0' for unknown banks."""
    bank = acc.bank_name or ""
    for key in _FID_BY_BANK:
        if key.lower() in bank.lower() or bank.lower() in key.lower():
            return _FID_BY_BANK[key]
    return "0"


def _resolve_acct_type(acct_type: Optional[AccountType]) -> str:
    """
    Return a valid OFX 1.02 ACCTTYPE value for BANKACCTFROM.

    Valid values per OFX 1.02 §11.3.2: CHECKING, SAVINGS, MONEYMRKT, CD.
    INVESTMENT is not valid in the bank envelope — map to CHECKING with a note.
    """
    if acct_type is None:
        return "CHECKING"
    val = acct_type.value if hasattr(acct_type, "value") else str(acct_type)
    # Valid bank-envelope ACCTTYPE values
    if val in ("CHECKING", "SAVINGS", "MONEYMRKT", "CD"):
        return val
    # INVESTMENT is not valid in BANKACCTFROM — fall back gracefully
    return "CHECKING"


def to_ofx(statement: ParsedStatement, is_qfx: bool = False) -> str:
    """
    Convert a ParsedStatement to an OFX/QFX string.

    Checking / savings / money market / CD accounts use the standard bank envelope.
    Credit card accounts use CREDITCARDMSGSRSV1 / CCSTMTRS / CCACCTFROM.
    Investment accounts are not natively supported by OFX 1.02 bank envelope;
    they are exported as CHECKING with a warning.

    QFX variant adds the <FI> block required by Quicken with a real FID
    from the bank lookup table (or FID 0 for unknown banks).

    Args:
        statement: fully parsed bank statement
        is_qfx:    if True, write QFX (Quicken) variant instead of OFX

    Returns:
        OFX/QFX string ready to save as .ofx or .qfx file
    """
    acc  = statement.account
    txns = statement.transactions

    # Warn if INVESTMENT account type is used (unsupported in bank OFX envelope)
    if (
        hasattr(acc.account_type, "value") and
        acc.account_type.value == AccountType.INVESTMENT.value
    ):
        statement.warnings.append(
            "INVESTMENT account type is not supported by the OFX 1.02 bank "
            "envelope. Exported as CHECKING; verify in QuickBooks® after import."
        )

    # Unique TRNUID per export (OFX spec §2.7.2 — must be unique per response)
    trnuid = str(uuid.uuid4()).replace("-", "")[:22]

    # Dates
    dt_start = _dt(acc.statement_start) if acc.statement_start else _dt(datetime.now())
    dt_end   = _dt(acc.statement_end)   if acc.statement_end   else _dt(datetime.now())
    dt_now   = datetime.now(timezone.utc).strftime("%Y%m%d120000") + "[+0:GMT]"

    # Build transaction list — collect name-truncation warnings
    trunc_warnings: list[str] = []
    tx_blocks = "\n".join(_tx_block(tx, trunc_warnings) for tx in txns)

    # Propagate any truncation warnings back to the statement
    statement.warnings.extend(trunc_warnings)

    # Closing balance — OFX 1.02 §11.4.2.2 requires <LEDGERBAL> in <STMTRS>.
    # If the parser could not extract closing_balance, emit 0.00 and add a
    # warning so the user knows to reconcile manually in QuickBooks®.
    if acc.closing_balance is not None:
        ledger_bal = (
            f"<LEDGERBAL>\n"
            f"<BALAMT>{_amount(acc.closing_balance)}\n"
            f"<DTASOF>{dt_end}\n"
            f"</LEDGERBAL>"
        )
    else:
        statement.warnings.append(
            "Closing balance could not be extracted — <LEDGERBAL> set to 0.00. "
            "Reconcile manually in QuickBooks®."
        )
        ledger_bal = (
            f"<LEDGERBAL>\n"
            f"<BALAMT>0.00\n"
            f"<DTASOF>{dt_end}\n"
            f"</LEDGERBAL>"
        )

    # QFX signon block includes the <FI> element with a real FID so Quicken can
    # identify the importing institution. OFX omits <FI>.
    if is_qfx:
        fid = _resolve_fid(acc)
        fi_block = (
            "<FI>\n"
            f"<ORG>{_escape(acc.bank_name)}\n"
            f"<FID>{fid}\n"
            "</FI>\n"
        )
    else:
        fi_block = ""

    signon = f"""\
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<DTSERVER>{dt_now}
<LANGUAGE>ENG
{fi_block}</SONRS>
</SIGNONMSGSRSV1>"""

    banktranlist = f"""\
<BANKTRANLIST>
<DTSTART>{dt_start}
<DTEND>{dt_end}
{tx_blocks}
</BANKTRANLIST>"""

    if _is_credit_card(statement):
        # ── Credit card envelope ──────────────────────────────────────────────
        body = f"""\
<OFX>
{signon}
<CREDITCARDMSGSRSV1>
<CCSTMTTRNRS>
<TRNUID>{trnuid}
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<CCSTMTRS>
<CURDEF>{acc.currency}
<CCACCTFROM>
<ACCTID>{_escape(acc.account_id or "UNKNOWN")}
</CCACCTFROM>
{banktranlist}
{ledger_bal}
</CCSTMTRS>
</CCSTMTTRNRS>
</CREDITCARDMSGSRSV1>
</OFX>"""
    else:
        # ── Bank / checking / savings / CD envelope ───────────────────────────
        acct_type = _resolve_acct_type(acc.account_type)
        bank_id   = _resolve_bank_id(acc)
        body = f"""\
<OFX>
{signon}
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>{trnuid}
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<STMTRS>
<CURDEF>{acc.currency}
<BANKACCTFROM>
<BANKID>{_escape(bank_id)}
<ACCTID>{_escape(acc.account_id or "UNKNOWN")}
<ACCTTYPE>{acct_type}
</BANKACCTFROM>
{banktranlist}
{ledger_bal}
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>"""

    return _OFX_HEADER + body


def save_ofx(
    statement: ParsedStatement,
    output_path: str | Path,
    is_qfx: bool = False,
) -> Path:
    """
    Save a ParsedStatement as an OFX or QFX file.

    Args:
        statement:   parsed bank statement
        output_path: where to write the file (.ofx or .qfx)
        is_qfx:      write QFX variant if True

    Returns:
        Path to the written file
    """
    output_path = Path(output_path)
    content     = to_ofx(statement, is_qfx=is_qfx)
    output_path.write_text(content, encoding="ascii", errors="replace")
    return output_path
