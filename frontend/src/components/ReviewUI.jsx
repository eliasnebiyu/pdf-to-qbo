/**
 * PDF-to-QBO Manual Review UI
 *
 * Dependencies (add to package.json):
 *   "react-pdf": "^7.7.0"
 *   "@react-pdf-viewer/core": "^3.12.0"  (optional alternative)
 *
 * This file is self-contained — copy into src/components/ReviewUI.jsx
 * The component expects the API /preview response shape:
 *   { bank, account_id, transactions: [{date, description, amount, balance, type}],
 *     warnings, transaction_count, total_debits, total_credits, closing_balance }
 */

import React, { useState, useCallback, useRef, useEffect } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Required for react-pdf worker
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.js`;

// ─── Design tokens ────────────────────────────────────────────────────────────
const css = `
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&display=swap');

  :root {
    --ink:       #0D1117;
    --ink-2:     #1C2128;
    --ink-3:     #2D333B;
    --muted:     #636E7B;
    --subtle:    #8B949E;
    --border:    #30363D;
    --border-lt: #21262D;
    --surface:   #161B22;
    --surface-2: #0D1117;
    --white:     #F0F6FC;
    --white-2:   #C9D1D9;
    --green:     #3FB950;
    --green-dim: #1A3A1F;
    --red:       #F85149;
    --red-dim:   #3D1C1A;
    --amber:     #E3B341;
    --amber-dim: #3A2B0A;
    --blue:      #58A6FF;
    --blue-dim:  #1A2F4A;
    --mono:      'IBM Plex Mono', monospace;
    --sans:      'DM Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  .review-root {
    font-family: var(--sans);
    background: var(--ink);
    color: var(--white);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ── Top bar ──────────────────────────────────────────────────── */
  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    height: 52px;
    background: var(--ink-2);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    gap: 16px;
  }
  .topbar-brand {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 500;
    color: var(--white);
    letter-spacing: -0.3px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .brand-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
  }
  .topbar-file {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
    background: var(--ink-3);
    padding: 3px 10px;
    border-radius: 4px;
    border: 1px solid var(--border);
    max-width: 280px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .topbar-actions { display: flex; gap: 8px; align-items: center; margin-left: auto; }

  /* ── Buttons ──────────────────────────────────────────────────── */
  .btn {
    font-family: var(--sans);
    font-size: 13px;
    font-weight: 500;
    padding: 6px 14px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--ink-3);
    color: var(--white-2);
    cursor: pointer;
    transition: all 0.15s;
    display: flex; align-items: center; gap: 6px;
  }
  .btn:hover { background: var(--ink-2); border-color: var(--subtle); color: var(--white); }
  .btn-primary {
    background: var(--green);
    border-color: var(--green);
    color: var(--ink);
    font-weight: 600;
  }
  .btn-primary:hover { background: #52d464; border-color: #52d464; color: var(--ink); }
  .btn-primary:disabled {
    background: var(--ink-3); border-color: var(--border);
    color: var(--muted); cursor: not-allowed;
  }
  .btn-icon {
    padding: 6px 8px;
    background: transparent;
    border-color: transparent;
    color: var(--subtle);
    font-size: 14px;
  }
  .btn-icon:hover { background: var(--ink-3); border-color: var(--border); color: var(--white); }
  .btn-danger { border-color: var(--red-dim); color: var(--red); }
  .btn-danger:hover { background: var(--red-dim); border-color: var(--red); }

  /* ── Status bar ───────────────────────────────────────────────── */
  .status-bar {
    display: flex;
    align-items: stretch;
    border-bottom: 1px solid var(--border);
    background: var(--ink-2);
    flex-shrink: 0;
  }
  .stat-cell {
    padding: 10px 20px;
    border-right: 1px solid var(--border);
    min-width: 140px;
  }
  .stat-cell:last-child { border-right: none; margin-left: auto; }
  .stat-label {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 3px;
  }
  .stat-val {
    font-family: var(--mono);
    font-size: 15px;
    font-weight: 500;
    color: var(--white);
  }
  .stat-val.green { color: var(--green); }
  .stat-val.red   { color: var(--red); }
  .stat-val.amber { color: var(--amber); }

  /* ── Main layout ──────────────────────────────────────────────── */
  .main-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    flex: 1;
    overflow: hidden;
    min-height: 0;
  }

  /* ── PDF pane ─────────────────────────────────────────────────── */
  .pdf-pane {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
    background: var(--surface-2);
    overflow: hidden;
  }
  .pane-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--ink-2);
    flex-shrink: 0;
  }
  .pane-label {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--subtle);
  }
  .pdf-scroll {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px 16px;
    gap: 16px;
  }
  .pdf-scroll::-webkit-scrollbar { width: 6px; }
  .pdf-scroll::-webkit-scrollbar-track { background: var(--ink); }
  .pdf-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  .pdf-page-wrap {
    box-shadow: 0 4px 24px rgba(0,0,0,0.5);
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
    transition: outline 0.15s;
  }
  .pdf-page-wrap.highlighted { outline: 2px solid var(--amber); outline-offset: 2px; }

  .pdf-controls {
    display: flex; align-items: center; gap: 8px;
  }
  .page-counter {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
  }

  /* drop zone */
  .drop-zone {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    border: 2px dashed var(--border);
    border-radius: 8px;
    margin: 24px;
    cursor: pointer;
    transition: all 0.2s;
    color: var(--muted);
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: var(--blue);
    color: var(--blue);
    background: var(--blue-dim);
  }
  .drop-icon { font-size: 36px; }
  .drop-text { font-size: 14px; font-weight: 500; }
  .drop-sub  { font-size: 12px; color: var(--muted); }

  /* ── Table pane ───────────────────────────────────────────────── */
  .table-pane {
    display: flex;
    flex-direction: column;
    background: var(--surface);
    overflow: hidden;
  }
  .table-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--ink-2);
    flex-shrink: 0;
    flex-wrap: wrap;
  }
  .search-wrap {
    position: relative;
    flex: 1;
    min-width: 140px;
  }
  .search-icon {
    position: absolute;
    left: 9px; top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    font-size: 12px;
    pointer-events: none;
  }
  .search-input {
    width: 100%;
    background: var(--ink-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 10px 5px 28px;
    font-family: var(--sans);
    font-size: 12px;
    color: var(--white);
    outline: none;
    transition: border-color 0.15s;
  }
  .search-input:focus { border-color: var(--blue); }
  .search-input::placeholder { color: var(--muted); }

  .filter-btn {
    font-family: var(--mono);
    font-size: 10px;
    padding: 4px 10px;
    border-radius: 20px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    transition: all 0.15s;
    white-space: nowrap;
  }
  .filter-btn:hover   { border-color: var(--subtle); color: var(--white-2); }
  .filter-btn.active  { background: var(--amber-dim); border-color: var(--amber); color: var(--amber); }

  .table-scroll {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }
  .table-scroll::-webkit-scrollbar { width: 6px; }
  .table-scroll::-webkit-scrollbar-track { background: var(--surface); }
  .table-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  table { width: 100%; border-collapse: collapse; }
  thead { position: sticky; top: 0; z-index: 10; }
  thead th {
    background: var(--ink-2);
    padding: 8px 12px;
    text-align: left;
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
  }
  thead th:hover { color: var(--white-2); }
  thead th.r { text-align: right; }

  tbody tr {
    border-bottom: 1px solid var(--border-lt);
    cursor: pointer;
    transition: background 0.1s;
  }
  tbody tr:hover { background: var(--ink-3); }
  tbody tr.selected { background: var(--blue-dim); }
  tbody tr.flagged td:first-child { box-shadow: inset 3px 0 0 var(--amber); }
  tbody tr.confirmed td:first-child { box-shadow: inset 3px 0 0 var(--green); }
  tbody tr.deleted { opacity: 0.4; text-decoration: line-through; }

  td {
    padding: 7px 12px;
    font-size: 12px;
    color: var(--white-2);
    vertical-align: middle;
  }

  /* inline editing */
  .editable {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    color: inherit;
    font-family: inherit;
    font-size: inherit;
    padding: 2px 4px;
    width: 100%;
    transition: border-color 0.15s, background 0.15s;
    outline: none;
  }
  .editable:focus {
    border-color: var(--blue);
    background: var(--ink-3);
  }
  .editable.amount-input {
    font-family: var(--mono);
    font-size: 12px;
    text-align: right;
    max-width: 90px;
  }

  .amount-pos { color: var(--green); font-family: var(--mono); font-size: 12px; }
  .amount-neg { color: var(--red);   font-family: var(--mono); font-size: 12px; }
  .date-cell  { font-family: var(--mono); font-size: 11px; color: var(--subtle); white-space: nowrap; }

  /* status dot */
  .dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot-ok     { background: var(--green); }
  .dot-warn   { background: var(--amber); box-shadow: 0 0 4px var(--amber); }
  .dot-err    { background: var(--red);   box-shadow: 0 0 4px var(--red); }
  .dot-del    { background: var(--muted); }

  .flag-reason {
    font-size: 10px;
    color: var(--amber);
    font-family: var(--mono);
    margin-top: 2px;
  }

  /* type badge */
  .type-badge {
    font-family: var(--mono);
    font-size: 9px;
    padding: 2px 6px;
    border-radius: 3px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    border: 1px solid;
    white-space: nowrap;
  }
  .type-DEBIT  { color: #F85149; border-color: #3D1C1A; background: #3D1C1A; }
  .type-CREDIT { color: #3FB950; border-color: #1A3A1F; background: #1A3A1F; }
  .type-INT    { color: #58A6FF; border-color: #1A2F4A; background: #1A2F4A; }
  .type-FEE    { color: #E3B341; border-color: #3A2B0A; background: #3A2B0A; }
  .type-OTHER  { color: #8B949E; border-color: #21262D; background: #21262D; }

  /* ── Footer ───────────────────────────────────────────────────── */
  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 20px;
    border-top: 1px solid var(--border);
    background: var(--ink-2);
    flex-shrink: 0;
    gap: 12px;
    flex-wrap: wrap;
  }
  .footer-summary {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .fs-item { display: flex; align-items: center; gap: 5px; }
  .fs-count { font-weight: 500; color: var(--white-2); }
  .fs-divider { color: var(--border); }

  /* ── Warning toast ────────────────────────────────────────────── */
  .warnings-bar {
    padding: 6px 20px;
    background: var(--amber-dim);
    border-bottom: 1px solid var(--amber);
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--amber);
    flex-shrink: 0;
    flex-wrap: wrap;
  }
  .warning-item {
    font-family: var(--mono);
    font-size: 11px;
  }

  /* ── Add row form ─────────────────────────────────────────────── */
  .add-row {
    display: grid;
    grid-template-columns: 80px 1fr 90px 70px 90px auto;
    gap: 6px;
    padding: 8px 12px;
    border-top: 1px solid var(--border);
    background: var(--ink-3);
    align-items: center;
    flex-shrink: 0;
  }
  .add-input {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 5px 8px;
    font-family: var(--sans);
    font-size: 12px;
    color: var(--white);
    outline: none;
    width: 100%;
  }
  .add-input:focus { border-color: var(--blue); }
  .add-label {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 2px;
  }
  .add-field { display: flex; flex-direction: column; }

  /* ── Export modal ─────────────────────────────────────────────── */
  .modal-overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.7);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
    backdrop-filter: blur(4px);
  }
  .modal {
    background: var(--ink-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 28px 32px;
    min-width: 380px;
    max-width: 460px;
  }
  .modal-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--white);
    margin-bottom: 6px;
  }
  .modal-sub { font-size: 13px; color: var(--muted); margin-bottom: 20px; }
  .format-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 20px; }
  .format-card {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 12px;
    cursor: pointer;
    transition: all 0.15s;
    text-align: center;
  }
  .format-card:hover { border-color: var(--subtle); }
  .format-card.selected { border-color: var(--green); background: var(--green-dim); }
  .format-name { font-family: var(--mono); font-size: 14px; font-weight: 500; color: var(--white); }
  .format-desc { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .modal-actions { display: flex; gap: 8px; justify-content: flex-end; }

  /* scrollbar global */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
`;

// ─── Utilities ─────────────────────────────────────────────────────────────
const fmt = (n) => {
  const v = Math.abs(parseFloat(n));
  return isNaN(v) ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const inferType = (amount, desc) => {
  const d = (desc || "").toUpperCase();
  if (d.includes("INTEREST")) return "INT";
  if (d.includes("FEE") || d.includes("CHARGE") || d.includes("PENALTY")) return "FEE";
  return parseFloat(amount) >= 0 ? "CREDIT" : "DEBIT";
};

const flagReasons = (tx) => {
  const reasons = [];
  if (tx.type === "OTHER") reasons.push("type unclassified");
  if (tx._balanceDelta !== undefined && tx.balance !== null) {
    const delta = Math.abs(tx._balanceDelta - Math.abs(parseFloat(tx.amount || 0)));
    if (delta > 0.02) reasons.push(`balance delta off by $${delta.toFixed(2)}`);
  }
  return reasons;
};

const uid = () => Math.random().toString(36).slice(2, 9);

// ─── SAMPLE DATA (replace with API response) ─────────────────────────────
const SAMPLE_TRANSACTIONS = [
  { id: uid(), date: "2024-01-03", description: "DIRECT DEPOSIT – FIFTH THIRD BANK", amount: "3200.00",  balance: "7450.00", type: "CREDIT"  },
  { id: uid(), date: "2024-01-04", description: "AMAZON.COM*2K4J8",                  amount: "-89.99",   balance: "7360.01", type: "DEBIT"   },
  { id: uid(), date: "2024-01-05", description: "WHOLEFDS MKT #10452",               amount: "-67.43",   balance: "7292.58", type: "DEBIT"   },
  { id: uid(), date: "2024-01-07", description: "NETFLIX.COM 866-579-7172",           amount: "-15.49",   balance: "7277.09", type: "DEBIT"   },
  { id: uid(), date: "2024-01-08", description: "SHELL OIL 12345678",                amount: "-54.20",   balance: "7222.89", type: "DEBIT"   },
  { id: uid(), date: "2024-01-09", description: "CHIPOTLE 2847",                      amount: "-14.75",   balance: "7208.14", type: "DEBIT",  _balanceDelta: 15.01 },
  { id: uid(), date: "2024-01-10", description: "ATM WITHDRAWAL CHASE",               amount: "-200.00",  balance: "7008.14", type: "DEBIT"   },
  { id: uid(), date: "2024-01-11", description: "SPOTIFY USA",                        amount: "-9.99",    balance: "6998.15", type: "DEBIT"   },
  { id: uid(), date: "2024-01-12", description: "WALMART SUPERCENTER #4872",          amount: "-134.22",  balance: "6863.93", type: "DEBIT"   },
  { id: uid(), date: "2024-01-14", description: "VENMO PAYMENT",                      amount: "-250.00",  balance: "6613.93", type: "DEBIT"   },
  { id: uid(), date: "2024-01-15", description: "APPLE.COM/BILL",                     amount: "-14.99",   balance: "6598.94", type: "DEBIT"   },
  { id: uid(), date: "2024-01-17", description: "DIRECT DEPOSIT – FIFTH THIRD BANK", amount: "3200.00",  balance: "9755.07", type: "CREDIT"  },
  { id: uid(), date: "2024-01-18", description: "DUKE ENERGY CORP PAYMENT",           amount: "-142.00",  balance: "9613.07", type: "DEBIT"   },
  { id: uid(), date: "2024-01-20", description: "UBER EATS",                          amount: "-32.45",   balance: "9230.62", type: "DEBIT"   },
  { id: uid(), date: "2024-01-22", description: "ZELLE PAYMENT TO JOHN SMITH",        amount: "-150.00",  balance: "8992.99", type: "DEBIT"   },
  { id: uid(), date: "2024-01-26", description: "ZELLE FROM MIKE JOHNSON",            amount: "200.00",   balance: "9105.75", type: "CREDIT"  },
  { id: uid(), date: "2024-01-28", description: "INTEREST EARNED",                    amount: "3.24",     balance: "9094.00", type: "OTHER",  },
  { id: uid(), date: "2024-01-29", description: "PLANET FITNESS",                     amount: "-24.99",   balance: "9069.01", type: "DEBIT"   },
  { id: uid(), date: "2024-01-30", description: "GRUBHUB",                            amount: "-28.50",   balance: "9040.51", type: "DEBIT"   },
];

const SAMPLE_META = {
  bank: "JPMorgan Chase",
  account_id: "xxxx1234",
  statement_start: "2024-01-01",
  statement_end: "2024-01-31",
  closing_balance: 9040.51,
  warnings: ["Row 6: balance delta mismatch — check Chipotle amount", "Row 17: Interest classified as OTHER — verify type"],
};

// ─── Sub-components ──────────────────────────────────────────────────────────

function TypeBadge({ type }) {
  return <span className={`type-badge type-${type || "OTHER"}`}>{type || "OTHER"}</span>;
}

function StatusDot({ tx, deleted }) {
  if (deleted) return <span className="dot dot-del" title="Deleted" />;
  const reasons = flagReasons(tx);
  if (reasons.length) return <span className="dot dot-warn" title={reasons.join(", ")} />;
  return <span className="dot dot-ok" title="OK" />;
}

function AddRowForm({ onAdd }) {
  const [form, setForm] = useState({ date: "", description: "", amount: "", balance: "", type: "DEBIT" });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const submit = () => {
    if (!form.date || !form.description || !form.amount) return;
    onAdd({ ...form, id: uid(), type: inferType(form.amount, form.description) });
    setForm({ date: "", description: "", amount: "", balance: "", type: "DEBIT" });
  };
  return (
    <div className="add-row">
      <div className="add-field">
        <div className="add-label">Date</div>
        <input className="add-input" type="date" value={form.date} onChange={set("date")} />
      </div>
      <div className="add-field">
        <div className="add-label">Description</div>
        <input className="add-input" placeholder="Merchant / memo" value={form.description} onChange={set("description")} />
      </div>
      <div className="add-field">
        <div className="add-label">Amount</div>
        <input className="add-input" placeholder="-0.00" value={form.amount} onChange={set("amount")} />
      </div>
      <div className="add-field">
        <div className="add-label">Balance</div>
        <input className="add-input" placeholder="0.00" value={form.balance} onChange={set("balance")} />
      </div>
      <div className="add-field">
        <div className="add-label">Type</div>
        <select className="add-input" value={form.type} onChange={set("type")}>
          {["DEBIT","CREDIT","INT","FEE","OTHER"].map(t => <option key={t}>{t}</option>)}
        </select>
      </div>
      <button className="btn btn-primary" onClick={submit} style={{ alignSelf: "flex-end" }}>+ Add</button>
    </div>
  );
}

function ExportModal({ transactions, onClose }) {
  const [fmt, setFmt] = useState("ofx");
  const active = transactions.filter(t => !t._deleted);
  const handleExport = () => {
    // In production: POST to /api/export with transactions + format
    const data = JSON.stringify({ format: fmt, transactions: active }, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = `export.${fmt}`; a.click();
    URL.revokeObjectURL(url);
    onClose();
  };
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-title">Export to QuickBooks</div>
        <div className="modal-sub">{active.length} transactions · {active.filter(t => flagReasons(t).length === 0).length} clean</div>
        <div className="format-grid">
          {[
            { id: "ofx",  name: "OFX",  desc: "Direct QBO import" },
            { id: "qfx",  name: "QFX",  desc: "Quicken format" },
            { id: "csv",  name: "CSV",  desc: "Manual import" },
          ].map(f => (
            <div key={f.id} className={`format-card ${fmt === f.id ? "selected" : ""}`} onClick={() => setFmt(f.id)}>
              <div className="format-name">{f.name}</div>
              <div className="format-desc">{f.desc}</div>
            </div>
          ))}
        </div>
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleExport}>Download {fmt.toUpperCase()}</button>
        </div>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function ReviewUI({
  pdfFile:       pdfFileProp  = null,   // File object or URL string
  transactions:  txProp       = null,   // pre-loaded transactions array
  meta:          metaProp     = null,   // bank/account metadata
  onExport:      onExportProp = null,   // callback(transactions, format)
}) {
  // ── State ──────────────────────────────────────────────────────
  const [pdfFile,       setPdfFile]       = useState(pdfFileProp);
  const [pdfName,       setPdfName]       = useState(null);
  const [numPages,      setNumPages]      = useState(null);
  const [currentPage,   setCurrentPage]   = useState(1);
  const [pdfScale,      setPdfScale]      = useState(0.85);
  const [transactions,  setTransactions]  = useState(txProp || SAMPLE_TRANSACTIONS);
  const [meta,          setMeta]          = useState(metaProp || SAMPLE_META);
  const [selectedId,    setSelectedId]    = useState(null);
  const [filter,        setFilter]        = useState("all");   // all | flagged | debit | credit
  const [search,        setSearch]        = useState("");
  const [sortKey,       setSortKey]       = useState("date");
  const [sortAsc,       setSortAsc]       = useState(true);
  const [showAddRow,    setShowAddRow]    = useState(false);
  const [showExport,    setShowExport]    = useState(false);
  const [isDragOver,    setIsDragOver]    = useState(false);
  const fileInputRef = useRef(null);

  // ── Inject styles once ─────────────────────────────────────────
  useEffect(() => {
    const id = "review-ui-styles";
    if (!document.getElementById(id)) {
      const s = document.createElement("style");
      s.id = id; s.textContent = css;
      document.head.appendChild(s);
    }
    return () => document.getElementById(id)?.remove();
  }, []);

  // ── PDF handlers ───────────────────────────────────────────────
  const handleFile = useCallback((file) => {
    if (!file || file.type !== "application/pdf") return;
    setPdfFile(file);
    setPdfName(file.name);
    setCurrentPage(1);
  }, []);

  const onDrop = (e) => {
    e.preventDefault(); setIsDragOver(false);
    handleFile(e.dataTransfer.files[0]);
  };

  // ── Inline editing ─────────────────────────────────────────────
  const updateTx = (id, field, value) => {
    setTransactions(txs => txs.map(t =>
      t.id === id ? { ...t, [field]: value, type: field === "amount" || field === "description" ? inferType(field === "amount" ? value : t.amount, field === "description" ? value : t.description) : t.type } : t
    ));
  };

  const deleteTx  = (id) => setTransactions(txs => txs.map(t => t.id === id ? { ...t, _deleted: !t._deleted } : t));
  const addTx     = (tx) => setTransactions(txs => [tx, ...txs]);
  const confirmTx = (id) => setTransactions(txs => txs.map(t => t.id === id ? { ...t, _confirmed: true } : t));
  const confirmAll = () => {
    setTransactions(txs => txs.map(t => ({ ...t, _confirmed: flagReasons(t).length === 0 ? true : t._confirmed })));
  };

  // ── Filtering & sorting ────────────────────────────────────────
  const visible = transactions
    .filter(t => {
      if (filter === "flagged") return flagReasons(t).length > 0 && !t._deleted;
      if (filter === "debit")   return parseFloat(t.amount) < 0 && !t._deleted;
      if (filter === "credit")  return parseFloat(t.amount) >= 0 && !t._deleted;
      return true;
    })
    .filter(t => {
      if (!search) return true;
      const q = search.toLowerCase();
      return t.description.toLowerCase().includes(q) || t.date.includes(q) || String(t.amount).includes(q);
    })
    .sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (sortKey === "amount") { av = parseFloat(av); bv = parseFloat(bv); }
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });

  const flagged   = transactions.filter(t => flagReasons(t).length > 0 && !t._deleted);
  const totalCr   = transactions.filter(t => !t._deleted && parseFloat(t.amount) >= 0).reduce((s,t) => s + parseFloat(t.amount), 0);
  const totalDr   = transactions.filter(t => !t._deleted && parseFloat(t.amount) < 0).reduce((s,t) => s + parseFloat(t.amount), 0);
  const cleanCount = transactions.filter(t => !t._deleted && !flagReasons(t).length).length;

  const sort = (key) => { if (sortKey === key) setSortAsc(a => !a); else { setSortKey(key); setSortAsc(true); } };
  const sortIcon = (key) => sortKey === key ? (sortAsc ? " ↑" : " ↓") : "";

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div className="review-root">

      {/* Top bar */}
      <div className="topbar">
        <div className="topbar-brand">
          <span className="brand-dot" />
          pdf-to-qbo
        </div>
        {pdfName && <div className="topbar-file">{pdfName}</div>}
        <div className="topbar-actions">
          <button className="btn" onClick={() => fileInputRef.current?.click()}>
            ↑ Upload PDF
          </button>
          <input ref={fileInputRef} type="file" accept=".pdf" style={{ display:"none" }}
            onChange={e => handleFile(e.target.files[0])} />
          <button className="btn" onClick={confirmAll}>✓ Confirm clean</button>
          <button className="btn btn-primary" disabled={flagged.length > 0}
            onClick={() => setShowExport(true)}>
            {flagged.length > 0 ? `${flagged.length} issues — fix first` : "Export to QBO →"}
          </button>
        </div>
      </div>

      {/* Warnings */}
      {meta.warnings?.length > 0 && (
        <div className="warnings-bar">
          <span>⚠</span>
          {meta.warnings.map((w, i) => <span key={i} className="warning-item">· {w}</span>)}
        </div>
      )}

      {/* Status bar */}
      <div className="status-bar">
        <div className="stat-cell">
          <div className="stat-label">Bank</div>
          <div className="stat-val">{meta.bank || "—"}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-label">Transactions</div>
          <div className="stat-val">{transactions.filter(t=>!t._deleted).length}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-label">Total Credits</div>
          <div className="stat-val green">+{fmt(totalCr)}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-label">Total Debits</div>
          <div className="stat-val red">{fmt(totalDr)}</div>
        </div>
        <div className="stat-cell">
          <div className="stat-label">Closing Balance</div>
          <div className={`stat-val ${meta.closing_balance ? "" : "amber"}`}>
            {meta.closing_balance ? fmt(meta.closing_balance) : "—"}
          </div>
        </div>
        <div className="stat-cell">
          <div className="stat-label">Issues</div>
          <div className={`stat-val ${flagged.length ? "amber" : "green"}`}>
            {flagged.length ? `${flagged.length} flagged` : "All clear"}
          </div>
        </div>
      </div>

      {/* Main split layout */}
      <div className="main-layout">

        {/* ── LEFT: PDF viewer ─────────────────────────────────── */}
        <div className="pdf-pane">
          <div className="pane-header">
            <span className="pane-label">Original PDF</span>
            {numPages && (
              <div className="pdf-controls">
                <button className="btn btn-icon" onClick={() => setCurrentPage(p => Math.max(1, p-1))} disabled={currentPage === 1}>‹</button>
                <span className="page-counter">{currentPage} / {numPages}</span>
                <button className="btn btn-icon" onClick={() => setCurrentPage(p => Math.min(numPages, p+1))} disabled={currentPage === numPages}>›</button>
                <button className="btn btn-icon" onClick={() => setPdfScale(s => Math.max(0.4, s - 0.15))}>−</button>
                <button className="btn btn-icon" onClick={() => setPdfScale(s => Math.min(1.6, s + 0.15))}>+</button>
              </div>
            )}
          </div>
          <div className="pdf-scroll">
            {pdfFile ? (
              <Document
                file={pdfFile}
                onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                loading={<div style={{ color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 12, padding: 40 }}>Loading PDF…</div>}
                error={<div style={{ color: "var(--red)", padding: 24, fontSize: 12 }}>Failed to load PDF. Ensure it is a valid bank statement.</div>}
              >
                {Array.from({ length: numPages || 0 }, (_, i) => i + 1).map(page => (
                  <div key={page} className={`pdf-page-wrap ${page === currentPage && selectedId ? "highlighted" : ""}`}>
                    <Page pageNumber={page} scale={pdfScale} renderTextLayer renderAnnotationLayer />
                  </div>
                ))}
              </Document>
            ) : (
              <div
                className={`drop-zone ${isDragOver ? "dragover" : ""}`}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={e => { e.preventDefault(); setIsDragOver(true); }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={onDrop}
              >
                <div className="drop-icon">⇪</div>
                <div className="drop-text">Drop PDF here</div>
                <div className="drop-sub">or click to browse</div>
              </div>
            )}
          </div>
        </div>

        {/* ── RIGHT: Transaction table ──────────────────────────── */}
        <div className="table-pane">
          <div className="pane-header">
            <span className="pane-label">Parsed Transactions</span>
            <button className="btn btn-icon" title="Add row" onClick={() => setShowAddRow(s=>!s)} style={{ fontSize: 16 }}>＋</button>
          </div>

          <div className="table-toolbar">
            <div className="search-wrap">
              <span className="search-icon">⌕</span>
              <input className="search-input" placeholder="Search transactions…"
                value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            {[
              { id: "all",     label: "All" },
              { id: "flagged", label: `Flagged (${flagged.length})` },
              { id: "debit",   label: "Debits" },
              { id: "credit",  label: "Credits" },
            ].map(f => (
              <button key={f.id} className={`filter-btn ${filter === f.id ? "active" : ""}`}
                onClick={() => setFilter(f.id)}>{f.label}</button>
            ))}
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 24 }} />
                  <th style={{ width: 88 }} onClick={() => sort("date")}>Date{sortIcon("date")}</th>
                  <th onClick={() => sort("description")}>Description{sortIcon("description")}</th>
                  <th style={{ width: 64 }}>Type</th>
                  <th className="r" style={{ width: 96 }} onClick={() => sort("amount")}>Amount{sortIcon("amount")}</th>
                  <th className="r" style={{ width: 88 }}>Balance</th>
                  <th style={{ width: 64 }} />
                </tr>
              </thead>
              <tbody>
                {visible.map(tx => {
                  const reasons = flagReasons(tx);
                  const isNeg   = parseFloat(tx.amount) < 0;
                  const rowCls  = [
                    selectedId === tx.id ? "selected" : "",
                    reasons.length && !tx._confirmed ? "flagged" : "",
                    tx._confirmed ? "confirmed" : "",
                    tx._deleted ? "deleted" : "",
                  ].filter(Boolean).join(" ");

                  return (
                    <tr key={tx.id} className={rowCls} onClick={() => setSelectedId(id => id === tx.id ? null : tx.id)}>
                      {/* status dot */}
                      <td onClick={e => e.stopPropagation()} style={{ paddingLeft: 14 }}>
                        <StatusDot tx={tx} deleted={tx._deleted} />
                      </td>

                      {/* date */}
                      <td className="date-cell" onClick={e => e.stopPropagation()}>
                        <input className="editable" type="date" value={tx.date}
                          onChange={e => updateTx(tx.id, "date", e.target.value)}
                          onClick={e => e.stopPropagation()} />
                      </td>

                      {/* description */}
                      <td onClick={e => e.stopPropagation()}>
                        <input className="editable" value={tx.description}
                          onChange={e => updateTx(tx.id, "description", e.target.value)}
                          onClick={e => e.stopPropagation()} />
                        {reasons.length > 0 && !tx._confirmed && (
                          <div className="flag-reason">⚠ {reasons.join(" · ")}</div>
                        )}
                      </td>

                      {/* type */}
                      <td onClick={e => e.stopPropagation()}>
                        <select className="editable" value={tx.type || "OTHER"}
                          onChange={e => updateTx(tx.id, "type", e.target.value)}
                          onClick={e => e.stopPropagation()}
                          style={{ fontFamily: "var(--mono)", fontSize: 10, textTransform: "uppercase" }}>
                          {["DEBIT","CREDIT","INT","FEE","OTHER"].map(t => <option key={t}>{t}</option>)}
                        </select>
                      </td>

                      {/* amount */}
                      <td style={{ textAlign: "right" }} onClick={e => e.stopPropagation()}>
                        <input className={`editable amount-input ${isNeg ? "amount-neg" : "amount-pos"}`}
                          value={tx.amount}
                          onChange={e => updateTx(tx.id, "amount", e.target.value)}
                          onClick={e => e.stopPropagation()} />
                      </td>

                      {/* balance */}
                      <td style={{ textAlign: "right", fontFamily: "var(--mono)", fontSize: 11, color: "var(--subtle)" }}
                        onClick={e => e.stopPropagation()}>
                        <input className="editable amount-input" style={{ color: "var(--subtle)" }}
                          value={tx.balance || ""}
                          onChange={e => updateTx(tx.id, "balance", e.target.value)}
                          onClick={e => e.stopPropagation()} />
                      </td>

                      {/* actions */}
                      <td onClick={e => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                        {reasons.length > 0 && !tx._confirmed && !tx._deleted && (
                          <button className="btn btn-icon" title="Mark OK" style={{ fontSize: 12, color: "var(--green)" }}
                            onClick={() => confirmTx(tx.id)}>✓</button>
                        )}
                        <button className={`btn btn-icon ${tx._deleted ? "" : "btn-danger"}`}
                          title={tx._deleted ? "Restore" : "Delete"}
                          onClick={() => deleteTx(tx.id)}
                          style={{ fontSize: 13 }}>
                          {tx._deleted ? "↩" : "×"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {visible.length === 0 && (
                  <tr><td colSpan={7} style={{ textAlign:"center", padding: 40, color:"var(--muted)", fontFamily:"var(--mono)", fontSize:12 }}>
                    No transactions match the current filter.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Add row form */}
          {showAddRow && <AddRowForm onAdd={(tx) => { addTx(tx); setShowAddRow(false); }} />}

          {/* Footer summary */}
          <div className="footer">
            <div className="footer-summary">
              <span className="fs-item"><span className="dot dot-ok" /> <span className="fs-count">{cleanCount}</span> confirmed</span>
              <span className="fs-divider">·</span>
              <span className="fs-item"><span className="dot dot-warn" /> <span className="fs-count">{flagged.length}</span> flagged</span>
              <span className="fs-divider">·</span>
              <span className="fs-item"><span className="dot dot-del" /> <span className="fs-count">{transactions.filter(t=>t._deleted).length}</span> deleted</span>
              <span className="fs-divider">·</span>
              <span style={{ color: "var(--subtle)" }}>{visible.length} of {transactions.length} shown</span>
            </div>
            <div style={{ display:"flex", gap: 8 }}>
              <button className="btn" onClick={() => setShowAddRow(s=>!s)}>+ Add row</button>
              <button className="btn btn-primary" disabled={flagged.length > 0}
                onClick={() => setShowExport(true)}>
                Export →
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Export modal */}
      {showExport && (
        <ExportModal transactions={transactions} onClose={() => setShowExport(false)} />
      )}
    </div>
  );
}

/**
 * INTEGRATION GUIDE
 * ─────────────────
 * 1. Install dependencies:
 *      npm install react-pdf
 *
 * 2. Copy this file to src/components/ReviewUI.jsx
 *
 * 3. Add the worker to your vite/webpack config (vite example):
 *      // vite.config.js
 *      import { viteStaticCopy } from 'vite-plugin-static-copy';
 *      plugins: [viteStaticCopy({ targets: [{
 *        src: 'node_modules/pdfjs-dist/build/pdf.worker.min.js',
 *        dest: ''
 *      }]})]
 *
 * 4. Use in your app:
 *      import ReviewUI from './components/ReviewUI';
 *
 *      // After user uploads a PDF:
 *      <ReviewUI
 *        pdfFile={file}              // File object from input[type=file]
 *        transactions={apiResponse.transactions}
 *        meta={apiResponse}
 *        onExport={(txns, format) => postToApi(txns, format)}
 *      />
 *
 *      // Or standalone (loads sample data):
 *      <ReviewUI />
 *
 * 5. API shape expected from /preview endpoint:
 *      {
 *        bank: "JPMorgan Chase",
 *        account_id: "xxxx1234",
 *        statement_start: "2024-01-01",
 *        statement_end: "2024-01-31",
 *        closing_balance: 9040.51,
 *        warnings: ["string", ...],
 *        transactions: [
 *          { date: "2024-01-03", description: "...", amount: "3200.00",
 *            balance: "7450.00", type: "CREDIT" },
 *          ...
 *        ]
 *      }
 */
