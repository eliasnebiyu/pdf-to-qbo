"""
Tests for the PDF-to-QBO converter.
Run with: pytest tests/ -v
"""
from decimal import Decimal
from datetime import date
import pytest

from src.models import Transaction, ParsedStatement, BankAccount, TransactionType
from src.utils.amount_parser import parse_amount, parse_date, extract_date_range
from src.exporter.ofx import to_ofx
from src.exporter.csv_export import to_csv


# ── Amount parser tests ───────────────────────────────────────────────────────

class TestAmountParser:
    def test_standard(self):
        assert parse_amount("1,234.56") == Decimal("1234.56")

    def test_negative(self):
        assert parse_amount("-500.00") == Decimal("-500.00")

    def test_parenthetical_negative(self):
        assert parse_amount("(500.00)") == Decimal("-500.00")

    def test_dollar_sign(self):
        assert parse_amount("$1,234.56") == Decimal("1234.56")

    def test_trailing_minus(self):
        assert parse_amount("500.00-") == Decimal("-500.00")

    def test_dr_suffix(self):
        assert parse_amount("500.00 DR") == Decimal("-500.00")

    def test_cr_suffix(self):
        result = parse_amount("500.00 CR")
        assert result == Decimal("500.00")

    def test_empty(self):
        assert parse_amount("") is None

    def test_none_string(self):
        assert parse_amount("N/A") is None


# ── Date parser tests ─────────────────────────────────────────────────────────

class TestDateParser:
    def test_slash_format(self):
        assert parse_date("01/15/2024") == date(2024, 1, 15)

    def test_slash_short_year(self):
        assert parse_date("01/15/24") == date(2024, 1, 15)

    def test_dash_format(self):
        assert parse_date("2024-01-15") == date(2024, 1, 15)

    def test_month_name(self):
        assert parse_date("Jan 15, 2024") == date(2024, 1, 15)

    def test_compact(self):
        assert parse_date("20240115") == date(2024, 1, 15)

    def test_no_year_uses_hint(self):
        result = parse_date("01/15", year_hint=2024)
        assert result is not None
        assert result.year == 2024

    def test_empty(self):
        assert parse_date("") is None

    def test_invalid(self):
        assert parse_date("not a date") is None


def test_extract_date_range():
    text = "Statement Period: 01/01/2024 to 01/31/2024"
    start, end = extract_date_range(text)
    assert start == date(2024, 1, 1)
    assert end   == date(2024, 1, 31)


# ── Transaction model tests ───────────────────────────────────────────────────

class TestTransaction:
    def test_basic(self):
        tx = Transaction(
            date=date(2024, 1, 15),
            description="AMAZON PURCHASE",
            amount=Decimal("-50.00"),
        )
        assert tx.amount == Decimal("-50.00")

    def test_type_inference_debit(self):
        tx = Transaction(
            date=date(2024, 1, 15),
            description="Walmart",
            amount=Decimal("-25.00"),
        )
        assert tx.infer_type() == TransactionType.DEBIT

    def test_type_inference_credit(self):
        tx = Transaction(
            date=date(2024, 1, 15),
            description="Incoming payment received",
            amount=Decimal("2500.00"),
        )
        assert tx.infer_type() == TransactionType.CREDIT

    def test_type_inference_interest(self):
        tx = Transaction(
            date=date(2024, 1, 31),
            description="Interest Earned",
            amount=Decimal("1.23"),
        )
        assert tx.infer_type() == TransactionType.INT

    def test_description_whitespace_collapse(self):
        tx = Transaction(
            date=date(2024, 1, 1),
            description="  Multiple   Spaces   Here  ",
            amount=Decimal("-10.00"),
        )
        assert tx.description == "Multiple Spaces Here"

    def test_fit_id_generation(self):
        tx = Transaction(
            date=date(2024, 1, 15),
            description="Test",
            amount=Decimal("-50.00"),
        )
        fit_id = tx.generate_fit_id()
        assert fit_id.startswith("20240115")
        # Same transaction must always produce the same FITID (content-stable)
        assert fit_id == tx.generate_fit_id()


# ── ParsedStatement tests ─────────────────────────────────────────────────────

def _make_statement() -> ParsedStatement:
    account = BankAccount(
        bank_name="Test Bank",
        account_id="1234",
        statement_start=date(2024, 1, 1),
        statement_end=date(2024, 1, 31),
        closing_balance=Decimal("950.00"),
    )
    transactions = [
        Transaction(date=date(2024, 1, 5),  description="Paycheck",  amount=Decimal("1000.00")),
        Transaction(date=date(2024, 1, 10), description="Rent",       amount=Decimal("-800.00")),
        Transaction(date=date(2024, 1, 15), description="Groceries",  amount=Decimal("-50.00")),
        Transaction(date=date(2024, 1, 20), description="Gas",        amount=Decimal("-30.00")),
        Transaction(date=date(2024, 1, 25), description="Freelance",  amount=Decimal("200.00")),
    ]
    stmt = ParsedStatement(account=account, transactions=transactions)
    stmt.assign_fit_ids()
    return stmt


class TestParsedStatement:
    def test_total_debits(self):
        stmt = _make_statement()
        assert stmt.total_debits == Decimal("880.00")

    def test_total_credits(self):
        stmt = _make_statement()
        assert stmt.total_credits == Decimal("1200.00")

    def test_transaction_count(self):
        assert _make_statement().transaction_count == 5

    def test_fit_ids_assigned(self):
        stmt = _make_statement()
        assert all(tx.fit_id is not None for tx in stmt.transactions)

    def test_fit_ids_unique(self):
        stmt  = _make_statement()
        ids   = [tx.fit_id for tx in stmt.transactions]
        assert len(ids) == len(set(ids))


# ── OFX exporter tests ────────────────────────────────────────────────────────

class TestOFXExporter:
    def test_ofx_header(self):
        ofx = to_ofx(_make_statement())
        assert "OFXHEADER:100" in ofx
        assert "DATA:OFXSGML" in ofx

    def test_contains_transactions(self):
        ofx = to_ofx(_make_statement())
        assert "STMTTRN" in ofx
        assert "Paycheck" in ofx
        assert "Rent" in ofx

    def test_negative_amounts(self):
        ofx = to_ofx(_make_statement())
        assert "-800.00" in ofx
        assert "-50.00"  in ofx

    def test_positive_amounts(self):
        ofx = to_ofx(_make_statement())
        assert "1000.00" in ofx

    def test_account_id(self):
        ofx = to_ofx(_make_statement())
        assert "1234" in ofx

    def test_qfx_variant(self):
        qfx = to_ofx(_make_statement(), is_qfx=True)
        assert "OFXHEADER:100" in qfx  # same format, different extension


# ── CSV exporter tests ────────────────────────────────────────────────────────

class TestCSVExporter:
    def test_has_header(self):
        csv = to_csv(_make_statement())
        assert "Date" in csv
        assert "Description" in csv
        assert "Amount" in csv

    def test_contains_transactions(self):
        csv = to_csv(_make_statement())
        assert "Paycheck" in csv
        assert "Rent" in csv

    def test_debit_negative(self):
        csv = to_csv(_make_statement())
        assert "-800.00" in csv

    def test_row_count(self):
        csv    = to_csv(_make_statement())
        lines  = [l for l in csv.strip().splitlines() if l.strip()]
        # 1 header + 5 transactions
        assert len(lines) == 6
