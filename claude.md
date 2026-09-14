# One-time setup
cat > /Users/nebiyuelias/Documents/GitHub/pdf_to_qbo/pdf-to-qbo/CLAUDE.md << 'EOF'
# LedgerFlow — Intuit App Marketplace Submission

## What this is
PDF → OFX/QFX/CSV converter being submitted to the Intuit App Marketplace.
API key prefix issued: `lf_` (NOT qbo_ or sk-)

## Critical OFX rules
- CHARSET:0, ENCODING:USASCII (not CHARSET:1252)
- Date format: YYYYMMDD120000[+0:GMT] (not [0:UTC])
- BANKID must be ABA routing number for known banks
- FITID suffix for collisions: underscore not hyphen (OFX §3.2.3 = alphanumeric only)
- ACCTTYPE: only CHECKING/SAVINGS/MONEYMRKT/CD in BANKACCTFROM

## Trademark rules (Intuit requirement)
- QuickBooks® with ® on first use per page/view
- Disclaimer "not affiliated with or endorsed by Intuit Inc." must appear on EVERY view that uses the trademark — not just the landing page footer
- No competitor names in marketing copy

## Key env vars (correct names)
- DB_PATH (not DATABASE_URL)
- ANTHROPIC_API_KEY (not OPENAI_API_KEY)
- STRIPE_PRICE_STARTER (not STRIPE_STARTER_PRICE_ID)
- STRIPE_PRICE_PRO (not STRIPE_PRO_PRICE_ID)
- ALLOWED_ORIGINS defaults to localhost ports, not *

## Auth
- HTTP 402 (not 429) for quota exhaustion
- verify_key_only checks status field — suspended/revoked keys get 402
EOF