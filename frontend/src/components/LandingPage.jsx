import React, { useState } from "react";
import { useNavigate } from "react-router-dom";

/* ─── palette ─────────────────────────────────────────────────────────────── */
const C = {
  bg:        "#FFFFFF",
  bgAlt:     "#F7F8FA",
  bgCard:    "#FFFFFF",
  border:    "#E5E7EB",
  primary:   "#1652F0",
  primaryD:  "#1140C4",
  primaryBg: "#EEF2FF",
  accent:    "#0AAF60",
  accentD:   "#059669",
  accentBg:  "rgba(10,175,96,0.08)",
  text:      "#111827",
  textMid:   "#374151",
  muted:     "#6B7280",
  success:   "#059669",
};

/* ─── shared style helpers ─────────────────────────────────────────────────── */
const s = {
  container: {
    maxWidth: 1120,
    margin: "0 auto",
    padding: "0 24px",
  },
  btn: {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    padding: "12px 24px",
    borderRadius: 8,
    fontWeight: 600,
    fontSize: 15,
    cursor: "pointer",
    border: "none",
    transition: "all 0.15s",
    textDecoration: "none",
    fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
  },
};

/* ─── sub-components ───────────────────────────────────────────────────────── */

function Nav({ onGetKey }) {
  return (
    <nav style={{
      position: "sticky", top: 0, zIndex: 100,
      background: "#FFFFFF",
      borderBottom: `1px solid ${C.border}`,
    }}>
      <div style={{ ...s.container, display: "flex", alignItems: "center", height: 64 }}>
        {/* Logo + trust badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 20, fontWeight: 700, color: C.primary, letterSpacing: -0.3, fontFamily: "'Inter', system-ui, -apple-system, sans-serif" }}>
            Statably
          </span>
          <span style={{
            background: C.primaryBg,
            color: C.primary,
            fontSize: 11,
            fontWeight: 600,
            padding: "3px 10px",
            borderRadius: 20,
            border: `1px solid rgba(22,82,240,0.2)`,
            letterSpacing: 0.2,
            fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
          }}>
            QuickBooks® Compatible
          </span>
        </div>

        <div style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center" }}>
          <a href="#how-it-works" style={{ color: C.textMid, textDecoration: "none", fontSize: 14, fontWeight: 500, padding: "8px 12px", borderRadius: 6, fontFamily: "'Inter', system-ui, -apple-system, sans-serif" }}>
            How it works
          </a>
          <a href="#pricing" style={{ color: C.textMid, textDecoration: "none", fontSize: 14, fontWeight: 500, padding: "8px 12px", borderRadius: 6, fontFamily: "'Inter', system-ui, -apple-system, sans-serif" }}>
            Pricing
          </a>
          <a href="#banks" style={{ color: C.textMid, textDecoration: "none", fontSize: 14, fontWeight: 500, padding: "8px 12px", borderRadius: 6, fontFamily: "'Inter', system-ui, -apple-system, sans-serif" }}>
            Banks
          </a>
          <button
            onClick={onGetKey}
            style={{ ...s.btn, background: C.primary, color: "#fff", padding: "9px 20px", fontSize: 14, marginLeft: 8 }}
          >
            Get free key →
          </button>
        </div>
      </div>
    </nav>
  );
}

function HeroEmailForm({ onSuccess }) {
  const [email, setEmail]     = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");

  async function submit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res  = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Registration failed");
      onSuccess(data.api_key, email);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 480, fontFamily: "'Inter', system-ui, -apple-system, sans-serif" }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          type="email"
          required
          placeholder="you@yourfirm.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          style={{
            flex: 1, minWidth: 200,
            padding: "13px 16px",
            borderRadius: 8,
            border: `1.5px solid ${C.border}`,
            background: "#FFFFFF",
            color: C.text,
            fontSize: 15,
            outline: "none",
            fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
          }}
        />
        <button
          type="submit"
          disabled={loading}
          style={{
            ...s.btn,
            background: loading ? C.primaryD : C.primary,
            color: "#fff",
            whiteSpace: "nowrap",
          }}
        >
          {loading ? "Sending…" : "Get free key →"}
        </button>
      </div>
      {error && <p style={{ color: "#DC2626", fontSize: 13, margin: 0 }}>{error}</p>}
      {/* Trust signals */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 4 }}>
        {["Bank-grade encryption", "No credit card required", "10 free conversions/month"].map(t => (
          <span key={t} style={{ color: C.muted, fontSize: 13, display: "flex", alignItems: "center", gap: 5 }}>
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ flexShrink: 0 }}>
              <circle cx="6.5" cy="6.5" r="6.5" fill={C.primaryBg} />
              <path d="M3.5 6.5L5.5 8.5L9.5 4.5" stroke={C.primary} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {t}
          </span>
        ))}
      </div>
    </form>
  );
}

function KeySuccess({ apiKey, email, onGoToApp }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(apiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div style={{
      background: "#F0FDF4",
      border: `1.5px solid #BBF7D0`,
      borderRadius: 12,
      padding: "20px 24px",
      maxWidth: 520,
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
    }}>
      <p style={{ color: C.success, fontWeight: 700, margin: "0 0 4px" }}>Your free key is ready</p>
      <p style={{ color: C.muted, fontSize: 13, margin: "0 0 12px" }}>
        Also sent to <strong style={{ color: C.text }}>{email}</strong>. Save it — shown only once.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <code style={{
          flex: 1, background: "#FFFFFF", color: C.text,
          padding: "10px 14px", borderRadius: 7, fontSize: 13,
          border: `1px solid ${C.border}`, wordBreak: "break-all",
          fontFamily: "ui-monospace, 'SF Mono', monospace",
        }}>
          {apiKey}
        </code>
        <button onClick={copy} style={{
          ...s.btn, padding: "10px 16px", fontSize: 13,
          background: copied ? C.success : "#FFFFFF",
          color: copied ? "#fff" : C.textMid,
          border: `1px solid ${C.border}`,
        }}>
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <button
        onClick={onGoToApp}
        style={{ ...s.btn, marginTop: 16, background: C.primary, color: "#fff", width: "100%", justifyContent: "center" }}
      >
        Open the converter →
      </button>
    </div>
  );
}

function AppPreview() {
  const rows = [
    { date: "Sep 02", desc: "Client Payment — Acme Consulting LLC", amount: "+$8,500.00", cls: "dep" },
    { date: "Sep 05", desc: "Google Workspace — Monthly Subscription", amount: "−$72.00",   cls: "wd"  },
    { date: "Sep 08", desc: "Client Payment — Meridian Group Inc.",    amount: "+$4,250.00", cls: "dep" },
    { date: "Sep 12", desc: "QuickBooks Online — Annual Plan",         amount: "−$540.00",  cls: "wd"  },
    { date: "Sep 15", desc: "Wire Transfer — Payroll Sep 1–15",        amount: "−$6,200.00", cls: "wd" },
  ];
  return (
    <div style={{
      background: "#FFFFFF",
      border: `1px solid ${C.border}`,
      borderRadius: 14,
      overflow: "hidden",
      boxShadow: "0 4px 24px rgba(0,0,0,0.08), 0 1px 4px rgba(0,0,0,0.04)",
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
    }}>
      {/* light topbar */}
      <div style={{
        background: "#F9FAFB",
        padding: "12px 16px",
        display: "flex",
        alignItems: "center",
        gap: 10,
        borderBottom: `1px solid ${C.border}`,
      }}>
        <div style={{ display: "flex", gap: 6 }}>
          {["#FECACA","#FEF08A","#BBF7D0"].map(c => (
            <div key={c} style={{ width: 11, height: 11, borderRadius: "50%", background: c, border: "1px solid rgba(0,0,0,0.06)" }} />
          ))}
        </div>
        <span style={{ color: C.muted, fontSize: 12 }}>Chase Business Checking • Sep 2026 • 47 transactions</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <span style={{ background: C.primaryBg, color: C.primary, fontSize: 11, padding: "3px 8px", borderRadius: 5, fontWeight: 600 }}>QFX</span>
          <span style={{ background: "#F3F4F6", color: C.muted, fontSize: 11, padding: "3px 8px", borderRadius: 5 }}>CSV</span>
        </div>
      </div>
      {/* table */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ background: "#F9FAFB" }}>
            {["Date","Description","Amount","Balance","Category"].map(h => (
              <th key={h} style={{ padding: "8px 14px", textAlign: "left", color: C.muted, fontWeight: 600, letterSpacing: 0.3 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: `1px solid ${C.border}`, background: i % 2 === 0 ? "#FFFFFF" : "#FAFAFA" }}>
              <td style={{ padding: "9px 14px", color: C.muted, whiteSpace: "nowrap" }}>{r.date}</td>
              <td style={{ padding: "9px 14px", color: C.text, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.desc}</td>
              <td style={{ padding: "9px 14px", color: r.cls === "dep" ? C.accent : "#DC2626", fontWeight: 600, whiteSpace: "nowrap" }}>{r.amount}</td>
              <td style={{ padding: "9px 14px", color: C.muted }}>—</td>
              <td style={{ padding: "9px 14px" }}>
                <span style={{ background: r.cls === "dep" ? "#F0FDF4" : "#FEF2F2", color: r.cls === "dep" ? C.success : "#DC2626", fontSize: 10, padding: "2px 7px", borderRadius: 4, fontWeight: 600 }}>
                  {r.cls === "dep" ? "Income" : "Expense"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", background: "#FAFAFA" }}>
        <span style={{ color: C.muted, fontSize: 11 }}>Balance reconciled · 0 discrepancies</span>
        <button style={{ ...s.btn, padding: "7px 16px", fontSize: 12, background: C.primary, color: "#fff" }}>
          Download OFX
        </button>
      </div>
    </div>
  );
}

/* SVG icons for DiffCard — simple, clean, no emoji */
const IconEdit = () => (
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
    <rect width="28" height="28" rx="8" fill={C.primaryBg} />
    <path d="M8 18.5V20h1.5l7-7-1.5-1.5-7 7zm10.7-9.3a1 1 0 0 0 0-1.4l-1-1a1 1 0 0 0-1.4 0l-1.3 1.3 2.4 2.4 1.3-1.3z" fill={C.primary} />
  </svg>
);

const IconApi = () => (
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
    <rect width="28" height="28" rx="8" fill={C.primaryBg} />
    <path d="M9 14h10M14 9l5 5-5 5" stroke={C.primary} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const IconTransparency = () => (
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
    <rect width="28" height="28" rx="8" fill={C.primaryBg} />
    <circle cx="14" cy="14" r="5" stroke={C.primary} strokeWidth="1.8" />
    <path d="M14 9V7M14 21v-2M9 14H7M21 14h-2" stroke={C.primary} strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

function DiffCard({ icon, title, sub, tag }) {
  return (
    <div style={{
      background: C.bgCard,
      border: `1px solid ${C.border}`,
      borderRadius: 14,
      padding: "28px 28px",
      display: "flex",
      flexDirection: "column",
      gap: 14,
      boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
    }}>
      {tag && (
        <span style={{
          background: C.primaryBg,
          color: C.primary,
          fontSize: 11,
          padding: "3px 10px",
          borderRadius: 20,
          fontWeight: 600,
          alignSelf: "flex-start",
          letterSpacing: 0.2,
        }}>
          {tag}
        </span>
      )}
      <div>{icon}</div>
      <h3 style={{ color: C.text, margin: 0, fontSize: 18, fontWeight: 700, letterSpacing: -0.2 }}>{title}</h3>
      <p style={{ color: C.textMid, margin: 0, fontSize: 14, lineHeight: 1.7 }}>{sub}</p>
    </div>
  );
}

function PricingCard({ plan, price, per, features, cta, highlight, onCta }) {
  return (
    <div style={{
      background: highlight ? C.primaryBg : "#FFFFFF",
      border: `1.5px solid ${highlight ? C.primary : C.border}`,
      borderRadius: 16,
      padding: "32px 28px",
      display: "flex",
      flexDirection: "column",
      gap: 20,
      position: "relative",
      boxShadow: highlight ? "0 4px 24px rgba(22,82,240,0.12)" : "0 1px 4px rgba(0,0,0,0.04)",
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
    }}>
      {highlight && (
        <span style={{
          position: "absolute", top: -13, left: "50%", transform: "translateX(-50%)",
          background: C.primary, color: "#fff", fontSize: 11, padding: "4px 14px",
          borderRadius: 20, fontWeight: 700, letterSpacing: 0.5,
          whiteSpace: "nowrap",
        }}>MOST POPULAR</span>
      )}
      <div>
        <p style={{ color: C.muted, margin: "0 0 4px", fontSize: 13, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase" }}>{plan}</p>
        <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
          <span style={{ fontSize: 40, fontWeight: 700, color: C.text }}>{price}</span>
          {per && <span style={{ color: C.muted, fontSize: 14 }}>{per}</span>}
        </div>
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
        {features.map(f => (
          <li key={f} style={{ display: "flex", gap: 10, alignItems: "flex-start", color: C.textMid, fontSize: 14 }}>
            <span style={{ color: C.accent, marginTop: 1, fontWeight: 700, flexShrink: 0 }}>✓</span> {f}
          </li>
        ))}
      </ul>
      <button
        onClick={onCta}
        style={{
          ...s.btn,
          background: highlight ? C.primary : "transparent",
          color: highlight ? "#fff" : C.primary,
          border: highlight ? "none" : `1.5px solid ${C.primary}`,
          justifyContent: "center",
          marginTop: "auto",
        }}
      >
        {cta}
      </button>
    </div>
  );
}

const BANKS = [
  "JPMorgan Chase","Bank of America","Wells Fargo","Citibank",
  "PNC Bank","U.S. Bank","TD Bank","Capital One",
  "Fifth Third Bank","American Express","Fidelity","USAA",
  "Ally Bank","Charles Schwab","Navy Federal CU","Truist",
  "KEMBA Financial CU","+ any bank via AI fallback",
];

/* ─── main page ────────────────────────────────────────────────────────────── */

export default function LandingPage() {
  const navigate = useNavigate();
  const [apiKey, setApiKey]     = useState(null);
  const [email,  setEmail]      = useState("");
  const [showForm, setShowForm] = useState(false);

  function handleSuccess(key, em) {
    // persist key so the app picks it up immediately
    localStorage.setItem("statably_api_key", key);
    setApiKey(key);
    setEmail(em);
    setShowForm(false);
  }

  function goToApp() {
    navigate("/app");
  }

  function scrollToKey() {
    setShowForm(true);
    document.getElementById("hero-cta")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const baseFont = { fontFamily: "'Inter', system-ui, -apple-system, sans-serif" };

  return (
    <div style={{ background: C.bg, color: C.text, ...baseFont, minHeight: "100vh" }}>
      <Nav onGetKey={scrollToKey} />

      {/* ── HERO ─────────────────────────────────────────────────────────── */}
      <section style={{ padding: "80px 24px 72px", background: "#FFFFFF" }}>
        <div style={{ ...s.container, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 60, alignItems: "center" }}>
          <div>
            {/* badge */}
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              background: C.primaryBg,
              border: `1px solid rgba(22,82,240,0.2)`,
              borderRadius: 20, padding: "5px 14px", marginBottom: 24,
            }}>
              <span style={{ width: 6, height: 6, background: C.primary, borderRadius: "50%", display: "inline-block" }} />
              <span style={{ color: C.primary, fontSize: 12, fontWeight: 600, letterSpacing: 0.3 }}>QuickBooks® Compatible</span>
            </div>

            <h1 style={{ fontSize: "clamp(30px, 3.8vw, 48px)", fontWeight: 700, lineHeight: 1.15, margin: "0 0 12px", letterSpacing: -0.3, color: C.text }}>
              Convert bank statement PDFs to{" "}
              <span style={{ color: C.primary }}>QuickBooks®-ready files</span>{" "}
              in seconds
            </h1>
            <p style={{ fontSize: 11, color: C.muted, margin: "0 0 16px", lineHeight: 1.5 }}>
              QuickBooks® is a registered trademark of Intuit Inc. Statably is not affiliated with or endorsed by Intuit Inc.
            </p>
            <p style={{ fontSize: 17, color: C.textMid, margin: "0 0 36px", lineHeight: 1.7, maxWidth: 480 }}>
              Upload your PDF, edit every transaction inline, reconcile against real balances, then export.
              The only converter that shows you exactly what it parsed — and lets you fix it before importing into QuickBooks®.
            </p>

            <div id="hero-cta">
              {apiKey ? (
                <KeySuccess apiKey={apiKey} email={email} onGoToApp={goToApp} />
              ) : showForm ? (
                <HeroEmailForm onSuccess={handleSuccess} />
              ) : (
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                  <button onClick={() => setShowForm(true)} style={{ ...s.btn, background: C.primary, color: "#fff", fontSize: 16, padding: "14px 28px" }}>
                    Get your free key →
                  </button>
                  <button onClick={() => navigate("/app?demo=true")} style={{ ...s.btn, background: "transparent", color: C.textMid, border: `1.5px solid ${C.border}`, fontSize: 16, padding: "14px 28px" }}>
                    Try demo ↗
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* app preview */}
          <div style={{ minWidth: 0 }}>
            <AppPreview />
          </div>
        </div>
      </section>

      {/* ── HOW IT WORKS ─────────────────────────────────────────────────── */}
      <section id="how-it-works" style={{ padding: "80px 24px", background: C.bgAlt, borderTop: `1px solid ${C.border}` }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.primary, fontWeight: 600, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>HOW IT WORKS</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 700, margin: "0 0 8px", letterSpacing: -0.3, color: C.text }}>Three steps, zero surprises</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 56px", lineHeight: 1.6 }}>From PDF to QuickBooks® in under a minute.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 32 }}>
            {[
              { n: "01", title: "Upload your PDF", body: "Drop any bank statement — Chase, Fifth Third, Amex, 20 banks total. Batch upload multiple months at once." },
              { n: "02", title: "Review & edit inline", body: "Every transaction is editable. Fix misreads, split transactions, assign categories, reconcile balances." },
              { n: "03", title: "Download & Import", body: "Download OFX, QFX, or CSV. Import into QuickBooks® Online or your accounting software in two clicks." },
            ].map(({ n, title, body }) => (
              <div key={n} style={{
                background: "#FFFFFF",
                border: `1px solid ${C.border}`,
                borderRadius: 14,
                padding: "28px 28px",
                display: "flex",
                flexDirection: "column",
                gap: 14,
                boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
              }}>
                <div style={{
                  width: 52, height: 52, borderRadius: "50%",
                  background: C.primaryBg,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  flexShrink: 0,
                }}>
                  <span style={{ fontSize: 20, fontWeight: 700, color: C.primary }}>{n}</span>
                </div>
                <h3 style={{ color: C.text, margin: 0, fontSize: 18, fontWeight: 700, letterSpacing: -0.2 }}>{title}</h3>
                <p style={{ color: C.textMid, margin: 0, fontSize: 14, lineHeight: 1.7 }}>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── DIFFERENTIATORS ──────────────────────────────────────────────── */}
      <section style={{ padding: "80px 24px", borderTop: `1px solid ${C.border}`, background: "#FFFFFF" }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.primary, fontWeight: 600, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>WHY US</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 700, margin: "0 0 8px", letterSpacing: -0.3, color: C.text }}>What sets us apart</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 48px" }}>Features our users actually asked for.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 24 }}>
            <DiffCard
              icon={<IconEdit />}
              tag="Exclusive"
              title="Edit before you export"
              sub="Inline editing with live balance reconciliation. Fix parser misreads, split one transaction into many, override categories — all before you import. Other converters give you a finished file with no way to review it first."
            />
            <DiffCard
              icon={<IconApi />}
              tag="Exclusive"
              title="API access for bookkeeping firms"
              sub="One API key, unlimited integrations. Connect to Zapier, Make, or your firm's internal tools. Automate statement processing across all your clients without touching the UI."
            />
            <DiffCard
              icon={<IconTransparency />}
              tag="Exclusive"
              title="Full transparency on every parse"
              sub="See exactly what was extracted, line by line. Balance discrepancies flagged in real time. Session auto-saved so you never lose work. No silent failures, no mystery results."
            />
          </div>
        </div>
      </section>

      {/* ── SUPPORTED BANKS ──────────────────────────────────────────────── */}
      <section id="banks" style={{ padding: "80px 24px", borderTop: `1px solid ${C.border}`, background: C.bgAlt }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.primary, fontWeight: 600, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>SUPPORTED BANKS</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 700, margin: "0 0 8px", letterSpacing: -0.3, color: C.text }}>20 native parsers</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 40px" }}>
            Don't see yours? The AI fallback parser handles any layout.{" "}
            <a href="mailto:support@statably.org?subject=Bank%20parser%20request" style={{ color: C.primary, textDecoration: "none", fontWeight: 500 }}>Request a bank →</a>
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center" }}>
            {BANKS.map(b => (
              <span key={b} style={{
                background: "#FFFFFF",
                border: `1px solid ${C.border}`,
                borderRadius: 8, padding: "8px 16px",
                color: b.startsWith("+") ? C.primary : C.textMid,
                fontSize: 13, fontWeight: 500,
                boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
              }}>{b}</span>
            ))}
          </div>
        </div>
      </section>

      {/* ── PRICING ──────────────────────────────────────────────────────── */}
      <section id="pricing" style={{ padding: "80px 24px", borderTop: `1px solid ${C.border}`, background: "#FFFFFF" }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.primary, fontWeight: 600, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>PRICING</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 700, margin: "0 0 8px", letterSpacing: -0.3, color: C.text }}>Simple, honest pricing</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 48px" }}>
            Start free — upgrade when you need more volume.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 24, maxWidth: 900, margin: "0 auto" }}>
            <PricingCard
              plan="Free"
              price="$0"
              features={[
                "10 conversions / month",
                "All 20 bank parsers",
                "OFX · QFX · CSV export",
                "Inline editing",
                "Batch upload",
              ]}
              cta="Get started free"
              onCta={scrollToKey}
            />
            <PricingCard
              plan="Starter"
              price="$9"
              per="/month"
              highlight
              features={[
                "100 conversions / month",
                "Everything in Free",
                "API access",
                "Priority email support",
                "Session history (roadmap)",
              ]}
              cta="Start Starter"
              onCta={scrollToKey}
            />
            <PricingCard
              plan="Pro"
              price="$29"
              per="/month"
              features={[
                "Unlimited conversions",
                "Everything in Starter",
                "Accountant workspace (roadmap)",
                "Multi-entity batch processing (roadmap)",
                "White-label exports (roadmap)",
              ]}
              cta="Go Pro"
              onCta={scrollToKey}
            />
          </div>
        </div>
      </section>

      {/* ── FINAL CTA ────────────────────────────────────────────────────── */}
      <section style={{ padding: "80px 24px", background: C.primaryBg, borderTop: `1px solid rgba(22,82,240,0.12)` }}>
        <div style={{ ...s.container, textAlign: "center" }}>
          <h2 style={{ fontSize: "clamp(26px, 3.5vw, 40px)", fontWeight: 700, margin: "0 0 16px", letterSpacing: -0.3, color: C.text }}>
            Ready to stop downloading files<br />and start just importing?
          </h2>
          <p style={{ color: C.textMid, fontSize: 16, margin: "0 0 36px", lineHeight: 1.6 }}>
            Takes 60 seconds. No credit card. Cancel anytime.
          </p>
          {apiKey ? (
            <div style={{ display: "inline-block" }}>
              <KeySuccess apiKey={apiKey} email={email} onGoToApp={goToApp} />
            </div>
          ) : (
            <div style={{ display: "flex", justifyContent: "center" }}>
              <HeroEmailForm onSuccess={handleSuccess} />
            </div>
          )}
        </div>
      </section>

      {/* ── FOOTER ───────────────────────────────────────────────────────── */}
      <footer style={{ background: "#F9FAFB", borderTop: `1px solid ${C.border}`, padding: "32px 24px" }}>
        <div style={{ ...s.container, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16 }}>
          <span style={{ color: C.text, fontSize: 15, fontWeight: 700 }}>
            <span style={{ color: C.primary }}>Stat</span>ably
            <span style={{ color: C.muted, fontWeight: 400, marginLeft: 8 }}>· Built for accountants</span>
          </span>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            {[
              ["Terms of Service", "/terms"],
              ["Privacy Policy", "/privacy"],
              ["API Docs", "/docs"],
              ["support@statably.org", "mailto:support@statably.org"],
            ].map(([label, href]) => (
              <a key={label} href={href} style={{ color: C.muted, fontSize: 13, textDecoration: "none" }}>{label}</a>
            ))}
          </div>
        </div>
        <div style={{ ...s.container, marginTop: 20, borderTop: `1px solid ${C.border}`, paddingTop: 16 }}>
          <p style={{ color: C.muted, fontSize: 11, margin: 0, lineHeight: 1.7 }}>
            QuickBooks® is a registered trademark of Intuit Inc. Statably is not affiliated with or endorsed by Intuit Inc.
            Statably exports files in open OFX/QFX/CSV formats that are compatible with QuickBooks® and other accounting software.
          </p>
          <p style={{ color: C.muted, fontSize: 11, margin: "8px 0 0", lineHeight: 1.7 }}>
            Your API key is stored in your browser's <code style={{ fontFamily: "ui-monospace, 'SF Mono', monospace" }}>localStorage</code> for convenience. It never leaves your device to third parties.
            You can clear it at any time in your browser settings. See our <a href="/privacy" style={{ color: C.muted }}>Privacy Policy</a> for details.
          </p>
        </div>
      </footer>
    </div>
  );
}
