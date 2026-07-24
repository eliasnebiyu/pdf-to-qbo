import React from "react";
import { useNavigate } from "react-router-dom";

const C = { bg: "#0b1120", card: "#111827", border: "#1e293b", accent: "#10b981", muted: "#94a3b8", white: "#f1f5f9", text: "#cbd5e1" };

function Section({ title, children }) {
  return (
    <section style={{ marginBottom: 40 }}>
      <h2 style={{ fontSize: 18, fontWeight: 700, color: C.white, margin: "0 0 12px", paddingBottom: 8, borderBottom: `1px solid ${C.border}` }}>
        {title}
      </h2>
      <div style={{ color: C.text, fontSize: 15, lineHeight: 1.8 }}>{children}</div>
    </section>
  );
}

function P({ children }) { return <p style={{ margin: "0 0 12px" }}>{children}</p>; }

function Table({ rows }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 16, fontSize: 14 }}>
      <thead>
        <tr>
          {["Data", "Purpose", "Retention"].map(h => (
            <th key={h} style={{ textAlign: "left", padding: "8px 12px", background: "#1c2128", color: C.muted, fontWeight: 600, borderBottom: `1px solid ${C.border}` }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
            {row.map((cell, j) => (
              <td key={j} style={{ padding: "8px 12px", color: C.text }}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function PrivacyPage() {
  const navigate = useNavigate();
  const EFFECTIVE = "July 21, 2025";

  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "system-ui, -apple-system, sans-serif" }}>
      {/* Nav */}
      <nav style={{ borderBottom: `1px solid ${C.border}`, padding: "0 24px" }}>
        <div style={{ maxWidth: 800, margin: "0 auto", display: "flex", alignItems: "center", height: 60, gap: 16 }}>
          <button onClick={() => navigate("/")} style={{ background: "none", border: "none", color: C.accent, fontWeight: 800, fontSize: 18, cursor: "pointer", letterSpacing: -0.5 }}>
            <span style={{ color: C.accent }}>Ledger</span><span style={{ color: C.white }}>Flow</span>
          </button>
          <span style={{ color: C.muted, fontSize: 14 }}>/ Privacy Policy</span>
        </div>
      </nav>

      {/* Body */}
      <div style={{ maxWidth: 800, margin: "0 auto", padding: "48px 24px 80px" }}>
        <h1 style={{ fontSize: 32, fontWeight: 900, color: C.white, margin: "0 0 8px", letterSpacing: -0.5 }}>Privacy Policy</h1>
        <p style={{ color: C.muted, fontSize: 14, margin: "0 0 48px" }}>Effective date: {EFFECTIVE}</p>

        <Section title="Overview">
          <P>LedgerFlow is built for accountants and bookkeepers who handle sensitive financial documents. We take privacy seriously. This policy explains exactly what we collect, why, and how long we keep it.</P>
          <P><strong style={{ color: C.white }}>Short version:</strong> We do not store the contents of your bank statement PDFs. We never sell your data. We never train AI models on your documents.</P>
          <P><strong style={{ color: C.white }}>Controller:</strong> LedgerFlow Inc., a Delaware corporation. Registered office: 651 N Broad St, Suite 201, Middletown, DE 19709, United States. For all privacy requests, contact <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a> with the subject "Privacy Request". You may also write to us at the registered address above.</P>
        </Section>

        <Section title="1. Information We Collect">
          <Table rows={[
            ["Email address", "Issue your API key; send account notifications", "Until account deletion"],
            ["API key (hashed)", "Authenticate requests and track usage quota", "Until account deletion"],
            ["Usage counters", "Enforce plan limits; display your quota", "Rolling 30-day window"],
            ["Uploaded PDF files", "Parse transactions in-memory", "Deleted immediately after processing — never persisted"],
            ["IP address", "Rate limiting; fraud prevention", "7 days in server logs"],
            ["Error events (Sentry)", "Diagnose bugs; improve reliability", "90 days"],
            ["Browser/OS metadata", "Compatibility debugging", "90 days, via Sentry"],
          ]} />
          <P>We do <strong style={{ color: C.white }}>not</strong> collect: names, addresses, bank account numbers, transaction amounts stored server-side, or any data from inside your PDFs beyond what is returned to you in the same request.</P>
        </Section>

        <Section title="2. How We Use Your Information">
          <P>We use the information we collect to:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}>Operate and maintain the Service</li>
            <li style={{ marginBottom: 6 }}>Send your API key and transactional account emails (via Resend)</li>
            <li style={{ marginBottom: 6 }}>Enforce usage quotas and rate limits</li>
            <li style={{ marginBottom: 6 }}>Monitor for errors and improve parsing accuracy</li>
            <li style={{ marginBottom: 6 }}>Prevent fraud and abuse</li>
          </ul>
          <P>We do <strong style={{ color: C.white }}>not</strong> use your data for advertising, sell it to third parties, or use it to train machine learning models.</P>
        </Section>

        <Section title="2a. GDPR — Lawful Basis for Processing (EU/UK Users)">
          <P>If you are located in the European Economic Area (EEA) or United Kingdom, we rely on the following lawful bases under Article 6 of the GDPR for each processing activity:</P>
          <Table rows={[
            ["Email address & API key", "Performance of a contract (Art. 6(1)(b)) — necessary to provide the Service you signed up for"],
            ["Usage counters", "Legitimate interests (Art. 6(1)(f)) — enforcing fair usage and preventing abuse"],
            ["IP address / rate-limiting data", "Legitimate interests (Art. 6(1)(f)) — security, fraud prevention, and service integrity"],
            ["Error monitoring (Sentry)", "Legitimate interests (Art. 6(1)(f)) — diagnosing bugs and maintaining service reliability"],
            ["PDF content (in-memory only)", "Performance of a contract (Art. 6(1)(b)) — processing your uploaded file is the core service"],
            ["Billing data (via Stripe)", "Performance of a contract (Art. 6(1)(b)) — processing your payment for a paid subscription"],
          ]} />
          <P>Where we rely on legitimate interests, you have the right to object to that processing. To exercise this right, contact us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a>.</P>
        </Section>

        <Section title="3. PDF Processing and AI Fallback">
          <P>When you upload a PDF, it is transmitted over HTTPS to our servers, parsed in memory, and the result (a JSON list of transactions) is returned to your browser. The PDF is deleted from our servers immediately after processing — it is never written to persistent storage.</P>
          <P>For bank layouts that our native parsers do not recognise, we may relay the extracted text (not the raw PDF) to Anthropic's Claude API to assist with parsing. In those cases:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}>Only the text content of the PDF is sent — not the file itself</li>
            <li style={{ marginBottom: 6 }}>Anthropic does not use this content to train models (<a href="https://www.anthropic.com/privacy" target="_blank" rel="noopener noreferrer" style={{ color: C.accent }}>Anthropic Privacy Policy</a>)</li>
            <li style={{ marginBottom: 6 }}>This only happens when no native parser succeeds</li>
          </ul>
        </Section>

        <Section title="4. Third-Party Services">
          <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 16, fontSize: 14 }}>
            <thead>
              <tr>
                {["Service", "Purpose", "Privacy Policy"].map(h => (
                  <th key={h} style={{ textAlign: "left", padding: "8px 12px", background: "#1c2128", color: C.muted, fontWeight: 600, borderBottom: `1px solid ${C.border}` }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[
                ["Stripe", "Payment processing", "https://stripe.com/privacy"],
                ["Resend", "Transactional email delivery", "https://resend.com/privacy"],
                ["Anthropic", "AI fallback parsing (text only)", "https://www.anthropic.com/privacy"],
                ["Sentry", "Error monitoring", "https://sentry.io/privacy/"],
                ["Railway", "Cloud infrastructure (servers in US)", "https://railway.app/legal/privacy"],
              ].map(([svc, purpose, url]) => (
                <tr key={svc} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: "8px 12px", color: C.white, fontWeight: 500 }}>{svc}</td>
                  <td style={{ padding: "8px 12px", color: C.text }}>{purpose}</td>
                  <td style={{ padding: "8px 12px" }}><a href={url} target="_blank" rel="noopener noreferrer" style={{ color: C.accent, fontSize: 13 }}>Policy →</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <Section title="5. Data Security">
          <P>We implement reasonable security measures including:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}>HTTPS/TLS encryption for all data in transit</li>
            <li style={{ marginBottom: 6 }}>API keys stored as SHA-256 hashes — the raw key is never persisted and is shown to you exactly once</li>
            <li style={{ marginBottom: 6 }}>No persistent storage of PDF contents or parsed transaction data</li>
            <li style={{ marginBottom: 6 }}>Rate limiting and abuse detection on all endpoints</li>
          </ul>
          <P>No method of transmission over the internet is 100% secure. If you discover a security vulnerability, please disclose it responsibly to <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a>.</P>
        </Section>

        <Section title="6. Your Rights">
          <P>Depending on your jurisdiction, you may have the right to:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Access</strong> the personal data we hold about you</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Correct</strong> inaccurate data</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Delete</strong> your account and associated data</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Port</strong> your data in a machine-readable format</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Object</strong> to certain processing activities</li>
          </ul>
          <P>To exercise any of these rights, email us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a>. We will respond within 30 days.</P>
        </Section>

        <Section title="7. Data Retention">
          <P>We retain personal data only as long as necessary:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}>Email and API key: until you request deletion or account is inactive for 12 months</li>
            <li style={{ marginBottom: 6 }}>Usage counters: reset on a rolling 30-day basis</li>
            <li style={{ marginBottom: 6 }}>Server logs (IP, timestamp): 7 days</li>
            <li style={{ marginBottom: 6 }}>Error monitoring data (Sentry): 90 days</li>
            <li style={{ marginBottom: 6 }}>PDF content: zero — never persisted</li>
          </ul>
        </Section>

        <Section title="8. Cookies and Tracking">
          <P>LedgerFlow does not use advertising cookies or third-party tracking pixels. The Service uses:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>localStorage</strong> (browser) to store your API key and session draft — this data never leaves your device. By using the Service you acknowledge that these values are stored locally; you can clear them at any time via your browser's developer tools or settings.
            </li>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>Error monitoring cookies</strong> set by Sentry for session replay (5% of sessions). Replay is fully masked — no text or transaction content is visible. You can opt out by contacting us.
            </li>
          </ul>
          <P>We do not use cookies for advertising, re-targeting, or cross-site tracking.</P>
        </Section>

        <Section title="9. Children's Privacy">
          <P>The Service is not directed to individuals under 16 years of age. We do not knowingly collect personal information from children. If you believe a child has provided us with personal information, contact us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a>.</P>
        </Section>

        <Section title="10. California Privacy Rights (CCPA / CPRA)">
          <P>If you are a California resident, the California Consumer Privacy Act (CCPA) and California Privacy Rights Act (CPRA) grant you additional rights:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Right to Know:</strong> You may request a copy of the personal information we have collected about you in the past 12 months.</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Right to Delete:</strong> You may request deletion of your personal information, subject to certain exceptions.</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Right to Correct:</strong> You may request correction of inaccurate personal information we hold about you.</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Right to Opt Out of Sale or Sharing:</strong> We do <em>not</em> sell, share, or disclose your personal information to third parties for cross-context behavioural advertising. There is nothing to opt out of, but you may confirm this in writing by emailing us.</li>
            <li style={{ marginBottom: 6 }}><strong style={{ color: C.white }}>Right to Non-Discrimination:</strong> We will not discriminate against you for exercising any of your CCPA rights.</li>
          </ul>
          <P>To exercise any of these rights, email us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a> with the subject line "California Privacy Request". We will respond within 45 days.</P>
          <P><strong style={{ color: C.white }}>Do Not Sell or Share My Personal Information.</strong> We do not sell or share your personal information as defined by the CCPA/CPRA. If you wish to confirm this in writing or wish to opt out of any future sale or sharing, use the link below or email us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a>.</P>
          <div style={{ margin: "12px 0" }}>
            <a
              href="mailto:support@ledgerflow.io?subject=CCPA%20Opt-Out%20Request"
              style={{
                display: "inline-block",
                padding: "8px 16px",
                background: "transparent",
                border: `1.5px solid ${C.accent}`,
                borderRadius: 6,
                color: C.accent,
                fontSize: 13,
                fontWeight: 600,
                textDecoration: "none",
              }}
            >
              Do Not Sell or Share My Information →
            </a>
          </div>
          <P><strong style={{ color: C.white }}>Categories of personal information collected:</strong> identifiers (email address, IP address), internet or other electronic network activity (usage counters, error events). We do not collect sensitive personal information as defined by the CPRA.</P>
        </Section>

        <Section title="11. International Data Transfers (GDPR)">
          <P>LedgerFlow Inc. is based in the United States. If you are located in the European Economic Area (EEA) or United Kingdom, your personal data may be transferred to and processed in the United States, which may not provide the same level of data protection as your home jurisdiction.</P>
          <P>We take the following steps to ensure adequate protection when transferring data internationally:</P>
          <ul style={{ margin: "0 0 12px", paddingLeft: 24, color: C.text }}>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>Railway (infrastructure):</strong> Servers are in the United States. Railway participates in data transfer frameworks and offers DPA coverage under Standard Contractual Clauses (SCCs) approved by the European Commission.
            </li>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>Anthropic (AI fallback):</strong> Processes text extracted from PDFs in the US. Anthropic offers an API Data Processing Addendum with SCCs. Text relayed to Anthropic is not stored by Anthropic for model training.
            </li>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>Stripe (payments):</strong> Certified under the EU–US Data Privacy Framework and offers SCCs for international data transfers. Stripe is also ISO 27001-certified.
            </li>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>Sentry (error monitoring):</strong> Sentry offers EU data residency options and provides SCCs for transfers to the United States.
            </li>
            <li style={{ marginBottom: 6 }}>
              <strong style={{ color: C.white }}>Resend (transactional email):</strong> Processes email address and delivery metadata in the US. Resend provides SCCs under its Data Processing Agreement.
            </li>
          </ul>
          <P>To request a copy of our Data Processing Addendum (DPA) or the applicable Standard Contractual Clauses, email <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a> with the subject "DPA Request".</P>
          <P>If you are an EEA or UK resident and believe we have not adequately addressed a privacy concern, you have the right to lodge a complaint with your local data protection authority.</P>
        </Section>

        <Section title="12. Changes to This Policy">
          <P>We may update this Privacy Policy from time to time. We will notify you of material changes by updating the effective date and, where appropriate, by emailing registered users. Continued use of the Service after changes constitutes acceptance of the updated policy.</P>
        </Section>

        <Section title="13. Contact">
          <P>Questions or concerns about this Privacy Policy?<br />
          Email us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a></P>
        </Section>
      </div>

      {/* Footer */}
      <footer style={{ borderTop: `1px solid ${C.border}`, padding: "24px", textAlign: "center" }}>
        <div style={{ display: "flex", gap: 24, justifyContent: "center", flexWrap: "wrap" }}>
          {[["← Back to home", "/"], ["Terms of Service", "/terms"]].map(([label, href]) => (
            <button key={label} onClick={() => navigate(href)}
              style={{ background: "none", border: "none", color: C.muted, fontSize: 13, cursor: "pointer" }}>
              {label}
            </button>
          ))}
        </div>
      </footer>
    </div>
  );
}
