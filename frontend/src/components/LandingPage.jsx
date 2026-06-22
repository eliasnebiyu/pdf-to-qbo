import React, { useState } from "react";
import { useNavigate } from "react-router-dom";

/* ─── palette ─────────────────────────────────────────────────────────────── */
const C = {
  bg:       "#0b1120",
  bgCard:   "#111827",
  bgLight:  "#f8fafc",
  border:   "#1e293b",
  borderL:  "#e2e8f0",
  accent:   "#10b981",
  accentD:  "#059669",
  accentBg: "rgba(16,185,129,0.08)",
  blue:     "#3b82f6",
  muted:    "#94a3b8",
  white:    "#f1f5f9",
  text:     "#1e293b",
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
  },
};

/* ─── sub-components ───────────────────────────────────────────────────────── */

function Nav({ onGetKey }) {
  return (
    <nav style={{
      position: "sticky", top: 0, zIndex: 100,
      background: "rgba(11,17,32,0.95)",
      backdropFilter: "blur(12px)",
      borderBottom: `1px solid ${C.border}`,
    }}>
      <div style={{ ...s.container, display: "flex", alignItems: "center", height: 64 }}>
        <span style={{ fontSize: 20, fontWeight: 800, color: C.white, letterSpacing: -0.5 }}>
          <span style={{ color: C.accent }}>PDF</span>toQBO
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
          <a href="#pricing" style={{ color: C.muted, textDecoration: "none", fontSize: 14, fontWeight: 500 }}>
            Pricing
          </a>
          <a href="#banks" style={{ color: C.muted, textDecoration: "none", fontSize: 14, fontWeight: 500 }}>
            Banks
          </a>
          <button
            onClick={onGetKey}
            style={{ ...s.btn, background: C.accent, color: "#fff", padding: "9px 20px", fontSize: 14 }}
          >
            Get free key →
          </button>
        </div>
      </div>
    </nav>
  );
}

function HeroEmailForm({ onSuccess }) {
  const [email, setEmail]   = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState("");

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
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 480 }}>
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
            background: C.bgCard,
            color: C.white,
            fontSize: 15,
            outline: "none",
          }}
        />
        <button
          type="submit"
          disabled={loading}
          style={{
            ...s.btn,
            background: loading ? C.accentD : C.accent,
            color: "#fff",
            whiteSpace: "nowrap",
          }}
        >
          {loading ? "Sending…" : "Get free key →"}
        </button>
      </div>
      {error && <p style={{ color: "#f87171", fontSize: 13, margin: 0 }}>{error}</p>}
      <p style={{ color: C.muted, fontSize: 13, margin: 0 }}>
        No credit card. 10 free conversions / month. Cancel anytime.
      </p>
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
      background: C.accentBg,
      border: `1.5px solid ${C.accent}`,
      borderRadius: 12,
      padding: "20px 24px",
      maxWidth: 520,
    }}>
      <p style={{ color: C.accent, fontWeight: 700, margin: "0 0 4px" }}>✓ Your free key is ready</p>
      <p style={{ color: C.muted, fontSize: 13, margin: "0 0 12px" }}>
        Also sent to <strong style={{ color: C.white }}>{email}</strong>. Save it — shown only once.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <code style={{
          flex: 1, background: C.bgCard, color: C.white,
          padding: "10px 14px", borderRadius: 7, fontSize: 13,
          border: `1px solid ${C.border}`, wordBreak: "break-all",
        }}>
          {apiKey}
        </code>
        <button onClick={copy} style={{
          ...s.btn, padding: "10px 16px", fontSize: 13,
          background: copied ? C.accentD : C.bgCard,
          color: copied ? "#fff" : C.muted,
          border: `1px solid ${C.border}`,
        }}>
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <button
        onClick={onGoToApp}
        style={{ ...s.btn, marginTop: 16, background: C.accent, color: "#fff", width: "100%", justifyContent: "center" }}
      >
        Open the converter →
      </button>
    </div>
  );
}

function AppPreview() {
  const rows = [
    { date: "05/01", desc: "Square Inc · Green By Nature", amount: "+$193.28", cls: "dep" },
    { date: "05/01", desc: "DEBIT CARD PURCHASE · SITEONE LANDSCAPE", amount: "−$154.90", cls: "wd" },
    { date: "05/05", desc: "DIRECT DEPOSIT PAYROLL", amount: "+$3,500.00", cls: "dep" },
    { date: "05/07", desc: "Check #7627", amount: "−$200.00", cls: "wd" },
    { date: "05/12", desc: "5/3 ATM WITHDRAWAL", amount: "−$300.00", cls: "wd" },
  ];
  return (
    <div style={{
      background: C.bgCard, border: `1px solid ${C.border}`,
      borderRadius: 14, overflow: "hidden",
      boxShadow: "0 25px 60px rgba(0,0,0,0.5)",
    }}>
      {/* fake topbar */}
      <div style={{
        background: "#0f1929", padding: "12px 16px",
        display: "flex", alignItems: "center", gap: 10,
        borderBottom: `1px solid ${C.border}`,
      }}>
        <div style={{ display: "flex", gap: 6 }}>
          {["#ff5f57","#ffbd2e","#28c840"].map(c => (
            <div key={c} style={{ width: 11, height: 11, borderRadius: "50%", background: c }} />
          ))}
        </div>
        <span style={{ color: C.muted, fontSize: 12 }}>Fifth Third Bank • May 2026 • 66 transactions</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <span style={{ background: "#1a2744", color: C.blue, fontSize: 11, padding: "3px 8px", borderRadius: 5, fontWeight: 600 }}>QFX</span>
          <span style={{ background: "#1a2744", color: C.muted, fontSize: 11, padding: "3px 8px", borderRadius: 5 }}>CSV</span>
        </div>
      </div>
      {/* fake table */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ background: "#0d1525" }}>
            {["Date","Description","Amount","Balance","Category"].map(h => (
              <th key={h} style={{ padding: "8px 14px", textAlign: "left", color: C.muted, fontWeight: 600, letterSpacing: 0.3 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: `1px solid ${C.border}`, background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.015)" }}>
              <td style={{ padding: "9px 14px", color: C.muted, whiteSpace: "nowrap" }}>{r.date}</td>
              <td style={{ padding: "9px 14px", color: C.white, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.desc}</td>
              <td style={{ padding: "9px 14px", color: r.cls === "dep" ? C.accent : "#f87171", fontWeight: 600, whiteSpace: "nowrap" }}>{r.amount}</td>
              <td style={{ padding: "9px 14px", color: C.muted }}>—</td>
              <td style={{ padding: "9px 14px" }}>
                <span style={{ background: C.accentBg, color: C.accent, fontSize: 10, padding: "2px 7px", borderRadius: 4, fontWeight: 600 }}>
                  {r.cls === "dep" ? "Income" : "Expense"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: C.muted, fontSize: 11 }}>✓ Balance reconciled · 0 discrepancies</span>
        <button style={{ ...s.btn, padding: "7px 16px", fontSize: 12, background: C.accent, color: "#fff" }}>
          Export to QuickBooks
        </button>
      </div>
    </div>
  );
}

function DiffCard({ icon, title, sub, tag }) {
  return (
    <div style={{
      background: C.bgCard, border: `1px solid ${C.border}`,
      borderRadius: 14, padding: "28px 28px",
      display: "flex", flexDirection: "column", gap: 12,
    }}>
      {tag && (
        <span style={{ background: C.accentBg, color: C.accent, fontSize: 11, padding: "3px 8px", borderRadius: 4, fontWeight: 700, alignSelf: "flex-start", letterSpacing: 0.5 }}>
          {tag}
        </span>
      )}
      <div style={{ fontSize: 32 }}>{icon}</div>
      <h3 style={{ color: C.white, margin: 0, fontSize: 18, fontWeight: 700 }}>{title}</h3>
      <p style={{ color: C.muted, margin: 0, fontSize: 14, lineHeight: 1.7 }}>{sub}</p>
    </div>
  );
}

function PricingCard({ plan, price, per, features, cta, highlight, onCta }) {
  return (
    <div style={{
      background: highlight ? "linear-gradient(135deg, #0d2a1e 0%, #0a1f1a 100%)" : C.bgCard,
      border: `1.5px solid ${highlight ? C.accent : C.border}`,
      borderRadius: 16, padding: "32px 28px",
      display: "flex", flexDirection: "column", gap: 20,
      position: "relative",
      boxShadow: highlight ? `0 0 40px rgba(16,185,129,0.15)` : "none",
    }}>
      {highlight && (
        <span style={{
          position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)",
          background: C.accent, color: "#fff", fontSize: 11, padding: "3px 14px",
          borderRadius: 20, fontWeight: 700, letterSpacing: 0.5,
        }}>MOST POPULAR</span>
      )}
      <div>
        <p style={{ color: C.muted, margin: "0 0 4px", fontSize: 13, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase" }}>{plan}</p>
        <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
          <span style={{ fontSize: 40, fontWeight: 800, color: C.white }}>{price}</span>
          {per && <span style={{ color: C.muted, fontSize: 14 }}>{per}</span>}
        </div>
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
        {features.map(f => (
          <li key={f} style={{ display: "flex", gap: 10, alignItems: "flex-start", color: C.muted, fontSize: 14 }}>
            <span style={{ color: C.accent, marginTop: 1 }}>✓</span> {f}
          </li>
        ))}
      </ul>
      <button
        onClick={onCta}
        style={{
          ...s.btn,
          background: highlight ? C.accent : "transparent",
          color: highlight ? "#fff" : C.accent,
          border: highlight ? "none" : `1.5px solid ${C.accent}`,
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
  const [apiKey, setApiKey]   = useState(null);
  const [email,  setEmail]    = useState("");
  const [showForm, setShowForm] = useState(false);

  function handleSuccess(key, em) {
    // persist key so the app picks it up immediately
    localStorage.setItem("pdfqbo_api_key", key);
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

  return (
    <div style={{ background: C.bg, color: C.white, fontFamily: "system-ui, -apple-system, sans-serif", minHeight: "100vh" }}>
      <Nav onGetKey={scrollToKey} />

      {/* ── HERO ─────────────────────────────────────────────────────────── */}
      <section style={{ padding: "80px 24px 60px", background: `radial-gradient(ellipse 80% 50% at 50% -10%, rgba(16,185,129,0.12) 0%, transparent 70%), ${C.bg}` }}>
        <div style={{ ...s.container, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 60, alignItems: "center" }}>
          <div>
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              background: C.accentBg, border: `1px solid rgba(16,185,129,0.3)`,
              borderRadius: 20, padding: "5px 14px", marginBottom: 24,
            }}>
              <span style={{ width: 6, height: 6, background: C.accent, borderRadius: "50%", display: "inline-block" }} />
              <span style={{ color: C.accent, fontSize: 12, fontWeight: 600, letterSpacing: 0.5 }}>20 banks · No manual entry</span>
            </div>

            <h1 style={{ fontSize: "clamp(32px, 4vw, 52px)", fontWeight: 900, lineHeight: 1.1, margin: "0 0 20px", letterSpacing: -1 }}>
              Turn any bank statement PDF into{" "}
              <span style={{ color: C.accent }}>QuickBooks data</span>{" "}
              in seconds
            </h1>
            <p style={{ fontSize: 17, color: C.muted, margin: "0 0 36px", lineHeight: 1.7, maxWidth: 480 }}>
              Upload your PDF, edit every transaction inline, reconcile against real balances, then export.
              The only converter that shows you exactly what it parsed — and lets you fix it before it hits QuickBooks.
            </p>

            <div id="hero-cta">
              {apiKey ? (
                <KeySuccess apiKey={apiKey} email={email} onGoToApp={goToApp} />
              ) : showForm ? (
                <HeroEmailForm onSuccess={handleSuccess} />
              ) : (
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  <button onClick={() => setShowForm(true)} style={{ ...s.btn, background: C.accent, color: "#fff", fontSize: 16, padding: "14px 28px" }}>
                    Get your free key →
                  </button>
                  <button onClick={goToApp} style={{ ...s.btn, background: "transparent", color: C.muted, border: `1.5px solid ${C.border}`, fontSize: 16, padding: "14px 28px" }}>
                    Open the app
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
      <section style={{ padding: "72px 24px", borderTop: `1px solid ${C.border}` }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.accent, fontWeight: 700, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>HOW IT WORKS</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 800, margin: "0 0 48px", letterSpacing: -0.5 }}>Three steps, zero surprises</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 32 }}>
            {[
              { n: "01", title: "Upload your PDF", body: "Drop any bank statement — Chase, Fifth Third, Amex, 20 banks total. Batch upload multiple months at once." },
              { n: "02", title: "Review & edit inline", body: "Every transaction is editable. Fix misreads, split transactions, assign categories, reconcile balances." },
              { n: "03", title: "Export to QuickBooks", body: "Download OFX, QFX, or CSV. Import into QuickBooks Online in two clicks. QuickBooks direct push coming soon." },
            ].map(({ n, title, body }) => (
              <div key={n} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <span style={{ fontSize: 40, fontWeight: 900, color: C.accentBg, WebkitTextStroke: `2px ${C.accent}`, fontVariantNumeric: "tabular-nums" }}>{n}</span>
                <h3 style={{ color: C.white, margin: 0, fontSize: 18, fontWeight: 700 }}>{title}</h3>
                <p style={{ color: C.muted, margin: 0, fontSize: 14, lineHeight: 1.7 }}>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── DIFFERENTIATORS ──────────────────────────────────────────────── */}
      <section style={{ padding: "72px 24px", borderTop: `1px solid ${C.border}`, background: "rgba(255,255,255,0.015)" }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.accent, fontWeight: 700, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>WHY US</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 800, margin: "0 0 8px", letterSpacing: -0.5 }}>What DocuClipper can't do</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 48px" }}>We built the features accountants actually asked for.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 24 }}>
            <DiffCard
              icon="✏️"
              tag="ONLY US"
              title="Edit before you export"
              sub="Inline editing with live balance reconciliation. Fix parser misreads, split one transaction into many, override categories — before anything touches QuickBooks. Every competitor dumps a file and hopes for the best."
            />
            <DiffCard
              icon="⚡"
              tag="ONLY US"
              title="API access for bookkeeping firms"
              sub="One API key, unlimited integrations. Connect to Zapier, Make, or your firm's internal tools. Automate statement processing across all your clients without touching the UI."
            />
            <DiffCard
              icon="🔍"
              tag="ONLY US"
              title="Full transparency on every parse"
              sub="See exactly what was extracted, line by line. Balance discrepancies flagged in real time. Session auto-saved so you never lose work. No silent failures, no mystery results."
            />
          </div>
        </div>
      </section>

      {/* ── SUPPORTED BANKS ──────────────────────────────────────────────── */}
      <section id="banks" style={{ padding: "72px 24px", borderTop: `1px solid ${C.border}` }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.accent, fontWeight: 700, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>SUPPORTED BANKS</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 800, margin: "0 0 8px", letterSpacing: -0.5 }}>20 native parsers</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 40px" }}>
            Don't see yours? The AI fallback parser handles any layout.{" "}
            <a href="mailto:support@pdftoqbo.com?subject=Bank%20parser%20request" style={{ color: C.accent }}>Request a bank →</a>
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center" }}>
            {BANKS.map(b => (
              <span key={b} style={{
                background: C.bgCard, border: `1px solid ${C.border}`,
                borderRadius: 8, padding: "8px 16px",
                color: b.startsWith("+") ? C.accent : C.muted,
                fontSize: 13, fontWeight: 500,
              }}>{b}</span>
            ))}
          </div>
        </div>
      </section>

      {/* ── PRICING ──────────────────────────────────────────────────────── */}
      <section id="pricing" style={{ padding: "72px 24px", borderTop: `1px solid ${C.border}`, background: "rgba(255,255,255,0.015)" }}>
        <div style={s.container}>
          <p style={{ textAlign: "center", color: C.accent, fontWeight: 700, fontSize: 12, letterSpacing: 1.5, textTransform: "uppercase", margin: "0 0 12px" }}>PRICING</p>
          <h2 style={{ textAlign: "center", fontSize: 32, fontWeight: 800, margin: "0 0 8px", letterSpacing: -0.5 }}>Simple, honest pricing</h2>
          <p style={{ textAlign: "center", color: C.muted, fontSize: 16, margin: "0 0 48px" }}>
            DocuClipper charges $29–$99/month. We start at $0.
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
                "Session history",
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
                "Accountant workspace (soon)",
                "QuickBooks direct push (soon)",
                "White-label exports (soon)",
              ]}
              cta="Go Pro"
              onCta={scrollToKey}
            />
          </div>
        </div>
      </section>

      {/* ── FINAL CTA ────────────────────────────────────────────────────── */}
      <section style={{ padding: "80px 24px", borderTop: `1px solid ${C.border}` }}>
        <div style={{ ...s.container, textAlign: "center" }}>
          <h2 style={{ fontSize: "clamp(26px, 3.5vw, 40px)", fontWeight: 900, margin: "0 0 16px", letterSpacing: -0.5 }}>
            Ready to stop downloading files<br />and start just importing?
          </h2>
          <p style={{ color: C.muted, fontSize: 16, margin: "0 0 36px" }}>
            Takes 60 seconds. No credit card. Cancel anytime.
          </p>
          {apiKey ? (
            <div style={{ display: "inline-block" }}>
              <KeySuccess apiKey={apiKey} email={email} onGoToApp={goToApp} />
            </div>
          ) : (
            <HeroEmailForm onSuccess={handleSuccess} />
          )}
        </div>
      </section>

      {/* ── FOOTER ───────────────────────────────────────────────────────── */}
      <footer style={{ borderTop: `1px solid ${C.border}`, padding: "32px 24px" }}>
        <div style={{ ...s.container, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16 }}>
          <span style={{ color: C.muted, fontSize: 14 }}>
            <span style={{ color: C.accent, fontWeight: 800 }}>PDF</span>toQBO · Built for accountants
          </span>
          <div style={{ display: "flex", gap: 24 }}>
            {[
              ["Terms of Service", "/terms"],
              ["Privacy Policy", "/privacy"],
              ["support@pdftoqbo.com", "mailto:support@pdftoqbo.com"],
            ].map(([label, href]) => (
              <a key={label} href={href} style={{ color: C.muted, fontSize: 13, textDecoration: "none" }}>{label}</a>
            ))}
          </div>
        </div>
      </footer>
    </div>
  );
}
