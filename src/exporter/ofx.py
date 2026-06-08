"""
OFX / QFX exporter.

OFX (Open Financial Exchange) is the format QuickBooks uses for
bank statement import. QFX is Quicken's variant — structurally
identical, different header value. Both are accepted by QBO.

Spec reference: OFX 1.02 (SGML, not XML — the version QBO still uses)

Account-type routing
--------------------
- Checking / Savings / Money Market → BANKMSGSRSV1 / STMTRS / BANKACCTFROM
- Credit Card                       → CREDITCARDMSGSRSV1 / CCSTMTRS / CCACCTFROM

QBO requires the credit-card envelope for accounts set up as "Credit Card"
in the chart of accounts; using the bank envelope will cause a mismatch and
the import will fail or create a duplicate account.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from src.models import ParsedStatement, Transaction, TransactionType, AccountType


# ── OFX header (SGML format, not XML) ────────────────────────────────────────
_OFX_HEADER = """\
OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

"""


def _dt(d) -> str:
    """Format a date or datetime as OFX DTYYYYMMDDHHMMSS."""
    if hasattr(d, "strftime"):
        return d.strftime("%Y%m%d120000")
    return datetime.now(timezone.utc).strftime("%Y%m%d120000")


def _amount(v: Decimal) -> str:
    """Format a Decimal as OFX amount string."""
    return f"{v:.2f}"


def _escape(s: str) -> str:
    """Escape characters that break OFX SGML parsing."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tx_block(tx: Transaction, index: int) -> str:
    """Render a single OFX <STMTTRN> block."""
    fit_id  = tx.fit_id or tx.generate_fit_id(index)
    tx_type = tx.tx_type or TransactionType.OTHER
    name    = _escape(tx.description[:32])   # OFX NAME limit: 32 chars
    memo    = _escape((tx.memo or tx.description)[:255])

    lines = [
        "<STMTTRN>",
        f"<TRNTYPE>{tx_type}",
        f"<DTPOSTED>{_dt(tx.date)}",
        f"<TRNAMT>{_amount(tx.amount)}",
        f"<FITID>{fit_id}",
        f"<NAME>{name}",
        f"<MEMO>{memo}",
    ]
    if tx.check_num:
        lines.append(f"<CHECKNUM>{tx.check_num}")
    lines.append("</STMTTRN>")
    return "\n".join(lines)


def _is_credit_card(statement: ParsedStatement) -> bool:
    """Return True if this statement should use the credit-card OFX envelope."""
    acct_type = statement.account.account_type or AccountType.CHECKING
    return str(acct_type) == AccountType.CREDIT or acct_type == "CREDITLINE"


def to_ofx(statement: ParsedStatement, is_qfx: bool = False) -> str:
    """
    Convert a ParsedStatement to an OFX/QFX string.

    Checking / savings accounts use the standard bank envelope.
    Credit card accounts use CREDITCARDMSGSRSV1 / CCSTMTRS / CCACCTFROM
    as required by QBO for credit-card account types.

    Args:
        statement: fully parsed bank statement
        is_qfx:    if True, write QFX (Quicken) variant instead of OFX

    Returns:
        OFX/QFX string ready to save as .ofx or .qfx file
    """
    acc  = statement.account
    txns = statement.transactions

    # Dates
    dt_start = _dt(acc.statement_start) if acc.statement_start else _dt(datetime.now())
    dt_end   = _dt(acc.statement_end)   if acc.statement_end   else _dt(datetime.now())
    dt_now   = datetime.now(timezone.utc).strftime("%Y%m%d120000")

    # Build transaction list
    tx_blocks = "\n".join(
        _tx_block(tx, i) for i, tx in enumerate(txns)
    )

    # Closing balance
    ledger_bal = ""
    if acc.closing_balance is not None:
        ledger_bal = (
            f"<LEDGERBAL>\n"
            f"<BALAMT>{_amount(acc.closing_balance)}\n"
            f"<DTASOF>{dt_end}\n"
            f"</LEDGERBAL>"
        )

    signon = f"""\
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<DTSERVER>{dt_now}
<LANGUAGE>ENG
</SONRS>
</SIGNONMSGSRSV1>"""

    banktranlist = f"""\
<BANKTRANLIST>
<DTSTART>{dt_start}
<DTEND>{dt_end}
{tx_blocks}
</BANKTRANLIST>"""

    if _is_credit_card(statement):
        # ── Credit card envelope ──────────────────────────────────────────────
        # QBO requires CREDITCARDMSGSRSV1 for accounts set up as "Credit Card".
        # CCACCTFROM has only ACCTID — no BANKID or ACCTTYPE tags.
        body = f"""\
<OFX>
{signon}
<CREDITCARDMSGSRSV1>
<CCSTMTTRNRS>
<TRNUID>1001
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
        # ── Bank / checking / savings envelope ───────────────────────────────
        acct_type = acc.account_type or AccountType.CHECKING
        body = f"""\
<OFX>
{signon}
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1001
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<STMTRS>
<CURDEF>{acc.currency}
<BANKACCTFROM>
<BANKID>{_escape(acc.routing_id or acc.bank_name)}
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
