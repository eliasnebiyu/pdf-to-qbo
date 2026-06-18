# PDF to QBO Converter

Convert bank statement PDFs to QuickBooks Online-compatible OFX/QFX/CSV files — with a full-featured web UI, API-key authentication, and per-key usage quotas.

## Supported Banks (20 native parsers)

| Bank | Parser | Notes |
|------|--------|-------|
| JPMorgan Chase | Native | Table + line fallback |
| Bank of America | Native | Table + line fallback |
| Wells Fargo | Native | Table + line fallback |
| Citibank | Native | Table + section-aware |
| PNC Bank | Native | Table + business layout |
| US Bank | Native | Table + section-aware |
| TD Bank | Native | Table + line fallback |
| Capital One | Native | Table + line fallback |
| Fifth Third Bank | Native | Consumer + business layouts; pypdf page fallback |
| American Express | Native | Table + line fallback |
| Fidelity | Native | Table + line fallback |
| USAA | Native | Table + line fallback |
| Ally Bank | Native | Table + line fallback |
| Charles Schwab | Native | Table + line fallback |
| Navy Federal CU | Native | Table + line fallback |
| Truist | Native | Table + line fallback |
| KEMBA Financial CU | Native | Table + line fallback |
| Any other bank | Generic | Heuristic column detection |
| Scanned / unusual PDFs | LLM fallback | OCR + AI extraction (optional) |

## Quick Start

### Web UI

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install & build the React frontend
cd frontend && npm install && npm run build && cd ..

# Start the API (serves the UI at http://localhost:8000)
uvicorn api:app --reload --port 8000
```

Open `http://localhost:8000` — register for a free API key, upload a PDF, review transactions, and export.

### CLI

```bash
# Convert a PDF (outputs .ofx by default)
python cli.py convert statement.pdf

# Choose format
python cli.py convert statement.pdf --format qfx
python cli.py convert statement.pdf --format csv

# See all transactions during conversion
python cli.py convert statement.pdf --verbose

# List supported banks
python cli.py banks
```

## Import into QuickBooks Online

1. In QBO: **Banking → Upload transactions**
2. Select your `.ofx` or `.qfx` file
3. Map to your bank account
4. Review and accept transactions

## API Reference

### Authentication

All conversion endpoints require an `X-API-Key` header. Get a free key at the web UI, or:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
# → {"api_key": "sk-...", ...}
```

### Subscription Tiers

| Plan | Conversions / month | Price |
|------|--------------------|----|
| Free | 10 | $0 |
| Starter | 100 | $9/mo |
| Pro | Unlimited | $29/mo |

Upgrade via the web UI or `POST /auth/checkout` → redirects to Stripe Checkout.

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Health check |
| GET | `/banks` | — | List supported banks |
| POST | `/auth/register` | — | Register and get a free API key |
| GET | `/auth/usage` | Key | Current quota usage |
| POST | `/auth/checkout` | Key | Start Stripe Checkout for upgrade |
| POST | `/preview` | Key + quota | Upload PDF → JSON transaction list |
| POST | `/convert` | Key + quota | Upload PDF → download OFX/QFX/CSV |
| POST | `/batch` | Key + quota | Upload multiple PDFs → merged download |
| POST | `/batch-preview` | Key + quota | Upload multiple PDFs → merged JSON |
| POST | `/export` | Key | JSON transaction list → file download |
| POST | `/stripe/webhook` | Stripe sig | Stripe event handler |

### Example — preview a statement

```bash
curl -X POST http://localhost:8000/preview \
  -H "X-API-Key: sk-your-key" \
  -F "file=@statement.pdf" | jq .
```

### Example — convert to OFX

```bash
curl -X POST "http://localhost:8000/convert?format=ofx" \
  -H "X-API-Key: sk-your-key" \
  -F "file=@statement.pdf" \
  --output statement.ofx
```

## Web UI Features

- **Inline editing** — date, description, amount, balance, type, category
- **Split transactions** — divide one transaction into multiple line items
- **Batch upload** — merge multiple statements into a single export
- **Session persistence** — draft auto-saved to localStorage, restored on reload
- **Reconciliation** — running balance vs. actual balance delta per transaction
- **PDF viewer** — multi-page with highlight-on-hover
- **Usage widget** — live quota pill in the top bar; upgrade CTA when near limit
- **Export formats** — OFX, QFX, CSV

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./pdf_to_qbo.db` | Database connection string |
| `STRIPE_SECRET_KEY` | — | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | — | Stripe webhook signing secret |
| `STRIPE_STARTER_PRICE_ID` | — | Stripe price ID for Starter plan |
| `STRIPE_PRO_PRICE_ID` | — | Stripe price ID for Pro plan |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |
| `OPENAI_API_KEY` | — | Required only for LLM parser fallback |

Copy `.env.example` to `.env` and fill in values before running.

## Run Tests

```bash
pytest tests/ -v
```

73 tests — no external services required (PDF parsing is mocked).

## Deploy to Railway

```bash
npm install -g @railway/cli
railway login && railway init
railway up
# → https://your-app.up.railway.app
```

Set the environment variables above in the Railway dashboard.

## Project Structure

```
pdf-to-qbo/
├── cli.py                      # CLI entry point
├── api.py                      # FastAPI REST API + auth + Stripe
├── requirements.txt
├── Procfile                    # Railway/Heroku deploy
├── railway.json
├── frontend/
│   ├── src/components/
│   │   └── ReviewUI.jsx        # React upload/review/export UI
│   └── dist/                   # Built frontend (served by FastAPI)
├── src/
│   ├── models.py               # Transaction, ParsedStatement, BankAccount
│   ├── parser/
│   │   ├── __init__.py         # Bank auto-detection router
│   │   ├── base.py             # Abstract BaseParser
│   │   └── banks/              # 20 bank-specific parsers
│   ├── exporter/
│   │   ├── ofx.py              # OFX/QFX writer
│   │   └── csv_export.py       # CSV writer
│   └── utils/
│       ├── amount_parser.py    # Amount + date parsing
│       ├── dedup.py            # Cross-statement deduplication
│       └── categorizer.py      # Transaction categorization
└── tests/
    ├── test_converter.py
    └── test_parsers.py
```

## Adding a New Bank Parser

1. Create `src/parser/banks/yourbank.py`
2. Subclass `BaseParser`
3. Implement `can_parse()` and `extract()`
4. Add your class to `_PARSERS` in `src/parser/__init__.py`

```python
from src.parser.base import BaseParser
from src.models import ParsedStatement

class YourBankParser(BaseParser):
    bank_name = "Your Bank"

    def can_parse(self) -> bool:
        return "your bank" in self.full_text.lower()

    def extract(self) -> ParsedStatement:
        text    = self.full_text
        account = self.build_account(text)
        txns    = []  # your extraction logic here
        stmt    = ParsedStatement(account=account, transactions=txns,
                                  raw_page_count=self.page_count,
                                  parser_used="yourbank")
        stmt.assign_fit_ids()
        return stmt
```
