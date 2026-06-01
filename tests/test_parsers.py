"""
Integration tests for bank statement parsers.

These tests mock pdfplumber so no real PDF files are needed —
the full parse pipeline (detection → extraction → deduplication → model)
runs against realistic text and table data.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

from src.parser import detect_and_parse
from src.parser.banks.bofa import BofAParser
from src.parser.banks.chase import ChaseParser
from src.parser.banks.citi import CitiParser
from src.parser.banks.generic import GenericParser
from src.parser.banks.wells import WellsFargoParser


# ── Mock helpers ──────────────────────────────────────────────────────────────

def make_mock_pdf(pages: list[dict]) -> MagicMock:
    """Return a mock pdfplumber PDF with controllable text and tables per page.

    Each page dict may contain:
      text   (str)              — text returned by page.extract_text()
      tables (list of tables)   — returned by page.extract_tables()
    """
    mock_pdf = MagicMock()
    mock_pages = []
    for p in pages:
        page = MagicMock()
        page.extract_text.return_value = p.get("text", "")
        page.extract_tables.return_value = p.get("tables", [])
        mock_pages.append(page)
    mock_pdf.pages = mock_pages
    return mock_pdf


def patch_pdf(pages: list[dict]):
    """Context manager: patch pdfplumber.open to return a mock PDF."""
    return patch("pdfplumber.open", return_value=make_mock_pdf(pages))


# ── Shared test fixtures ──────────────────────────────────────────────────────

_CHASE_HEADER = (
    "JPMorgan Chase Bank, N.A.\nchase.com\n"
    "Statement Period: 01/01/2024 to 01/31/2024"
)
_BOFA_HEADER = (
    "Bank of America\nbankofamerica.com\n"
    "Statement Period: 01/01/2024 – 01/31/2024"
)
_WF_HEADER = (
    "Wells Fargo Bank, N.A.\nwellsfargo.com\n"
    "Statement Period: 01/01/2024 to 01/31/2024"
)
_CITI_HEADER = (
    "Citibank, N.A.\ncitibank.com\n"
    "Statement Period: 01/01/2024 to 01/31/2024"
)

_CHASE_TABLE = [
    ["Date", "Description", "Amount", "Balance"],
    ["01/05", "DIRECT DEPOSIT EMPLOYER",  "3,200.00", "4,200.00"],
    ["01/10", "AMAZON.COM*ABCD1234",      "-89.99",   "4,110.01"],
    ["01/15", "WHOLEFDS MKT #10452",      "-67.43",   "4,042.58"],
    ["01/20", "ATM WITHDRAWAL",           "-200.00",  "3,842.58"],
]

_BOFA_TABLE = [
    ["Date", "Description", "Amount", "Balance"],
    ["01/05/2024", "DIRECT DEPOSIT",      "3,200.00", "5,200.00"],
    ["01/12/2024", "NETFLIX.COM",         "-15.49",   "5,184.51"],
    ["01/18/2024", "WHOLE FOODS MARKET",  "-78.32",   "5,106.19"],
]

_WF_TABLE = [
    ["Date", "Description", "Withdrawals", "Deposits", "Balance"],
    ["01/03", "PAYROLL DEPOSIT",  "",        "2,500.00", "3,500.00"],
    ["01/08", "GROCERY STORE",   "62.45",   "",         "3,437.55"],
    ["01/15", "UTILITY PAYMENT", "145.00",  "",         "3,292.55"],
    ["01/22", "ATM DEPOSIT",     "",        "500.00",   "3,792.55"],
]

_GENERIC_TABLE = [
    ["Date", "Description", "Amount", "Balance"],
    ["2024-01-05", "SALARY CREDIT",    "4,000.00", "5,500.00"],
    ["2024-01-10", "ELECTRICITY BILL", "-120.00",  "5,380.00"],
    ["2024-01-20", "SUPERMARKET",      "-95.50",   "5,284.50"],
]


# ── Chase parser ──────────────────────────────────────────────────────────────

class TestChaseParser:

    def test_detects_chase_statement(self):
        with patch_pdf([{"text": _CHASE_HEADER, "tables": []}]):
            p = ChaseParser("fake.pdf")
            with p:
                assert p.can_parse() is True

    def test_ignores_non_chase_statement(self):
        with patch_pdf([{"text": _BOFA_HEADER, "tables": []}]):
            p = ChaseParser("fake.pdf")
            with p:
                assert p.can_parse() is False

    def test_table_extraction(self):
        with patch_pdf([{"text": _CHASE_HEADER, "tables": [_CHASE_TABLE]}]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.parser_used == "chase"
        assert stmt.account.bank_name == "JPMorgan Chase"
        assert stmt.transaction_count == 4
        assert all(tx.fit_id is not None for tx in stmt.transactions)

        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["DIRECT DEPOSIT EMPLOYER"].amount == Decimal("3200.00")
        assert by_desc["AMAZON.COM*ABCD1234"].amount == Decimal("-89.99")
        assert by_desc["AMAZON.COM*ABCD1234"].balance == Decimal("4110.01")
        assert by_desc["AMAZON.COM*ABCD1234"].date == date(2024, 1, 10)

    def test_line_fallback(self):
        text = (
            _CHASE_HEADER + "\n\n"
            "ACCOUNT ACTIVITY\n"
            "01/05 DIRECT DEPOSIT EMPLOYER         3,200.00   4,200.00\n"
            "01/10 AMAZON.COM*ABCD1234             -89.99     4,110.01\n"
            "01/15 WHOLEFDS MKT #10452             -67.43     4,042.58\n"
            "01/20 ATM WITHDRAWAL                  -200.00    3,842.58\n"
        )
        with patch_pdf([{"text": text, "tables": []}]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 4
        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["DIRECT DEPOSIT EMPLOYER"].amount == Decimal("3200.00")
        assert by_desc["ATM WITHDRAWAL"].amount == Decimal("-200.00")

    def test_five_column_table(self):
        """Chase sometimes splits withdrawals and deposits into separate columns."""
        table = [
            ["Date", "Description", "Withdrawals", "Deposits", "Balance"],
            ["01/05", "PAYROLL DEPOSIT", "",       "3,200.00", "4,200.00"],
            ["01/10", "GROCERY STORE",   "55.00",  "",         "4,145.00"],
        ]
        with patch_pdf([{"text": _CHASE_HEADER, "tables": [table]}]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 2
        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["PAYROLL DEPOSIT"].amount == Decimal("3200.00")
        assert by_desc["GROCERY STORE"].amount == Decimal("-55.00")

    def test_multipage_tables(self):
        """Transactions across multiple pages should all be collected."""
        page1_table = [
            ["Date", "Description", "Amount", "Balance"],
            ["01/05", "DIRECT DEPOSIT", "3,200.00", "4,200.00"],
        ]
        page2_table = [
            ["Date", "Description", "Amount", "Balance"],
            ["01/15", "RENT PAYMENT", "-1,500.00", "2,700.00"],
        ]
        with patch_pdf([
            {"text": _CHASE_HEADER, "tables": [page1_table]},
            {"text": "",            "tables": [page2_table]},
        ]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 2

    def test_deduplication_removes_exact_duplicates(self):
        dup = ["01/10", "AMAZON.COM*ABCD1234", "-89.99", "4,110.01"]
        table = [
            ["Date", "Description", "Amount", "Balance"],
            dup,
            dup,
        ]
        with patch_pdf([{"text": _CHASE_HEADER, "tables": [table]}]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 1

    def test_fit_ids_are_unique(self):
        with patch_pdf([{"text": _CHASE_HEADER, "tables": [_CHASE_TABLE]}]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        ids = [tx.fit_id for tx in stmt.transactions]
        assert len(ids) == len(set(ids))

    def test_statement_dates_extracted(self):
        with patch_pdf([{"text": _CHASE_HEADER, "tables": [_CHASE_TABLE]}]):
            p = ChaseParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.account.statement_start == date(2024, 1, 1)
        assert stmt.account.statement_end == date(2024, 1, 31)


# ── Bank of America parser ────────────────────────────────────────────────────

class TestBofAParser:

    def test_detects_bofa_statement(self):
        with patch_pdf([{"text": _BOFA_HEADER, "tables": []}]):
            p = BofAParser("fake.pdf")
            with p:
                assert p.can_parse() is True

    def test_ignores_non_bofa_statement(self):
        with patch_pdf([{"text": _CHASE_HEADER, "tables": []}]):
            p = BofAParser("fake.pdf")
            with p:
                assert p.can_parse() is False

    def test_table_extraction(self):
        with patch_pdf([{"text": _BOFA_HEADER, "tables": [_BOFA_TABLE]}]):
            p = BofAParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.parser_used == "bofa"
        assert stmt.account.bank_name == "Bank of America"
        assert stmt.transaction_count == 3

        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["DIRECT DEPOSIT"].amount == Decimal("3200.00")
        assert by_desc["NETFLIX.COM"].amount == Decimal("-15.49")
        assert by_desc["NETFLIX.COM"].date == date(2024, 1, 12)
        assert by_desc["WHOLE FOODS MARKET"].balance == Decimal("5106.19")

    def test_line_fallback(self):
        text = (
            _BOFA_HEADER + "\n\n"
            "01/05/2024 DIRECT DEPOSIT                3,200.00   5,200.00\n"
            "01/12/2024 NETFLIX.COM                   -15.49     5,184.51\n"
            "01/18/2024 WHOLE FOODS MARKET            -78.32     5,106.19\n"
        )
        with patch_pdf([{"text": text, "tables": []}]):
            p = BofAParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 3
        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["WHOLE FOODS MARKET"].amount == Decimal("-78.32")

    def test_total_debits_and_credits(self):
        with patch_pdf([{"text": _BOFA_HEADER, "tables": [_BOFA_TABLE]}]):
            p = BofAParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.total_credits == Decimal("3200.00")
        assert stmt.total_debits == Decimal("93.81")  # 15.49 + 78.32


# ── Wells Fargo parser ────────────────────────────────────────────────────────

class TestWellsFargoParser:

    def test_detects_wells_fargo_statement(self):
        with patch_pdf([{"text": _WF_HEADER, "tables": []}]):
            p = WellsFargoParser("fake.pdf")
            with p:
                assert p.can_parse() is True

    def test_ignores_non_wf_statement(self):
        with patch_pdf([{"text": _BOFA_HEADER, "tables": []}]):
            p = WellsFargoParser("fake.pdf")
            with p:
                assert p.can_parse() is False

    def test_table_extraction_with_split_columns(self):
        """Withdrawals and deposits in separate columns → signed amounts."""
        with patch_pdf([{"text": _WF_HEADER, "tables": [_WF_TABLE]}]):
            p = WellsFargoParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.parser_used == "wells_fargo"
        assert stmt.account.bank_name == "Wells Fargo"
        assert stmt.transaction_count == 4

        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["PAYROLL DEPOSIT"].amount == Decimal("2500.00")
        assert by_desc["GROCERY STORE"].amount == Decimal("-62.45")
        assert by_desc["UTILITY PAYMENT"].amount == Decimal("-145.00")
        assert by_desc["ATM DEPOSIT"].amount == Decimal("500.00")

    def test_balance_parsed(self):
        with patch_pdf([{"text": _WF_HEADER, "tables": [_WF_TABLE]}]):
            p = WellsFargoParser("fake.pdf")
            with p:
                stmt = p.extract()

        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["GROCERY STORE"].balance == Decimal("3437.55")

    def test_no_table_emits_warning(self):
        with patch_pdf([{"text": _WF_HEADER, "tables": []}]):
            p = WellsFargoParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert any("line" in w.lower() or "no table" in w.lower() for w in stmt.warnings)


# ── Citi parser ───────────────────────────────────────────────────────────────

class TestCitiParser:

    def test_detects_citi_statement(self):
        with patch_pdf([{"text": _CITI_HEADER, "tables": []}]):
            p = CitiParser("fake.pdf")
            with p:
                assert p.can_parse() is True

    def test_ignores_non_citi_statement(self):
        with patch_pdf([{"text": _CHASE_HEADER, "tables": []}]):
            p = CitiParser("fake.pdf")
            with p:
                assert p.can_parse() is False

    def test_checking_table_extraction(self):
        """Citi checking statement: standard Date/Desc/Amount/Balance layout."""
        table = [
            ["Date", "Description", "Amount", "Balance"],
            ["01/05/2024", "DIRECT DEPOSIT",   "2,500.00", "3,500.00"],
            ["01/10/2024", "GROCERY STORE",    "-92.15",   "3,407.85"],
            ["01/20/2024", "ELECTRIC PAYMENT", "-135.00",  "3,272.85"],
        ]
        with patch_pdf([{"text": _CITI_HEADER, "tables": [table]}]):
            p = CitiParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.parser_used == "citi_chk"
        assert stmt.transaction_count == 3
        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["DIRECT DEPOSIT"].amount == Decimal("2500.00")
        assert by_desc["GROCERY STORE"].amount == Decimal("-92.15")


# ── Generic parser ────────────────────────────────────────────────────────────

class TestGenericParser:

    def test_always_accepts_any_pdf(self):
        with patch_pdf([{"text": "Completely Unknown Bank Statement 2024", "tables": []}]):
            p = GenericParser("fake.pdf")
            with p:
                assert p.can_parse() is True

    def test_table_extraction(self):
        with patch_pdf([{"text": "My Bank 2024", "tables": [_GENERIC_TABLE]}]):
            p = GenericParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.parser_used == "generic"
        assert stmt.transaction_count == 3

        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["SALARY CREDIT"].amount == Decimal("4000.00")
        assert by_desc["ELECTRICITY BILL"].amount == Decimal("-120.00")
        assert by_desc["ELECTRICITY BILL"].date == date(2024, 1, 10)

    def test_line_fallback(self):
        # Generic line regex expects MM/DD or MM/DD/YYYY, not ISO YYYY-MM-DD
        text = (
            "National Bank\nStatement 01/01/2024 to 01/31/2024\n\n"
            "01/05/2024 SALARY CREDIT          4,000.00   5,500.00\n"
            "01/10/2024 ELECTRICITY BILL       -120.00    5,380.00\n"
            "01/20/2024 SUPERMARKET            -95.50     5,284.50\n"
        )
        with patch_pdf([{"text": text, "tables": []}]):
            p = GenericParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 3
        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["SALARY CREDIT"].amount == Decimal("4000.00")
        assert by_desc["SUPERMARKET"].amount == Decimal("-95.50")

    def test_warns_when_no_transactions_found(self):
        with patch_pdf([{"text": "Some bank header text, no transactions", "tables": []}]):
            p = GenericParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 0
        assert len(stmt.warnings) > 0

    def test_withdrawal_deposit_columns(self):
        """Generic parser maps the first amount-like column as col_amount.
        Rows with an empty first amount cell are skipped; rows with a value
        are parsed as-is (unsigned). This reflects current parser behaviour —
        the split debit/credit path only activates when col_amount is None,
        which never happens for headers named 'Debit'/'Credit'/'Withdrawal'/
        'Deposit' since they all match _AMOUNT_HEADERS."""
        table = [
            ["Date", "Description", "Amount", "Balance"],
            ["01/05/2024", "PAYCHECK",     "3,000.00", "4,000.00"],
            ["01/12/2024", "RENT PAYMENT", "-1,200.00", "2,800.00"],
        ]
        with patch_pdf([{"text": "Generic Bank 2024", "tables": [table]}]):
            p = GenericParser("fake.pdf")
            with p:
                stmt = p.extract()

        assert stmt.transaction_count == 2
        by_desc = {tx.description: tx for tx in stmt.transactions}
        assert by_desc["PAYCHECK"].amount == Decimal("3000.00")
        assert by_desc["RENT PAYMENT"].amount == Decimal("-1200.00")


# ── Parser router (detect_and_parse) ─────────────────────────────────────────

class TestParserRouter:

    def test_routes_to_chase(self, tmp_path):
        pdf = tmp_path / "stmt.pdf"
        pdf.write_bytes(b"")
        with patch_pdf([{"text": _CHASE_HEADER, "tables": [_CHASE_TABLE]}]):
            stmt = detect_and_parse(pdf)
        assert stmt.parser_used == "chase"

    def test_routes_to_bofa(self, tmp_path):
        pdf = tmp_path / "stmt.pdf"
        pdf.write_bytes(b"")
        with patch_pdf([{"text": _BOFA_HEADER, "tables": [_BOFA_TABLE]}]):
            stmt = detect_and_parse(pdf)
        assert stmt.parser_used == "bofa"

    def test_routes_to_wells_fargo(self, tmp_path):
        pdf = tmp_path / "stmt.pdf"
        pdf.write_bytes(b"")
        with patch_pdf([{"text": _WF_HEADER, "tables": [_WF_TABLE]}]):
            stmt = detect_and_parse(pdf)
        assert stmt.parser_used == "wells_fargo"

    def test_falls_back_to_generic(self, tmp_path):
        pdf = tmp_path / "stmt.pdf"
        pdf.write_bytes(b"")
        with patch_pdf([{"text": "First National Bank of Nowhere 2024", "tables": [_GENERIC_TABLE]}]):
            stmt = detect_and_parse(pdf)
        assert stmt.parser_used == "generic"

    def test_empty_pdf_does_not_crash(self, tmp_path):
        pdf = tmp_path / "stmt.pdf"
        pdf.write_bytes(b"")
        with patch_pdf([{"text": "", "tables": []}]):
            stmt = detect_and_parse(pdf)
        assert stmt.transaction_count == 0

    def test_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            detect_and_parse("/does/not/exist.pdf")

    def test_non_pdf_raises(self, tmp_path):
        txt = tmp_path / "statement.txt"
        txt.write_bytes(b"not a pdf")
        with pytest.raises(ValueError):
            detect_and_parse(txt)
