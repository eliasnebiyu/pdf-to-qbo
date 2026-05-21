"""
CSV exporter — outputs a QuickBooks-compatible CSV for manual import.
This is the fallback when OFX import fails or the user prefers CSV.

QBO CSV import expects: Date, Description, Amount (negative=debit)
Optional columns: Transaction Type, Balance
"""
from __future__ import annotations
import csv
import io
from pathlib import Path

from src.models import ParsedStatement


# QuickBooks Online CSV column headers (exact names QBO expects)
_QBO_HEADERS = ["Date", "Description", "Original Description", "Amount", "Transaction Type", "Category", "Account Name", "Labels", "Notes"]


def to_csv(statement: ParsedStatement, qbo_format: bool = True) -> str:
    """
    Convert a ParsedStatement to a CSV string.

    Args:
        statement:  parsed bank statement
        qbo_format: if True, use QBO's expected column names

    Returns:
        CSV string
    """
    output = io.StringIO()

    if qbo_format:
        headers = _QBO_HEADERS
    else:
        headers = ["Date", "Description", "Amount", "Balance", "Transaction Type", "FitID"]

    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()

    for tx in statement.transactions:
        if qbo_format:
            row = {
                "Date":                 tx.date.strftime("%m/%d/%Y"),
                "Description":          tx.description,
                "Original Description": tx.description,
                "Amount":               f"{tx.amount:.2f}",
                "Transaction Type":     "debit" if tx.amount < 0 else "credit",
                "Category":             "",
                "Account Name":         statement.account.bank_name,
                "Labels":               "",
                "Notes":                tx.memo or "",
            }
        else:
            row = {
                "Date":             tx.date.strftime("%m/%d/%Y"),
                "Description":      tx.description,
                "Amount":           f"{tx.amount:.2f}",
                "Balance":          f"{tx.balance:.2f}" if tx.balance is not None else "",
                "Transaction Type": tx.tx_type or "",
                "FitID":            tx.fit_id or "",
            }
        writer.writerow(row)

    return output.getvalue()


def save_csv(
    statement: ParsedStatement,
    output_path: str | Path,
    qbo_format: bool = True,
) -> Path:
    """Save a ParsedStatement as a CSV file."""
    output_path = Path(output_path)
    content     = to_csv(statement, qbo_format=qbo_format)
    output_path.write_text(content, encoding="utf-8")
    return output_path
