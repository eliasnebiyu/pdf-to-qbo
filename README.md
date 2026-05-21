# PDF to QBO Converter

Convert bank statement PDFs to QuickBooks Online-compatible OFX/QFX/CSV files.

## Supported Banks

| Bank | Parser |
|------|--------|
| JPMorgan Chase | Native |
| Bank of America | Native |
| Wells Fargo | Native |
| All others | Generic (handles most standard layouts) |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Convert a PDF (outputs .ofx by default)
python cli.py convert statement.pdf

# Choose format
python cli.py convert statement.pdf --format qfx
python cli.py convert statement.pdf --format csv

# Specify output location
python cli.py convert statement.pdf --output ~/Downloads/

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

## Run the API

```bash
uvicorn api:app --reload --port 8000
```

Then open `http://localhost:8000/docs` for the interactive API docs.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/convert?format=ofx` | Upload PDF, download OFX |
| POST | `/convert?format=qfx` | Upload PDF, download QFX |
| POST | `/convert?format=csv` | Upload PDF, download CSV |
| POST | `/preview` | Upload PDF, get JSON summary |
| GET  | `/banks` | List supported banks |
| GET  | `/health` | Health check |

### Example API call (curl)

```bash
curl -X POST "http://localhost:8000/convert?format=ofx" \
  -F "file=@statement.pdf" \
  --output statement.ofx
```

## Run Tests

```bash
pytest tests/ -v
```

## Deploy to Railway (cheapest option — ~$5/month)

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and create project
railway login
railway init

# Deploy
railway up

# Your API is live at: https://your-app.up.railway.app
```

## Project Structure

```
pdf-to-qbo/
├── cli.py                      # CLI entry point
├── api.py                      # FastAPI REST API
├── requirements.txt
├── Procfile                    # Railway/Heroku deploy
├── railway.json
├── src/
│   ├── models.py               # Core data models (Transaction, ParsedStatement)
│   ├── parser/
│   │   ├── __init__.py         # Parser router (auto-detects bank)
│   │   ├── base.py             # Abstract base parser
│   │   └── banks/
│   │       ├── chase.py        # JPMorgan Chase
│   │       ├── bofa.py         # Bank of America
│   │       ├── wells.py        # Wells Fargo
│   │       └── generic.py      # Fallback for any bank
│   ├── exporter/
│   │   ├── ofx.py              # OFX/QFX output (direct QBO import)
│   │   └── csv_export.py       # CSV output
│   └── utils/
│       └── amount_parser.py    # Amount + date parsing utilities
└── tests/
    └── test_converter.py       # Full test suite (pytest)
```

## Adding a New Bank Parser

1. Create `src/parser/banks/yourbank.py`
2. Subclass `BaseParser`
3. Implement `can_parse()` and `extract()`
4. Add your class to the `_PARSERS` list in `src/parser/__init__.py`

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

## Roadmap

- [ ] Citi parser
- [ ] TD Bank parser
- [ ] US Bank parser
- [ ] Scanned PDF support (OCR via pytesseract)
- [ ] Stripe payment integration for per-report billing
- [ ] QuickBooks OAuth integration for direct import
- [ ] React frontend (upload UI + preview table)
