/**
 * PDF-to-QBO Manual Review UI
 *
 * Dependencies (add to package.json):
 *   "react-pdf": "^7.7.0"
 *
 * Features:
 *   • Single or multi-file PDF upload (calls /preview for each, merges client-side)
 *   • Inline editing of every field including category
 *   • Split transaction modal (one tx → two, amounts must sum to original)
 *   • Expanded OFX type options (CHECK, ATM, POS, DIRECTDEP, DIRECTDEBIT, XFER, PAYMENT)
 *   • QBO category suggestions shown + editable
 *   • Export sends category + account_type for full QBO compatibility
 *   • Statement reconciliation, PDF viewer with transaction highlighting
 */

import React, { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.js",
  import.meta.url,
).toString();

// ─── API key storage ─────────────────────────────────────────────────────────
const API_KEY_STORAGE = "parsify_api_key";
const getStoredKey  = () => localStorage.getItem(API_KEY_STORAGE) || "";
const saveStoredKey = (k) => localStorage.setItem(API_KEY_STORAGE, k.trim());

// ─── All supported OFX transaction types ──────────────────────────────────────
const TX_TYPES = [
  "DEBIT", "CREDIT", "INT", "DIV", "FEE",
  "CHECK", "ATM", "POS",
  "DIRECTDEP", "DIRECTDEBIT", "XFER", "PAYMENT",
  "OTHER",
];

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
    --purple:    #BC8CFF;
    --purple-dim:#2B1F4A;
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
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .topbar-file.multi { color: var(--blue); border-color: var(--blue-dim); }
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
  .pdf-page-wrap.hover-highlight { outline: 2px solid var(--blue); outline-offset: 2px; }

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
  .editable.category-input {
    font-size: 11px;
    color: var(--subtle);
  }
  .editable.category-input:focus { color: var(--white); }

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
  .type-DEBIT      { color: #F85149; border-color: #3D1C1A; background: #3D1C1A; }
  .type-CREDIT     { color: #3FB950; border-color: #1A3A1F; background: #1A3A1F; }
  .type-INT        { color: #58A6FF; border-color: #1A2F4A; background: #1A2F4A; }
  .type-DIV        { color: #58A6FF; border-color: #1A2F4A; background: #1A2F4A; }
  .type-FEE        { color: #E3B341; border-color: #3A2B0A; background: #3A2B0A; }
  .type-CHECK      { color: #F85149; border-color: #3D1C1A; background: #3D1C1A; }
  .type-ATM        { color: #F85149; border-color: #3D1C1A; background: #3D1C1A; }
  .type-POS        { color: #F85149; border-color: #3D1C1A; background: #3D1C1A; }
  .type-DIRECTDEP  { color: #3FB950; border-color: #1A3A1F; background: #1A3A1F; }
  .type-DIRECTDEBIT{ color: #F85149; border-color: #3D1C1A; background: #3D1C1A; }
  .type-XFER       { color: #58A6FF; border-color: #1A2F4A; background: #1A2F4A; }
  .type-PAYMENT    { color: #E3B341; border-color: #3A2B0A; background: #3A2B0A; }
  .type-OTHER      { color: #8B949E; border-color: #21262D; background: #21262D; }

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
    grid-template-columns: 80px 1fr 110px 90px 70px 90px auto;
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
    max-width: 480px;
    width: 100%;
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

  /* ── Split modal ──────────────────────────────────────────────── */
  .split-field {
    display: grid;
    grid-template-columns: 1fr 100px;
    gap: 8px;
    align-items: center;
    margin-bottom: 10px;
  }
  .split-label {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 4px;
  }
  .split-input {
    width: 100%;
    background: var(--ink-3);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 6px 10px;
    font-family: var(--sans);
    font-size: 13px;
    color: var(--white);
    outline: none;
  }
  .split-input:focus { border-color: var(--blue); }
  .split-divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 16px 0;
  }
  .split-total {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
    text-align: right;
    margin-bottom: 16px;
  }
  .split-total span { color: var(--white-2); font-weight: 500; }

  /* ── API key / registration modal ───────────────────────── */
  .key-overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.72);
    display: flex; align-items: center; justify-content: center;
    z-index: 999;
  }
  .key-modal {
    background: var(--ink-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 28px 32px;
    width: 460px;
    max-width: calc(100vw - 40px);
  }
  .key-modal h2 { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
  .key-modal p  { font-size: 13px; color: var(--muted); margin-bottom: 16px; line-height: 1.5; }

  /* Tabs */
  .reg-tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
    margin-top: 4px;
  }
  .reg-tab {
    background: none; border: none; border-bottom: 2px solid transparent;
    padding: 7px 14px; margin-bottom: -1px;
    font-family: var(--sans); font-size: 13px; font-weight: 500;
    color: var(--muted); cursor: pointer; transition: color .15s;
  }
  .reg-tab:hover { color: var(--white-2); }
  .reg-tab.active { color: var(--white); border-bottom-color: var(--blue); }

  /* Plan comparison pills */
  .plan-pills {
    display: flex; gap: 8px; margin-bottom: 16px;
  }
  .plan-pill {
    flex: 1; background: var(--ink-3); border: 1px solid var(--border-lt);
    border-radius: 6px; padding: 8px 10px; text-align: center;
  }
  .plan-pill-name { font-size: 11px; font-weight: 600; color: var(--subtle); text-transform: uppercase; letter-spacing: .04em; }
  .plan-pill-price { font-size: 15px; font-weight: 700; color: var(--white); margin: 2px 0; }
  .plan-pill-limit { font-size: 11px; color: var(--muted); }
  .plan-pill.highlight { border-color: var(--blue-dim); background: var(--blue-dim); }
  .plan-pill.highlight .plan-pill-name { color: var(--blue); }

  /* Generated key display */
  .key-display {
    display: flex; align-items: center; gap: 8px;
    background: var(--ink-3); border: 1px solid var(--green-dim);
    border-radius: 6px; padding: 8px 12px; margin-bottom: 8px;
  }
  .key-display code {
    flex: 1; font-family: var(--mono); font-size: 11px;
    color: var(--green); word-break: break-all;
  }
  .key-display button {
    flex-shrink: 0; background: none; border: 1px solid var(--border);
    border-radius: 4px; padding: 3px 8px; font-size: 11px;
    color: var(--muted); cursor: pointer; white-space: nowrap;
  }
  .key-display button:hover { color: var(--white); }

  /* Privacy note */
  .privacy-note {
    margin-top: 18px; padding: 10px 12px;
    background: var(--ink-3); border-radius: 6px;
    font-size: 11px; color: var(--muted); line-height: 1.5;
  }
  .privacy-note a { color: var(--blue); text-decoration: none; }
  .privacy-note a:hover { text-decoration: underline; }

  .key-input {
    width: 100%;
    background: var(--ink-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 9px 12px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--white);
    outline: none;
    margin-bottom: 14px;
  }
  .key-input:focus { border-color: var(--blue); }
  .key-input::placeholder { color: var(--muted); }
  .key-error { font-size: 12px; color: var(--red); margin: -10px 0 10px; }

  /* Topbar key/usage controls */
  .key-topbar-btn {
    font-family: var(--mono);
    font-size: 11px;
    background: none;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--muted);
    padding: 3px 8px;
    cursor: pointer;
    display: flex; align-items: center; gap: 4px;
  }
  .key-topbar-btn:hover { color: var(--white); border-color: var(--subtle); }
  .usage-pill {
    font-family: var(--mono); font-size: 10px;
    padding: 2px 7px; border-radius: 10px;
    border: 1px solid var(--border-lt); color: var(--muted);
    display: flex; align-items: center; gap: 3px;
  }
  .usage-pill.ok   { color: var(--green); border-color: var(--green-dim); }
  .usage-pill.warn { color: var(--amber); border-color: var(--amber-dim); }
  .usage-pill.full { color: var(--red);   border-color: var(--red-dim);   }

  /* ── Upgrade modal ───────────────────────────────────────── */
  .upgrade-modal {
    background: var(--ink-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 28px 32px;
    width: 480px;
    max-width: calc(100vw - 40px);
  }
  .upgrade-modal h2 { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
  .upgrade-modal > p { font-size: 13px; color: var(--muted); margin-bottom: 20px; line-height: 1.5; }
  .plan-cards { display: flex; gap: 12px; margin-bottom: 16px; }
  .plan-card {
    flex: 1; background: var(--ink-3);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; display: flex; flex-direction: column; gap: 6px;
  }
  .plan-card.featured { border-color: var(--blue); background: var(--blue-dim); }
  .plan-card-badge {
    font-size: 9px; font-weight: 700; letter-spacing: .06em;
    text-transform: uppercase; color: var(--blue); margin-bottom: 2px;
  }
  .plan-card-name { font-size: 14px; font-weight: 600; color: var(--white); }
  .plan-card-price { font-size: 22px; font-weight: 700; color: var(--white); }
  .plan-card-price span { font-size: 12px; font-weight: 400; color: var(--muted); }
  .plan-card-limit { font-size: 12px; color: var(--muted); }
  .plan-card button {
    margin-top: auto; padding-top: 8px;
  }
  .upgrade-error { font-size: 12px; color: var(--red); margin-bottom: 8px; }

  /* Quota-exceeded error state */
  .quota-error-card {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; height: 100%; gap: 12px; padding: 32px;
    text-align: center;
  }
  .quota-error-card h3 { font-size: 15px; font-weight: 600; color: var(--white); }
  .quota-error-card p  { font-size: 13px; color: var(--muted); max-width: 320px; line-height: 1.5; }

  /* ── Draft / session restore banner ──────────────────────── */
  .draft-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 20px;
    background: var(--amber-dim);
    border-bottom: 1px solid var(--amber);
    font-size: 13px;
    color: var(--amber);
    flex-shrink: 0;
  }
  .draft-banner-text { flex: 1; }
  .draft-banner strong { font-weight: 600; }

  /* scrollbar global */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* ── QBO preview panel ────────────────────────────────────────── */
  .qbo-preview {
    border-top: 1px solid var(--border);
    background: var(--ink-3);
    flex-shrink: 0;
    height: 168px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .qbo-preview-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 5px 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    gap: 8px;
  }
  .qbo-preview-label {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
  }
  .qbo-preview-fitid {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--subtle);
    background: var(--ink-2);
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid var(--border);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 240px;
  }
  .qbo-preview-close {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
    cursor: pointer;
    padding: 0 2px;
    margin-left: auto;
    flex-shrink: 0;
  }
  .qbo-preview-close:hover { color: var(--white); }
  .qbo-code {
    padding: 8px 16px;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.75;
    overflow: auto;
    flex: 1;
    white-space: pre;
  }
  .qbo-tag  { color: var(--blue); }
  .qbo-val  { color: var(--green); }
  .qbo-val-neg { color: var(--red); }
  .qbo-val-neutral { color: var(--white-2); }

  /* ── Mobile tab switcher (hidden on desktop) ─────────────────── */
  .pane-tabs {
    display: none;
    border-bottom: 1px solid var(--border);
    background: var(--ink-2);
    flex-shrink: 0;
  }
  .pane-tab {
    flex: 1;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 0;
    font-family: var(--sans);
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
    cursor: pointer;
    transition: color .15s;
  }
  .pane-tab.active { color: var(--white); border-bottom-color: var(--blue); }

  /* ── Tablet: ≤ 1024px ────────────────────────────────────────── */
  @media (max-width: 1024px) {
    /* Shrink topbar buttons */
    .btn { font-size: 12px; padding: 5px 10px; }
    .topbar-file { max-width: 160px; }

    /* Status bar: make it horizontally scrollable */
    .status-bar {
      overflow-x: auto;
      flex-wrap: nowrap;
    }
    .stat-cell { min-width: 110px; padding: 8px 14px; }

    /* Table font a little tighter */
    td { font-size: 11px; padding: 6px 10px; }
    thead th { padding: 7px 10px; }

    /* Add-row grid adapts */
    .add-row {
      grid-template-columns: 90px 1fr 100px 80px 60px auto;
    }
  }

  /* ── iPad portrait / large phone: ≤ 768px ───────────────────── */
  @media (max-width: 768px) {
    /* Show tab switcher, collapse side-by-side into single panel */
    .pane-tabs { display: flex; }

    .main-layout {
      grid-template-columns: 1fr;
      position: relative;
    }

    /* PDF pane hidden unless active */
    .pdf-pane {
      border-right: none;
      border-bottom: 1px solid var(--border);
    }
    .pdf-pane.pane-hidden,
    .table-pane.pane-hidden { display: none; }

    /* Topbar: hide file badge on small screens, tighten gaps */
    .topbar { padding: 0 12px; gap: 8px; }
    .topbar-file { display: none; }
    .topbar-actions { gap: 6px; }

    /* Status bar: 2 cells wide, wrap instead of scroll */
    .status-bar { flex-wrap: wrap; }
    .stat-cell {
      min-width: calc(50% - 1px);
      border-right: 1px solid var(--border);
      box-sizing: border-box;
    }
    .stat-cell:nth-child(even) { border-right: none; }
    .stat-cell:last-child { border-right: none; margin-left: 0; width: 100%; }

    /* Modals fill more of viewport */
    .modal, .key-modal, .upgrade-modal {
      min-width: unset;
      width: calc(100vw - 32px);
      padding: 20px;
    }
    .format-grid { grid-template-columns: 1fr 1fr; }
    .plan-cards  { flex-direction: column; }

    /* Footer: stack vertically */
    .footer { flex-direction: column; align-items: stretch; gap: 8px; }
    .footer > div { justify-content: center; }

    /* QBO preview shorter on small screen */
    .qbo-preview { height: 140px; }

    /* Add-row: stack into two rows */
    .add-row {
      grid-template-columns: 1fr 1fr;
    }
  }

  /* ── Phone: ≤ 480px ──────────────────────────────────────────── */
  @media (max-width: 480px) {
    /* Topbar: keep only essential controls */
    .topbar { height: 48px; padding: 0 10px; gap: 6px; }
    .topbar-brand { font-size: 12px; }
    .btn { font-size: 11px; padding: 4px 8px; }

    /* Status bar: full-width single cells */
    .stat-cell {
      min-width: 100%;
      border-right: none;
    }

    /* Table: remove balance column via hiding (use CSS not DOM) */
    .col-balance { display: none; }

    /* Smaller table text */
    td { font-size: 10px; padding: 5px 8px; }
    thead th { padding: 6px 8px; font-size: 9px; }

    /* Add-row: single column */
    .add-row { grid-template-columns: 1fr; }

    /* Footer summary: wrap items */
    .footer-summary { flex-wrap: wrap; gap: 8px; }
  }

  /* ── Demo mode banner ────────────────────────────────────────── */
  .demo-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 9px 20px;
    background: linear-gradient(90deg, rgba(88,166,255,0.08) 0%, rgba(188,140,255,0.06) 100%);
    border-bottom: 1px solid rgba(88,166,255,0.3);
    font-size: 13px;
    color: var(--blue);
    flex-shrink: 0;
    flex-wrap: wrap;
  }
  .demo-badge {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    background: var(--blue-dim);
    border: 1px solid rgba(88,166,255,0.4);
    border-radius: 4px;
    padding: 2px 8px;
    color: var(--blue);
    flex-shrink: 0;
  }
  .demo-text {
    flex: 1;
    color: var(--white-2);
    font-size: 13px;
    min-width: 0;
  }
  .demo-text strong { color: var(--white); }
  .demo-cta {
    font-family: var(--sans);
    font-size: 12px;
    font-weight: 600;
    background: var(--blue);
    color: var(--ink);
    border: none;
    border-radius: 6px;
    padding: 5px 14px;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
    transition: background 0.15s;
  }
  .demo-cta:hover { background: #79b8ff; }
`;

// ─── Utilities ─────────────────────────────────────────────────────────────
const fmt = (n) => {
  const v = Math.abs(parseFloat(n));
  return isNaN(v) ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const inferType = (amount, desc) => {
  const d = (desc || "").toUpperCase();
  if (d.includes("INTEREST")) return "INT";
  if (d.includes("DIVIDEND"))  return "DIV";
  if (d.includes("FEE") || d.includes("CHARGE") || d.includes("PENALTY")) return "FEE";
  if (d.match(/CHECK\s*#|\bCHK\b/)) return "CHECK";
  if (d.includes("ATM")) return "ATM";
  if (d.includes("POS") || d.includes("PURCHASE")) return "POS";
  if (d.includes("ZELLE") || d.includes("VENMO") || d.includes("TRANSFER")) return "XFER";
  if (d.includes("DIRECT DEP") || d.includes("ACH CREDIT") || d.includes("PAYROLL")) return "DIRECTDEP";
  if (d.includes("BILL PAY") || d.includes("ACH DEBIT")) return "PAYMENT";
  return parseFloat(amount) >= 0 ? "CREDIT" : "DEBIT";
};

const flagReasons = (tx, balanceDeltaMap = null) => {
  const reasons = [];
  if (tx.type === "OTHER") reasons.push("type unclassified");
  if (balanceDeltaMap && balanceDeltaMap[tx.id] !== undefined) {
    const balChange = Math.abs(balanceDeltaMap[tx.id]);
    const txAmt     = Math.abs(parseFloat(tx.amount || 0));
    const diff      = Math.abs(balChange - txAmt);
    if (diff > 0.02) reasons.push(`balance off by $${diff.toFixed(2)}`);
  }
  return reasons;
};

const uid = () => Math.random().toString(36).slice(2, 9);

// ─── Demo data ────────────────────────────────────────────────────────────────
const DEMO_META = {
  bank:            "JPMorgan Chase",
  account_id:      "****4729",
  account_type:    "CHECKING",
  statement_start: "2025-01-01",
  statement_end:   "2025-01-31",
  opening_balance: 4218.43,
  closing_balance: 8650.75,
  warnings:        ["Balance reconciliation: computed $8,650.75 vs stated $8,612.50 — Δ$38.25 · click a row to inspect"],
};

const DEMO_TRANSACTIONS = [
  { date:"2025-01-01", description:"ACME CORP DIRECT DEP PPD",            amount:"3200.00",  balance:"7418.43", type:"DIRECTDEP", category:"Payroll",         fit_id:"20250101DD001" },
  { date:"2025-01-02", description:"WHOLE FOODS MARKET #412 SAN JOSE CA", amount:"-124.50", balance:"7293.93", type:"POS",       category:"Groceries",        fit_id:"20250102POS001" },
  { date:"2025-01-03", description:"SHELL OIL 57442891600 SAN JOSE CA",   amount:"-65.20",  balance:"7228.73", type:"POS",       category:"Gas & Fuel",       fit_id:"20250103POS001" },
  { date:"2025-01-04", description:"NETFLIX.COM 408-5403700 CA",           amount:"-15.99",  balance:"7212.74", type:"PAYMENT",   category:"Subscriptions",    fit_id:"20250104PAY001" },
  { date:"2025-01-05", description:"CHIPOTLE MEXICAN GRILL 1482",          amount:"-13.45",  balance:"7199.29", type:"POS",       category:"Dining",           fit_id:"20250105POS001" },
  { date:"2025-01-06", description:"AMAZON PRIME MEMBERSHIP",              amount:"-14.99",  balance:"7184.30", type:"PAYMENT",   category:"Subscriptions",    fit_id:"20250106PAY001" },
  { date:"2025-01-07", description:"STARBUCKS STORE 12345 SAN JOSE CA",   amount:"-6.75",   balance:"7177.55", type:"POS",       category:"Dining",           fit_id:"20250107POS001" },
  { date:"2025-01-08", description:"ZELLE FROM MIKE JOHNSON",             amount:"250.00",  balance:"7427.55", type:"XFER",      category:"Transfer In",      fit_id:"20250108XFR001" },
  { date:"2025-01-09", description:"CHECK # 1045",                         amount:"-450.00", balance:"6977.55", type:"CHECK",     category:"Rent",             fit_id:"20250109CHK001" },
  { date:"2025-01-10", description:"CHASE ATM WITHDRAWAL 1250 BLOSSOM HL",amount:"-200.00", balance:"6777.55", type:"ATM",       category:"Cash",             fit_id:"20250110ATM001" },
  { date:"2025-01-11", description:"COSTCO WHOLESALE #0143 SAN JOSE CA",  amount:"-187.34", balance:"6590.21", type:"POS",       category:"Groceries",        fit_id:"20250111POS001" },
  { date:"2025-01-12", description:"GEICO INSURANCE PREMIUM",             amount:"-142.00", balance:"6448.21", type:"PAYMENT",   category:"Insurance",        fit_id:"20250112PAY001" },
  { date:"2025-01-14", description:"HOME DEPOT #6634 SAN JOSE CA",        amount:"-89.67",  balance:"6358.54", type:"POS",       category:"Home Improvement",  fit_id:"20250114POS001" },
  { date:"2025-01-15", description:"ACME CORP DIRECT DEP PPD",            amount:"3200.00",  balance:"9558.54", type:"DIRECTDEP", category:"Payroll",         fit_id:"20250115DD001" },
  { date:"2025-01-16", description:"TRADER JOE S 00512 SAN JOSE CA",      amount:"-78.23",  balance:"9480.31", type:"POS",       category:"Groceries",        fit_id:"20250116POS001" },
  { date:"2025-01-18", description:"VERIZON WIRELESS BILL PAY",           amount:"-95.00",  balance:"9385.31", type:"PAYMENT",   category:"Phone",            fit_id:"20250118PAY001" },
  { date:"2025-01-20", description:"ZELLE TO SARAH WILLIAMS",             amount:"-300.00", balance:"9085.31", type:"XFER",      category:"Transfer Out",     fit_id:"20250120XFR001" },
  { date:"2025-01-22", description:"INTEREST PAYMENT",                    amount:"1.24",    balance:"9086.55", type:"INT",       category:"Interest",         fit_id:"20250122INT001" },
  { date:"2025-01-24", description:"DELTA AIR LINES 00623419284124",      amount:"-423.80", balance:"8662.75", type:"POS",       category:"Travel",           fit_id:"20250124POS001" },
  { date:"2025-01-28", description:"MONTHLY SERVICE FEE",                 amount:"-12.00",  balance:"8650.75", type:"FEE",       category:"Bank Fees",        fit_id:"20250128FEE001" },
].map(tx => ({ ...tx, id: uid(), amount: String(tx.amount), balance: String(tx.balance), _demo: true }));

const buildOFXFields = (tx) => {
  const dtposted = (tx.date || "").replace(/-/g, "") + "120000[0:UTC]";
  const amount   = parseFloat(tx.amount || 0).toFixed(2);
  const fitId    = tx.fit_id || `${(tx.date || "").replace(/-/g, "")}-pending`;
  return [
    ["TRNTYPE",  tx.type  || "OTHER"],
    ["DTPOSTED", dtposted],
    ["TRNAMT",   amount],
    ["FITID",    fitId],
    ["NAME",     tx.description || ""],
    ...(tx.memo ? [["MEMO", tx.memo]] : []),
  ];
};

// Normalise tx from API response
const normaliseTx = (tx, sourceFile = null) => ({
  ...tx,
  id:          uid(),
  amount:      String(tx.amount),
  balance:     tx.balance != null ? String(tx.balance) : "",
  fit_id:      tx.fit_id   || null,
  source_page: tx.source_page || null,
  category:    tx.category || "",
  _sourceFile: sourceFile,
});


// ─── Sub-components ──────────────────────────────────────────────────────────

function TypeBadge({ type }) {
  return <span className={`type-badge type-${type || "OTHER"}`}>{type || "OTHER"}</span>;
}

function StatusDot({ tx, deleted, balanceDeltaMap }) {
  if (deleted) return <span className="dot dot-del" title="Deleted" />;
  const reasons = flagReasons(tx, balanceDeltaMap);
  if (reasons.length) return <span className="dot dot-warn" title={reasons.join(", ")} />;
  return <span className="dot dot-ok" title="OK" />;
}

// ── Add Row Form ──────────────────────────────────────────────────────────────
function AddRowForm({ onAdd }) {
  const [form, setForm] = useState({
    date: "", description: "", category: "", amount: "", balance: "", type: "DEBIT",
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const submit = () => {
    if (!form.date || !form.description || !form.amount) return;
    onAdd({ ...form, id: uid(), type: inferType(form.amount, form.description) });
    setForm({ date: "", description: "", category: "", amount: "", balance: "", type: "DEBIT" });
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
        <div className="add-label">Category</div>
        <input className="add-input" placeholder="e.g. Meals" value={form.category} onChange={set("category")} />
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
          {TX_TYPES.map(t => <option key={t}>{t}</option>)}
        </select>
      </div>
      <button className="btn btn-primary" onClick={submit} style={{ alignSelf: "flex-end" }}>+ Add</button>
    </div>
  );
}

// ── Split Transaction Modal ───────────────────────────────────────────────────
function SplitModal({ tx, onSplit, onClose }) {
  const total  = parseFloat(tx.amount || 0);
  const isNeg  = total < 0;
  const [amt1,  setAmt1]  = useState((total / 2).toFixed(2));
  const [desc1, setDesc1] = useState(tx.description || "");
  const [desc2, setDesc2] = useState(tx.description || "");
  const [cat1,  setCat1]  = useState(tx.category || "");
  const [cat2,  setCat2]  = useState(tx.category || "");

  const amt2 = (total - parseFloat(amt1 || 0)).toFixed(2);
  const sumOk = Math.abs(parseFloat(amt1 || 0) + parseFloat(amt2)) - Math.abs(total) < 0.005;

  const handleSplit = () => {
    if (!sumOk) return;
    onSplit({
      original: tx,
      part1: { ...tx, id: uid(), amount: String(parseFloat(amt1).toFixed(2)), description: desc1, category: cat1 },
      part2: { ...tx, id: uid(), amount: amt2, description: desc2, category: cat2, fit_id: null },
    });
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-title">Split Transaction</div>
        <div className="modal-sub">
          Original: {fmt(Math.abs(total))} {isNeg ? "(debit)" : "(credit)"}
        </div>

        {/* Part 1 */}
        <div className="split-label" style={{ marginBottom: 8 }}>Part 1</div>
        <div className="split-field">
          <div>
            <div className="split-label">Description</div>
            <input className="split-input" value={desc1} onChange={e => setDesc1(e.target.value)} />
          </div>
          <div>
            <div className="split-label">Amount</div>
            <input
              className="split-input"
              value={amt1}
              onChange={e => setAmt1(e.target.value)}
              style={{ color: isNeg ? "var(--red)" : "var(--green)" }}
            />
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div className="split-label">Category</div>
          <input className="split-input" value={cat1} onChange={e => setCat1(e.target.value)} placeholder="e.g. Meals" />
        </div>

        <hr className="split-divider" />

        {/* Part 2 */}
        <div className="split-label" style={{ marginBottom: 8 }}>Part 2</div>
        <div className="split-field">
          <div>
            <div className="split-label">Description</div>
            <input className="split-input" value={desc2} onChange={e => setDesc2(e.target.value)} />
          </div>
          <div>
            <div className="split-label">Amount</div>
            <input
              className="split-input"
              value={amt2}
              readOnly
              style={{ color: isNeg ? "var(--red)" : "var(--green)", opacity: 0.7 }}
            />
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <div className="split-label">Category</div>
          <input className="split-input" value={cat2} onChange={e => setCat2(e.target.value)} placeholder="e.g. Travel" />
        </div>

        <div className="split-total">
          Sum: <span style={{ color: sumOk ? "var(--green)" : "var(--red)" }}>
            {fmt(Math.abs(parseFloat(amt1 || 0)) + Math.abs(parseFloat(amt2)))}
          </span>
          {" / "}
          Total: <span>{fmt(Math.abs(total))}</span>
        </div>

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSplit} disabled={!sumOk}>
            Split
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Export Modal ──────────────────────────────────────────────────────────────
function ExportModal({ transactions, meta, onClose }) {
  const [exportFmt,  setExportFmt]  = useState("ofx");
  const [exporting,  setExporting]  = useState(false);
  const [error,      setError]      = useState(null);
  const active = transactions.filter(t => !t._deleted);

  const handleExport = async () => {
    setExporting(true);
    setError(null);
    try {
      const res = await apiFetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          format:          exportFmt,
          bank:            meta?.bank || "Unknown",
          account_id:      meta?.account_id || "unknown",
          account_type:    meta?.account_type || "CHECKING",
          statement_start: meta?.statement_start,
          statement_end:   meta?.statement_end,
          closing_balance: meta?.closing_balance,
          transactions: active.map(t => ({
            date:        t.date,
            description: t.description,
            amount:      parseFloat(t.amount),
            balance:     t.balance ? parseFloat(t.balance) : null,
            type:        t.type,
            category:    t.category || null,
          })),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Export failed" }));
        throw new Error(err.detail || "Export failed");
      }
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url; a.download = `export.${exportFmt}`; a.click();
      URL.revokeObjectURL(url);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-title">Export to QuickBooks</div>
        <div className="modal-sub">
          {active.length} transactions · {active.filter(t => flagReasons(t).length === 0).length} clean
          {meta?.account_type && ` · ${meta.account_type}`}
        </div>
        <div className="format-grid">
          {[
            { id: "ofx",  name: "OFX",  desc: "Direct QBO import" },
            { id: "qfx",  name: "QFX",  desc: "Quicken format" },
            { id: "csv",  name: "CSV",  desc: "Manual import" },
          ].map(f => (
            <div key={f.id} className={`format-card ${exportFmt === f.id ? "selected" : ""}`}
              onClick={() => setExportFmt(f.id)}>
              <div className="format-name">{f.name}</div>
              <div className="format-desc">{f.desc}</div>
            </div>
          ))}
        </div>
        {error && (
          <div style={{ color: "var(--red)", fontSize: 12, marginBottom: 12, fontFamily: "var(--mono)" }}>
            ⚠ {error}
          </div>
        )}
        <div className="modal-actions">
          <button className="btn" onClick={onClose} disabled={exporting}>Cancel</button>
          <button className="btn btn-primary" onClick={handleExport} disabled={exporting}>
            {exporting ? "Exporting…" : `Download ${exportFmt.toUpperCase()}`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── API Key Modal ─────────────────────────────────────────────────────────────
function ApiKeyModal({ onSave }) {
  const [tab,         setTab]        = useState("register");
  const [email,       setEmail]      = useState("");
  const [busy,        setBusy]       = useState(false);
  const [regError,    setRegError]   = useState(null);
  const [newKey,      setNewKey]     = useState(null);
  const [copied,      setCopied]     = useState(false);
  const [existingVal, setExistingVal] = useState("");
  const [pasteError,  setPasteError] = useState(null);

  const handleRegister = async () => {
    if (!email) return;
    setBusy(true);
    setRegError(null);
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Registration failed");
      setNewKey(data.api_key);
    } catch (err) {
      setRegError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(newKey).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handlePasteSave = () => {
    if (!existingVal.trim()) return;
    if (!existingVal.trim().startsWith("qbo_")) {
      setPasteError("Keys start with qbo_ — double-check and try again.");
      return;
    }
    onSave(existingVal);
  };

  // ── Success screen ────────────────────────────────────────────────────────
  if (newKey) {
    return (
      <div className="key-overlay">
        <div className="key-modal">
          <h2>🎉 You're all set!</h2>
          <p>
            Your free API key is ready. <strong>Save it somewhere safe</strong> —
            we can't show it again after you close this window.
          </p>
          <div className="key-display">
            <code>{newKey}</code>
            <button onClick={handleCopy}>{copied ? "✓ Copied" : "Copy"}</button>
          </div>
          <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20 }}>
            Free plan: <strong style={{ color: "var(--white-2)" }}>10 PDFs / month</strong>.
            Upgrade anytime from the app.
          </p>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button className="btn btn-primary" onClick={() => onSave(newKey)}>
              Start converting →
            </button>
          </div>
          <div className="privacy-note">
            🔒 We never sell your data. PDFs processed by AI fallback are sent to
            Anthropic's API (not used for training).{" "}
            <a href="https://www.anthropic.com/privacy" target="_blank" rel="noopener noreferrer">
              Anthropic privacy →
            </a>
          </div>
        </div>
      </div>
    );
  }

  // ── Main modal ────────────────────────────────────────────────────────────
  return (
    <div className="key-overlay">
      <div className="key-modal">
        <h2>🔑 API key required</h2>
        <p>All PDF conversions require an API key. Choose an option below.</p>

        {/* Tabs */}
        <div className="reg-tabs">
          {[["register", "Get a free key"], ["existing", "I have a key"]].map(([id, label]) => (
            <button
              key={id}
              className={`reg-tab${tab === id ? " active" : ""}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* ── Register tab ── */}
        {tab === "register" && (
          <>
            <div className="plan-pills">
              <div className="plan-pill highlight">
                <div className="plan-pill-name">Free</div>
                <div className="plan-pill-price">$0</div>
                <div className="plan-pill-limit">10 PDFs / mo</div>
              </div>
              <div className="plan-pill">
                <div className="plan-pill-name">Starter</div>
                <div className="plan-pill-price">$9</div>
                <div className="plan-pill-limit">100 PDFs / mo</div>
              </div>
              <div className="plan-pill">
                <div className="plan-pill-name">Pro</div>
                <div className="plan-pill-price">$29</div>
                <div className="plan-pill-limit">Unlimited</div>
              </div>
            </div>

            <input
              className="key-input"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={e => { setEmail(e.target.value); setRegError(null); }}
              onKeyDown={e => e.key === "Enter" && handleRegister()}
              autoFocus
            />
            {regError && <p className="key-error">{regError}</p>}

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>
                No credit card needed for free tier
              </span>
              <button
                className="btn btn-primary"
                disabled={!email || busy}
                onClick={handleRegister}
              >
                {busy ? "Creating…" : "Get my free key →"}
              </button>
            </div>
          </>
        )}

        {/* ── Existing key tab ── */}
        {tab === "existing" && (
          <>
            <input
              className="key-input"
              placeholder="qbo_…"
              value={existingVal}
              onChange={e => { setExistingVal(e.target.value); setPasteError(null); }}
              onKeyDown={e => e.key === "Enter" && handlePasteSave()}
              autoFocus
            />
            {pasteError && <p className="key-error">{pasteError}</p>}
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                className="btn btn-primary"
                disabled={!existingVal.trim()}
                onClick={handlePasteSave}
              >
                Save key →
              </button>
            </div>
          </>
        )}

        <div className="privacy-note">
          🔒 PDFs processed by AI fallback are sent to Anthropic's API (not used for training).{" "}
          <a href="https://www.anthropic.com/privacy" target="_blank" rel="noopener noreferrer">
            Privacy policy →
          </a>
        </div>
      </div>
    </div>
  );
}

// ─── Upgrade modal ────────────────────────────────────────────────────────────
function UpgradeModal({ onClose, onCheckout, busy, error }) {
  return (
    <div className="key-overlay">
      <div className="upgrade-modal">
        <h2>🚀 Upgrade your plan</h2>
        <p>
          You've hit your monthly limit. Upgrade to keep converting —
          no interruptions, instant activation.
        </p>
        <div className="plan-cards">
          <div className="plan-card">
            <div className="plan-card-name">Starter</div>
            <div className="plan-card-price">$9<span>/mo</span></div>
            <div className="plan-card-limit">100 PDFs / month</div>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => onCheckout("starter")}
            >
              {busy ? "Redirecting…" : "Choose Starter →"}
            </button>
          </div>
          <div className="plan-card featured">
            <div className="plan-card-badge">Most popular</div>
            <div className="plan-card-name">Pro</div>
            <div className="plan-card-price">$29<span>/mo</span></div>
            <div className="plan-card-limit">Unlimited PDFs</div>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => onCheckout("pro")}
            >
              {busy ? "Redirecting…" : "Choose Pro →"}
            </button>
          </div>
        </div>
        {error && <p className="upgrade-error">⚠ {error}</p>}
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            className="btn"
            style={{ color: "var(--muted)" }}
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
        </div>
        <div className="privacy-note" style={{ marginTop: 14 }}>
          Payments are processed by Stripe. You can cancel anytime from your
          billing dashboard.
        </div>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function ReviewUI({
  pdfFile:       pdfFileProp  = null,
  transactions:  txProp       = null,
  meta:          metaProp     = null,
  onExport:      onExportProp = null,
}) {
  // ── Demo mode (URL param: /app?demo=true) ─────────────────────
  const [searchParams] = useSearchParams();
  const [isDemo, setIsDemo] = useState(() => searchParams.get("demo") === "true");

  // ── State ──────────────────────────────────────────────────────
  const [pdfFile,       setPdfFile]       = useState(pdfFileProp);
  const [pdfName,       setPdfName]       = useState(isDemo ? "Chase_January_2025_demo.pdf" : null);
  const [isMultiFile,   setIsMultiFile]   = useState(false);
  const [numPages,      setNumPages]      = useState(null);
  const [currentPage,   setCurrentPage]   = useState(1);
  const [pdfScale,      setPdfScale]      = useState(0.85);
  const [transactions,  setTransactions]  = useState(() => isDemo ? DEMO_TRANSACTIONS : (txProp || []));
  const [meta,          setMeta]          = useState(() => isDemo ? DEMO_META : (metaProp || {}));
  const [loading,       setLoading]       = useState(false);
  const [loadingMsg,    setLoadingMsg]    = useState("Parsing PDF…");
  const [apiError,      setApiError]      = useState(null);
  const [quotaExceeded, setQuotaExceeded] = useState(false);
  const [showUpgrade,   setShowUpgrade]   = useState(false);
  const [upgradeBusy,   setUpgradeBusy]   = useState(false);
  const [upgradeError,  setUpgradeError]  = useState(null);
  const [selectedId,    setSelectedId]    = useState(null);
  const [splitTxId,     setSplitTxId]     = useState(null);
  const [filter,        setFilter]        = useState("all");
  const [search,        setSearch]        = useState("");
  const [sortKey,       setSortKey]       = useState("date");
  const [sortAsc,       setSortAsc]       = useState(true);
  const [showAddRow,    setShowAddRow]    = useState(false);
  const [showExport,    setShowExport]    = useState(false);
  const [isDragOver,    setIsDragOver]    = useState(false);
  const [hoveredId,     setHoveredId]     = useState(null);
  const fileInputRef = useRef(null);
  const pageRefs     = useRef({});

  // ── API key ────────────────────────────────────────────────────
  const [apiKey,       setApiKey]       = useState(getStoredKey);
  const [showKeyModal, setShowKeyModal] = useState(!getStoredKey() && !isDemo);
  const [usage,        setUsage]        = useState(null); // { plan, plan_label, used, remaining, limit }

  // ── Report parsing error ────────────────────────────────────────
  const [showReport,      setShowReport]      = useState(false);
  const [reportDesc,      setReportDesc]      = useState("");
  const [reportEmail,     setReportEmail]     = useState("");
  const [reportSubmitted, setReportSubmitted] = useState(false);
  const [reportBusy,      setReportBusy]      = useState(false);

  // Mobile pane tab: "pdf" | "table"
  const [activePane, setActivePane] = useState("table");

  const submitReport = async () => {
    if (reportDesc.trim().length < 10) return;
    setReportBusy(true);
    try {
      await fetch("/api/report-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email:       reportEmail || "anonymous",
          bank:        pdfName || "",
          description: reportDesc,
          api_key:     apiKey || "",
        }),
      });
      setReportSubmitted(true);
    } finally {
      setReportBusy(false);
    }
  };

  const saveApiKey = (k) => {
    saveStoredKey(k);
    setApiKey(k.trim());
    setShowKeyModal(false);
  };

  // Fetch quota info whenever the key changes
  useEffect(() => {
    if (!apiKey) { setUsage(null); return; }
    fetch("/api/auth/usage", { headers: { "X-API-Key": apiKey } })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setUsage(d))
      .catch(() => {});
  }, [apiKey]);

  // Authenticated fetch — attaches X-API-Key to every request
  const apiFetch = useCallback((url, opts = {}) => {
    const headers = { ...(opts.headers || {}), "X-API-Key": apiKey };
    return fetch(url, { ...opts, headers });
  }, [apiKey]);

  // Stripe upgrade flow
  const handleUpgrade = useCallback(async (plan) => {
    setUpgradeBusy(true);
    setUpgradeError(null);
    try {
      const res = await apiFetch("/api/auth/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan,
          success_url: window.location.href,
          cancel_url:  window.location.href,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Checkout failed");
      window.location.href = data.checkout_url;
    } catch (err) {
      setUpgradeError(err.message);
      setUpgradeBusy(false);
    }
  }, [apiFetch]);

  // ── Session persistence (localStorage) ────────────────────────
  const DRAFT_KEY = "parsify_draft_v1";
  const [draftBanner, setDraftBanner] = useState(false);

  // On mount: check for a saved draft (skip in demo mode)
  useEffect(() => {
    if (isDemo) return;
    try {
      const saved = localStorage.getItem(DRAFT_KEY);
      if (saved) {
        const { transactions: savedTxns, meta: savedMeta, pdfName: savedName } = JSON.parse(saved);
        if (savedTxns?.length > 0) {
          setDraftBanner({ txns: savedTxns, meta: savedMeta, name: savedName });
        }
      }
    } catch (_) { /* ignore corrupt draft */ }
  }, []);

  // Auto-save draft whenever transactions or meta change (debounced 1 s, skip demo)
  useEffect(() => {
    if (isDemo || transactions.length === 0) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({
          transactions,
          meta,
          pdfName,
          savedAt: new Date().toISOString(),
        }));
      } catch (_) { /* storage full — ignore */ }
    }, 1000);
    return () => clearTimeout(timer);
  }, [transactions, meta, pdfName]);

  const resumeDraft = () => {
    if (!draftBanner) return;
    setTransactions(draftBanner.txns);
    setMeta(draftBanner.meta || {});
    setPdfName(draftBanner.name || "Restored draft");
    setIsMultiFile(!!(draftBanner.name?.includes("file")));
    setDraftBanner(false);
  };

  const discardDraft = () => {
    localStorage.removeItem(DRAFT_KEY);
    setDraftBanner(false);
  };

  const clearSession = () => {
    localStorage.removeItem(DRAFT_KEY);
    setTransactions([]);
    setMeta({});
    setPdfFile(null);
    setPdfName(null);
    setIsMultiFile(false);
    setApiError(null);
  };

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

  // ── Scroll PDF to the active page ──────────────────────────────
  useEffect(() => {
    const el = pageRefs.current[currentPage];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [currentPage]);

  // ── Per-row balance delta map ──────────────────────────────────
  const balanceDeltaMap = useMemo(() => {
    const withBal = transactions
      .filter(t => !t._deleted && t.balance != null && t.balance !== "")
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    const map = {};
    for (let i = 1; i < withBal.length; i++) {
      map[withBal[i].id] =
        parseFloat(withBal[i].balance) - parseFloat(withBal[i - 1].balance);
    }
    return map;
  }, [transactions]);

  // ── Statement-level reconciliation ────────────────────────────
  const reconciliation = useMemo(() => {
    const closingBal = meta.closing_balance != null ? parseFloat(meta.closing_balance) : null;
    if (closingBal == null) return { status: "no-data" };
    const activeTxs = transactions.filter(t => !t._deleted);
    if (activeTxs.length === 0) return { status: "no-data" };
    const netFlow = activeTxs.reduce((s, t) => s + parseFloat(t.amount || 0), 0);
    let openingBal = meta.opening_balance != null ? parseFloat(meta.opening_balance) : null;
    if (openingBal == null) {
      const sorted = activeTxs
        .filter(t => t.balance != null && t.balance !== "")
        .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
      if (sorted.length > 0)
        openingBal = parseFloat(sorted[0].balance) - parseFloat(sorted[0].amount || 0);
    }
    if (openingBal != null) {
      const computed = openingBal + netFlow;
      const diff     = computed - closingBal;
      return { status: Math.abs(diff) < 0.02 ? "ok" : "off", diff, computed, closing: closingBal, opening: openingBal, method: "sum" };
    }
    const sorted = activeTxs
      .filter(t => t.balance != null && t.balance !== "")
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    if (sorted.length > 0) {
      const lastBal = parseFloat(sorted[sorted.length - 1].balance);
      const diff    = lastBal - closingBal;
      return { status: Math.abs(diff) < 0.02 ? "ok" : "off", diff, closing: closingBal, method: "last-balance" };
    }
    return { status: "no-data" };
  }, [transactions, meta]);

  // ── PDF text-layer highlight for hovered transaction ──────────
  const customTextRenderer = useCallback(({ str }) => {
    if (!hoveredId) return str;
    const tx = transactions.find(t => t.id === hoveredId);
    if (!tx || !str) return str;
    const month = parseInt((tx.date || "").slice(5, 7), 10);
    const day   = parseInt((tx.date || "").slice(8, 10), 10);
    const dateVariants = [
      `${month}/${day}`, `0${month}/${day}`, `${month}/0${day}`, `0${month}/0${day}`,
    ];
    const isDate = dateVariants.some(v => str === v || str.startsWith(v + "/"));
    const absAmt  = Math.abs(parseFloat(tx.amount || 0));
    const amtFmt  = absAmt.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const amtFixed = absAmt.toFixed(2);
    const isAmount = str === amtFmt || str === amtFixed || str.endsWith(amtFmt) || str.endsWith(amtFixed);
    if (isDate || isAmount)
      return `<mark style="background:rgba(88,166,255,0.38);border-radius:2px;padding:0 1px">${str}</mark>`;
    return str;
  }, [hoveredId, transactions]);

  // ── Single file handler ────────────────────────────────────────
  const handleSingleFile = useCallback(async (file) => {
    setPdfFile(file);
    setPdfName(file.name);
    setIsMultiFile(false);
    setCurrentPage(1);
    setTransactions([]);
    setMeta({});
    setApiError(null);
    setQuotaExceeded(false);
    setLoading(true);
    setLoadingMsg("Parsing PDF…");
    setActivePane("pdf"); // show PDF while loading on mobile

    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await apiFetch("/api/preview", { method: "POST", body: formData });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Failed to parse PDF" }));
        if (res.status === 401) { setShowKeyModal(true); throw new Error("Invalid or missing API key."); }
        if (res.status === 429) { setQuotaExceeded(true); throw new Error("quota"); }
        throw new Error(err.detail || "Failed to parse PDF");
      }
      const data = await res.json();
      setMeta(data);
      setTransactions(data.transactions.map(tx => normaliseTx(tx, file.name)));
      setIsDemo(false);      // exit demo mode once a real PDF is parsed
      setActivePane("table"); // switch to table pane on mobile after parse
      // Refresh quota display after a successful parse
      if (apiKey) {
        fetch("/api/auth/usage", { headers: { "X-API-Key": apiKey } })
          .then(r => r.ok ? r.json() : null)
          .then(d => d && setUsage(d))
          .catch(() => {});
      }
    } catch (err) {
      setApiError(err.message);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, apiKey]);

  // ── Multi-file handler ─────────────────────────────────────────
  // Calls /preview for each file, then merges + client-side deduplicates
  const handleMultipleFiles = useCallback(async (files) => {
    const pdfs = Array.from(files).filter(f => f.type === "application/pdf");
    if (pdfs.length === 0) return;
    if (pdfs.length === 1) return handleSingleFile(pdfs[0]);

    setPdfFile(pdfs[0]);
    setPdfName(`${pdfs.length} files`);
    setIsMultiFile(true);
    setCurrentPage(1);
    setTransactions([]);
    setMeta({});
    setApiError(null);
    setQuotaExceeded(false);
    setLoading(true);

    const allTxns   = [];
    let primaryMeta = null;
    const allWarnings = [];

    for (let i = 0; i < pdfs.length; i++) {
      setLoadingMsg(`Parsing ${i + 1}/${pdfs.length}: ${pdfs[i].name}…`);
      try {
        const formData = new FormData();
        formData.append("file", pdfs[i]);
        const res = await apiFetch("/api/preview", { method: "POST", body: formData });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: "Failed" }));
          if (res.status === 429) { setQuotaExceeded(true); break; }
          allWarnings.push(`${pdfs[i].name}: ${err.detail || "Failed to parse"}`);
          continue;
        }
        const data = await res.json();
        if (!primaryMeta) primaryMeta = data;
        allWarnings.push(...(data.warnings || []).map(w => `${pdfs[i].name}: ${w}`));
        allTxns.push(...data.transactions.map(tx => normaliseTx(tx, pdfs[i].name)));
      } catch (err) {
        allWarnings.push(`${pdfs[i].name}: ${err.message}`);
      }
    }

    if (!primaryMeta) {
      setApiError("No PDFs could be parsed. " + allWarnings.join("; "));
      setLoading(false);
      return;
    }

    // Sort by date then deduplicate on (date, amount, description)
    const seen   = new Set();
    const merged = allTxns
      .sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : 0)
      .filter(tx => {
        const key = `${tx.date}|${tx.amount}|${(tx.description || "").toLowerCase()}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });

    setMeta({ ...primaryMeta, warnings: allWarnings });
    setTransactions(merged);
    setIsDemo(false);       // exit demo mode on multi-file parse
    setActivePane("table"); // switch to table pane on mobile after multi-file parse
    setLoading(false);
  }, [handleSingleFile]);

  // ── File input glue ────────────────────────────────────────────
  const handleFiles = useCallback((fileList) => {
    if (!fileList || fileList.length === 0) return;
    const arr = Array.from(fileList).filter(f => f.type === "application/pdf");
    if (arr.length === 0) return;
    arr.length === 1 ? handleSingleFile(arr[0]) : handleMultipleFiles(arr);
  }, [handleSingleFile, handleMultipleFiles]);

  const onDrop = (e) => {
    e.preventDefault(); setIsDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  // ── Inline editing ─────────────────────────────────────────────
  const updateTx = (id, field, value) =>
    setTransactions(txs => txs.map(t => {
      if (t.id !== id) return t;
      const updated = { ...t, [field]: value };
      if (field === "amount" || field === "description")
        updated.type = inferType(field === "amount" ? value : t.amount, field === "description" ? value : t.description);
      return updated;
    }));

  const deleteTx   = (id) => setTransactions(txs => txs.map(t => t.id === id ? { ...t, _deleted: !t._deleted } : t));
  const addTx      = (tx) => setTransactions(txs => [tx, ...txs]);
  const confirmTx  = (id) => setTransactions(txs => txs.map(t => t.id === id ? { ...t, _confirmed: true } : t));
  const confirmAll = ()   => setTransactions(txs => txs.map(t => ({
    ...t, _confirmed: flagReasons(t, balanceDeltaMap).length === 0 ? true : t._confirmed,
  })));

  const splitTx = ({ original, part1, part2 }) =>
    setTransactions(txs => {
      const idx = txs.findIndex(t => t.id === original.id);
      if (idx === -1) return txs;
      const next = [...txs];
      next.splice(idx, 1, part1, part2);
      return next;
    });

  // ── Filtering & sorting ────────────────────────────────────────
  const visible = transactions
    .filter(t => {
      if (filter === "flagged") return flagReasons(t, balanceDeltaMap).length > 0 && !t._deleted;
      if (filter === "debit")   return parseFloat(t.amount) < 0 && !t._deleted;
      if (filter === "credit")  return parseFloat(t.amount) >= 0 && !t._deleted;
      return true;
    })
    .filter(t => {
      if (!search) return true;
      const q = search.toLowerCase();
      return (
        (t.description || "").toLowerCase().includes(q) ||
        (t.date || "").includes(q) ||
        String(t.amount).includes(q) ||
        (t.category || "").toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (sortKey === "amount") { av = parseFloat(av); bv = parseFloat(bv); }
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });

  const flagged    = transactions.filter(t => flagReasons(t, balanceDeltaMap).length > 0 && !t._deleted);
  const totalCr    = transactions.filter(t => !t._deleted && parseFloat(t.amount) >= 0).reduce((s, t) => s + parseFloat(t.amount), 0);
  const totalDr    = transactions.filter(t => !t._deleted && parseFloat(t.amount) < 0).reduce((s, t) => s + parseFloat(t.amount), 0);
  const cleanCount = transactions.filter(t => !t._deleted && !flagReasons(t, balanceDeltaMap).length).length;

  const sort     = (key) => { if (sortKey === key) setSortAsc(a => !a); else { setSortKey(key); setSortAsc(true); } };
  const sortIcon = (key) => sortKey === key ? (sortAsc ? " ↑" : " ↓") : "";

  const splitTx_ = transactions.find(t => t.id === splitTxId);

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div className="review-root">

      {/* API key modal — shown on first visit or when key is missing */}
      {showKeyModal && <ApiKeyModal onSave={saveApiKey} />}
      {showUpgrade  && (
        <UpgradeModal
          onClose={() => setShowUpgrade(false)}
          onCheckout={handleUpgrade}
          busy={upgradeBusy}
          error={upgradeError}
        />
      )}

      {/* ── Report parsing error modal ── */}
      {showReport && (
        <div style={{
          position:"fixed",inset:0,background:"rgba(0,0,0,0.7)",zIndex:9999,
          display:"flex",alignItems:"center",justifyContent:"center",padding:20,
        }} onClick={e => e.target === e.currentTarget && setShowReport(false)}>
          <div style={{
            background:"var(--ink-2)",border:"1px solid var(--border)",
            borderRadius:14,padding:28,width:"100%",maxWidth:460,
            display:"flex",flexDirection:"column",gap:16,
          }}>
            {reportSubmitted ? (
              <>
                <p style={{color:"var(--green)",fontWeight:700,margin:0,fontSize:17}}>✓ Report received</p>
                <p style={{color:"var(--muted)",margin:0,fontSize:14}}>
                  Thanks — we'll investigate and improve the parser. If you left your email we'll follow up.
                </p>
                <button className="btn btn-primary" onClick={() => setShowReport(false)}>Close</button>
              </>
            ) : (
              <>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                  <h3 style={{margin:0,color:"var(--white)",fontSize:16,fontWeight:700}}>🐛 Report a parsing issue</h3>
                  <button onClick={() => setShowReport(false)} style={{background:"none",border:"none",color:"var(--muted)",cursor:"pointer",fontSize:18}}>×</button>
                </div>
                <p style={{color:"var(--muted)",margin:0,fontSize:13}}>
                  Something look wrong? Tell us what happened and we'll fix the parser.
                </p>
                <div style={{display:"flex",flexDirection:"column",gap:10}}>
                  <input
                    type="email"
                    placeholder="Your email (optional, for follow-up)"
                    value={reportEmail}
                    onChange={e => setReportEmail(e.target.value)}
                    style={{
                      background:"var(--ink-3)",border:"1px solid var(--border)",
                      borderRadius:7,padding:"9px 12px",color:"var(--white)",
                      fontSize:13,outline:"none",fontFamily:"var(--sans)",
                    }}
                  />
                  <textarea
                    placeholder="What's wrong? e.g. 'Wrong amounts on deposits', 'Missing 3 transactions', 'Dates are off by 1 day'…"
                    value={reportDesc}
                    onChange={e => setReportDesc(e.target.value)}
                    rows={4}
                    style={{
                      background:"var(--ink-3)",border:"1px solid var(--border)",
                      borderRadius:7,padding:"9px 12px",color:"var(--white)",
                      fontSize:13,outline:"none",resize:"vertical",
                      fontFamily:"var(--sans)",lineHeight:1.6,
                    }}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  disabled={reportBusy || reportDesc.trim().length < 10}
                  onClick={submitReport}
                  style={{alignSelf:"flex-end"}}
                >
                  {reportBusy ? "Sending…" : "Send report"}
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Draft restore banner */}
      {draftBanner && (
        <div className="draft-banner">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm8-3a1 1 0 011 1v2.5l1.5 1.5a1 1 0 01-1.4 1.4l-1.8-1.8A1 1 0 017 8.5V6a1 1 0 011-1z"
              fill="currentColor" fillRule="evenodd" clipRule="evenodd"/>
          </svg>
          <span className="draft-banner-text">
            <strong>Unsaved session found</strong> — {draftBanner.txns.length} transactions
            {draftBanner.name ? ` from "${draftBanner.name}"` : ""}.
            Restore it or start fresh?
          </span>
          <button className="btn btn-primary" style={{ padding: "4px 14px", fontSize: 12 }}
            onClick={resumeDraft}>
            Resume
          </button>
          <button className="btn" style={{ padding: "4px 14px", fontSize: 12 }}
            onClick={discardDraft}>
            Discard
          </button>
        </div>
      )}

      {/* Top bar */}
      <div className="topbar">
        <div className="topbar-brand">
          <span className="brand-dot" />
          <span style={{ color: "var(--green)" }}>Par</span>sify
        </div>
        {pdfName && (
          <div className={`topbar-file ${isMultiFile ? "multi" : ""}`}>
            {isMultiFile ? `📂 ${pdfName} merged` : pdfName}
          </div>
        )}
        <div className="topbar-actions">
          <button className="key-topbar-btn" onClick={() => setShowKeyModal(true)}
            title={apiKey || "No API key set"}>
            🔑 {apiKey ? `…${apiKey.slice(-6)}` : "Add key"}
          </button>
          {usage && (() => {
            const limit = usage.monthly_limit;
            const used  = usage.conversions_used;
            const rem   = usage.conversions_remaining;
            const isUnlimited = limit === null;
            const pct = isUnlimited ? 0 : used / limit;
            const cls = isUnlimited ? "ok" : pct >= 1 ? "full" : pct >= 0.8 ? "warn" : "ok";
            const label = isUnlimited
              ? `${usage.plan_label} · ∞`
              : `${usage.plan_label} · ${rem}/${limit}`;
            return (<>
              <span className={`usage-pill ${cls}`} title={`${usage.used} used this period`}>
                {label}
              </span>
              {(cls === "full" || cls === "warn") && (
                <button
                  className="btn"
                  style={{ fontSize: 11, color: "var(--amber)", borderColor: "var(--amber-dim)", padding: "3px 8px" }}
                  onClick={() => { setUpgradeError(null); setShowUpgrade(true); }}
                >
                  ↑ Upgrade
                </button>
              )}
            </>);
          })()}
          {transactions.length > 0 && !isDemo && (
            <button
              className="btn"
              title="Report a parsing error"
              onClick={() => { setReportSubmitted(false); setReportDesc(""); setShowReport(true); }}
              style={{ color: "var(--muted)", fontSize: 12 }}
            >
              🐛 Report
            </button>
          )}
          <button className="btn" onClick={() => fileInputRef.current?.click()}>
            ↑ Upload PDF
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            multiple
            style={{ display: "none" }}
            onChange={e => handleFiles(e.target.files)}
          />
          <button className="btn" onClick={confirmAll}>✓ Confirm clean</button>
          {transactions.length > 0 && !isDemo && (
            <button className="btn" onClick={clearSession}
              style={{ color: "var(--muted)", borderColor: "var(--border-lt)" }}
              title="Clear session and discard draft">
              ✕ Clear
            </button>
          )}
          <button
            className="btn btn-primary"
            disabled={!isDemo && flagged.length > 0}
            onClick={() => isDemo ? setShowKeyModal(true) : setShowExport(true)}
          >
            {isDemo
              ? "Sign up to export →"
              : flagged.length > 0 ? `${flagged.length} issues — fix first` : "Export to QBO →"
            }
          </button>
        </div>
      </div>

      {/* Demo mode banner */}
      {isDemo && (
        <div className="demo-banner">
          <span className="demo-badge">Demo</span>
          <span className="demo-text">
            <strong>Sample Chase statement · Jan 2025.</strong>{" "}
            Edit any transaction, explore the review workflow, then sign up free to parse your own PDFs.
          </span>
          <button className="demo-cta" onClick={() => { setShowKeyModal(true); }}>
            Get free API key →
          </button>
        </div>
      )}

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
          <div className="stat-val">{transactions.filter(t => !t._deleted).length}</div>
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
        <div className="stat-cell">
          <div className="stat-label">Reconciliation</div>
          <div className={`stat-val ${
            reconciliation.status === "ok"  ? "green" :
            reconciliation.status === "off" ? "red"   : ""
          }`}>
            {reconciliation.status === "ok"  ? "✓ Balanced" :
             reconciliation.status === "off" ? `Off ${reconciliation.diff > 0 ? "+" : ""}${fmt(reconciliation.diff)}` :
             "—"}
          </div>
        </div>
      </div>

      {/* Reconciliation mismatch banner */}
      {reconciliation.status === "off" && (
        <div style={{
          padding: "6px 20px",
          background: "var(--red-dim)",
          borderBottom: "1px solid var(--red)",
          display: "flex", alignItems: "center", gap: 16,
          fontSize: 12, color: "var(--red)", flexShrink: 0,
          fontFamily: "var(--mono)", flexWrap: "wrap",
        }}>
          <span>⚠ Statement does not reconcile</span>
          {reconciliation.method === "sum" && (
            <span style={{ color: "var(--white-2)" }}>
              Opening {fmt(reconciliation.opening)} + net flow {reconciliation.diff > 0 ? "+" : ""}
              {fmt(reconciliation.computed - reconciliation.opening)}
              {" = "}<strong>{fmt(reconciliation.computed)}</strong>
              {" · "}Statement closing <strong>{fmt(reconciliation.closing)}</strong>
              {" · "}Δ <strong style={{ color: "var(--red)" }}>{fmt(reconciliation.diff)}</strong>
            </span>
          )}
          {reconciliation.method === "last-balance" && (
            <span style={{ color: "var(--white-2)" }}>
              Last running balance <strong>{fmt(reconciliation.closing + reconciliation.diff)}</strong>
              {" vs "}statement closing <strong>{fmt(reconciliation.closing)}</strong>
              {" · "}Δ <strong style={{ color: "var(--red)" }}>{fmt(reconciliation.diff)}</strong>
            </span>
          )}
        </div>
      )}

      {/* Mobile pane switcher — only visible on ≤ 768px via CSS */}
      <div className="pane-tabs">
        <button
          className={`pane-tab ${activePane === "pdf" ? "active" : ""}`}
          onClick={() => setActivePane("pdf")}
        >
          📄 PDF
        </button>
        <button
          className={`pane-tab ${activePane === "table" ? "active" : ""}`}
          onClick={() => setActivePane("table")}
        >
          📊 Transactions
        </button>
      </div>

      {/* Main split layout */}
      <div className="main-layout">

        {/* ── LEFT: PDF viewer ─────────────────────────────────── */}
        <div className={`pdf-pane${activePane !== "pdf" ? " pane-hidden" : ""}`}>
          <div className="pane-header">
            <span className="pane-label">
              {isMultiFile ? "First PDF (preview)" : "Original PDF"}
            </span>
            {numPages && (
              <div className="pdf-controls">
                <button className="btn btn-icon" onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1}>‹</button>
                <span className="page-counter">{currentPage} / {numPages}</span>
                <button className="btn btn-icon" onClick={() => setCurrentPage(p => Math.min(numPages, p + 1))} disabled={currentPage === numPages}>›</button>
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
                error={<div style={{ color: "var(--red)", padding: 24, fontSize: 12 }}>Failed to load PDF.</div>}
              >
                {Array.from({ length: numPages || 0 }, (_, i) => i + 1).map(page => (
                  <div
                    key={page}
                    ref={el => { pageRefs.current[page] = el; }}
                    className={`pdf-page-wrap ${
                      page === currentPage && selectedId ? "highlighted" :
                      page === currentPage && hoveredId  ? "hover-highlight" : ""
                    }`}
                  >
                    <Page
                      pageNumber={page}
                      scale={pdfScale}
                      renderTextLayer
                      renderAnnotationLayer
                      customTextRenderer={customTextRenderer}
                    />
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
                <div className="drop-text">Drop PDF(s) here</div>
                <div className="drop-sub">or click to browse · multiple files supported</div>
              </div>
            )}
          </div>
        </div>

        {/* ── RIGHT: Transaction table ──────────────────────────── */}
        <div className={`table-pane${activePane !== "table" ? " pane-hidden" : ""}`}>
          <div className="pane-header">
            <span className="pane-label">Parsed Transactions</span>
            <button className="btn btn-icon" title="Add row" onClick={() => setShowAddRow(s => !s)} style={{ fontSize: 16 }}>＋</button>
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
            {loading ? (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 10, color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 12 }}>
                <div style={{ width: 20, height: 20, border: "2px solid var(--border)", borderTopColor: "var(--blue)", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                {loadingMsg}
                <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
              </div>
            ) : quotaExceeded ? (
              <div className="quota-error-card">
                <div style={{ fontSize: 32 }}>🚫</div>
                <h3>Monthly limit reached</h3>
                <p>
                  You've used all your PDFs for this billing period.
                  Upgrade to keep converting without interruption.
                </p>
                <button
                  className="btn btn-primary"
                  onClick={() => { setUpgradeError(null); setShowUpgrade(true); }}
                >
                  ↑ Upgrade my plan
                </button>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  Or wait until your quota resets next period.
                </span>
              </div>
            ) : apiError ? (
              <div style={{ padding: 28, color: "var(--red)", fontFamily: "var(--mono)", fontSize: 12 }}>
                <div style={{ marginBottom: 8, fontWeight: 500 }}>⚠ Parse error</div>
                <div style={{ color: "var(--white-2)", lineHeight: 1.5 }}>{apiError}</div>
              </div>
            ) : !pdfFile ? (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 10, color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 12 }}>
                <span style={{ fontSize: 28, opacity: 0.4 }}>⇪</span>
                Upload a PDF to get started
                <span style={{ fontSize: 11, opacity: 0.6 }}>Drag &amp; drop or click "Upload PDF"</span>
              </div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 24 }} />
                    <th style={{ width: 88 }} onClick={() => sort("date")}>Date{sortIcon("date")}</th>
                    <th onClick={() => sort("description")}>Description{sortIcon("description")}</th>
                    <th onClick={() => sort("category")} style={{ width: 120 }}>Category{sortIcon("category")}</th>
                    <th style={{ width: 64 }}>Type</th>
                    <th className="r" style={{ width: 96 }} onClick={() => sort("amount")}>Amount{sortIcon("amount")}</th>
                    <th className="r col-balance" style={{ width: 88 }}>Balance</th>
                    <th style={{ width: 72 }} />
                  </tr>
                </thead>
                <tbody>
                  {visible.map(tx => {
                    const reasons = flagReasons(tx, balanceDeltaMap);
                    const isNeg   = parseFloat(tx.amount) < 0;
                    const rowCls  = [
                      selectedId === tx.id ? "selected" : "",
                      reasons.length && !tx._confirmed ? "flagged" : "",
                      tx._confirmed ? "confirmed" : "",
                      tx._deleted ? "deleted" : "",
                    ].filter(Boolean).join(" ");

                    return (
                      <tr
                        key={tx.id}
                        className={rowCls}
                        onClick={() => setSelectedId(id => id === tx.id ? null : tx.id)}
                        onMouseEnter={() => {
                          setHoveredId(tx.id);
                          if (tx.source_page) setCurrentPage(tx.source_page);
                        }}
                        onMouseLeave={() => setHoveredId(null)}
                      >
                        {/* status dot */}
                        <td onClick={e => e.stopPropagation()} style={{ paddingLeft: 14 }}>
                          <StatusDot tx={tx} deleted={tx._deleted} balanceDeltaMap={balanceDeltaMap} />
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

                        {/* category */}
                        <td onClick={e => e.stopPropagation()}>
                          <input
                            className="editable category-input"
                            value={tx.category || ""}
                            placeholder="—"
                            onChange={e => updateTx(tx.id, "category", e.target.value)}
                            onClick={e => e.stopPropagation()}
                            title={tx.category || "No category"}
                          />
                        </td>

                        {/* type */}
                        <td onClick={e => e.stopPropagation()}>
                          <select className="editable" value={tx.type || "OTHER"}
                            onChange={e => updateTx(tx.id, "type", e.target.value)}
                            onClick={e => e.stopPropagation()}
                            style={{ fontFamily: "var(--mono)", fontSize: 10, textTransform: "uppercase" }}>
                            {TX_TYPES.map(t => <option key={t}>{t}</option>)}
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
                        <td className="col-balance" style={{ textAlign: "right" }} onClick={e => e.stopPropagation()}>
                          <input className="editable amount-input" style={{ color: "var(--subtle)" }}
                            value={tx.balance || ""}
                            onChange={e => updateTx(tx.id, "balance", e.target.value)}
                            onClick={e => e.stopPropagation()} />
                        </td>

                        {/* actions */}
                        <td onClick={e => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                          {/* split button */}
                          {!tx._deleted && (
                            <button className="btn btn-icon" title="Split transaction"
                              style={{ fontSize: 12, color: "var(--blue)" }}
                              onClick={() => setSplitTxId(tx.id)}>⇗</button>
                          )}
                          {/* confirm button */}
                          {reasons.length > 0 && !tx._confirmed && !tx._deleted && (
                            <button className="btn btn-icon" title="Mark OK"
                              style={{ fontSize: 12, color: "var(--green)" }}
                              onClick={() => confirmTx(tx.id)}>✓</button>
                          )}
                          {/* delete / restore button */}
                          <button
                            className={`btn btn-icon ${tx._deleted ? "" : "btn-danger"}`}
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
                    <tr><td colSpan={8} style={{ textAlign: "center", padding: 40, color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 12 }}>
                      No transactions match the current filter.
                    </td></tr>
                  )}
                </tbody>
              </table>
            )}
          </div>

          {/* Add row form */}
          {showAddRow && <AddRowForm onAdd={(tx) => { addTx(tx); setShowAddRow(false); }} />}

          {/* QBO preview panel — shown when a row is selected */}
          {selectedId && (() => {
            const tx = transactions.find(t => t.id === selectedId);
            if (!tx) return null;
            const fields = buildOFXFields(tx);
            const isNeg  = parseFloat(tx.amount || 0) < 0;
            return (
              <div className="qbo-preview">
                <div className="qbo-preview-header">
                  <span className="qbo-preview-label">QBO Preview</span>
                  <span className="qbo-preview-fitid">{tx.fit_id || "FITID pending"}</span>
                  <span className="qbo-preview-close" onClick={() => setSelectedId(null)} title="Close">×</span>
                </div>
                <div className="qbo-code">
                  <span className="qbo-tag">{"<STMTTRN>"}</span>{"\n"}
                  {fields.map(([tag, val]) => {
                    const valCls =
                      tag === "TRNAMT"   ? (isNeg ? "qbo-val-neg" : "qbo-val") :
                      tag === "TRNTYPE"  ? (isNeg ? "qbo-val-neg" : "qbo-val") :
                      tag === "DTPOSTED" ? "qbo-val-neutral" : "qbo-val";
                    return (
                      <React.Fragment key={tag}>
                        {"  "}
                        <span className="qbo-tag">{`<${tag}>`}</span>
                        <span className={valCls}>{val}</span>
                        <span className="qbo-tag">{`</${tag}>`}</span>
                        {"\n"}
                      </React.Fragment>
                    );
                  })}
                  <span className="qbo-tag">{"</STMTTRN>"}</span>
                </div>
              </div>
            );
          })()}

          {/* Footer summary */}
          <div className="footer">
            <div className="footer-summary">
              <span className="fs-item"><span className="dot dot-ok" /> <span className="fs-count">{cleanCount}</span> confirmed</span>
              <span className="fs-divider">·</span>
              <span className="fs-item"><span className="dot dot-warn" /> <span className="fs-count">{flagged.length}</span> flagged</span>
              <span className="fs-divider">·</span>
              <span className="fs-item"><span className="dot dot-del" /> <span className="fs-count">{transactions.filter(t => t._deleted).length}</span> deleted</span>
              <span className="fs-divider">·</span>
              <span style={{ color: "var(--subtle)" }}>{visible.length} of {transactions.length} shown</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn" onClick={() => setShowAddRow(s => !s)}>+ Add row</button>
              <button className="btn btn-primary"
                disabled={!isDemo && flagged.length > 0}
                onClick={() => isDemo ? setShowKeyModal(true) : setShowExport(true)}>
                {isDemo ? "Sign up to export →" : "Export →"}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Split transaction modal */}
      {splitTxId && splitTx_ && (
        <SplitModal
          tx={splitTx_}
          onSplit={splitTx}
          onClose={() => setSplitTxId(null)}
        />
      )}

      {/* Export modal */}
      {showExport && (
        <ExportModal transactions={transactions} meta={meta} onClose={() => setShowExport(false)} />
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
 * 3. Add the worker to your vite config:
 *      // vite.config.js
 *      import { viteStaticCopy } from 'vite-plugin-static-copy';
 *      plugins: [viteStaticCopy({ targets: [{
 *        src: 'node_modules/pdfjs-dist/build/pdf.worker.min.js',
 *        dest: ''
 *      }]})]
 *
 * 4. Use in your app:
 *      import ReviewUI from './components/ReviewUI';
 *      <ReviewUI />
 *
 * 5. API shape expected from /preview endpoint:
 *      {
 *        bank: "JPMorgan Chase",
 *        account_id: "****1234",
 *        account_type: "CHECKING",
 *        statement_start: "2024-01-01",
 *        statement_end: "2024-01-31",
 *        closing_balance: 9040.51,
 *        warnings: [],
 *        transactions: [
 *          { date: "2024-01-03", description: "...", amount: -45.00,
 *            balance: 9500.00, type: "POS", category: "Meals" },
 *          ...
 *        ]
 *      }
 *
 * 6. Multi-file upload:
 *      Drag multiple PDFs onto the drop zone or use Ctrl+Click in the file
 *      picker.  Each file is sent to /preview individually; results are merged
 *      and client-side deduplicated (same date + amount + description).
 */
