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

function P({ children }) {
  return <p style={{ margin: "0 0 12px" }}>{children}</p>;
}

function Ul({ items }) {
  return (
    <ul style={{ margin: "0 0 12px", paddingLeft: 24 }}>
      {items.map((item, i) => <li key={i} style={{ marginBottom: 6 }}>{item}</li>)}
    </ul>
  );
}

export default function TermsPage() {
  const navigate = useNavigate();
  const EFFECTIVE = "June 22, 2025";

  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "system-ui, -apple-system, sans-serif" }}>
      {/* Nav */}
      <nav style={{ borderBottom: `1px solid ${C.border}`, padding: "0 24px" }}>
        <div style={{ maxWidth: 800, margin: "0 auto", display: "flex", alignItems: "center", height: 60, gap: 16 }}>
          <button onClick={() => navigate("/")} style={{ background: "none", border: "none", color: C.accent, fontWeight: 800, fontSize: 18, cursor: "pointer", letterSpacing: -0.5 }}>
            <span style={{ color: C.accent }}>Ledger</span><span style={{ color: C.white }}>Flow</span>
          </button>
          <span style={{ color: C.muted, fontSize: 14 }}>/ Terms of Service</span>
        </div>
      </nav>

      {/* Body */}
      <div style={{ maxWidth: 800, margin: "0 auto", padding: "48px 24px 80px" }}>
        <h1 style={{ fontSize: 32, fontWeight: 900, color: C.white, margin: "0 0 8px", letterSpacing: -0.5 }}>Terms of Service</h1>
        <p style={{ color: C.muted, fontSize: 14, margin: "0 0 48px" }}>Effective date: {EFFECTIVE}</p>

        <Section title="1. Acceptance of Terms">
          <P>By accessing or using LedgerFlow ("Service", "we", "us"), you agree to be bound by these Terms of Service. If you do not agree, do not use the Service.</P>
          <P>These Terms apply to all visitors, users, and others who access or use the Service. The Service is operated by LedgerFlow ("Company").</P>
        </Section>

        <Section title="2. Description of Service">
          <P>LedgerFlow is a software-as-a-service tool that converts bank statement PDF files into QuickBooks-compatible transaction files (OFX, QFX, CSV). The Service provides:</P>
          <Ul items={[
            "Automated parsing of bank statement PDFs using rule-based and AI-assisted extraction",
            "An in-browser review interface for editing and reconciling transactions",
            "Export of reviewed transactions in standard accounting import formats",
            "A REST API for programmatic access",
          ]} />
          <P>LedgerFlow is a productivity tool. It is not a financial institution, accounting firm, or licensed financial advisor. We do not hold, transmit, or have access to your financial accounts.</P>
        </Section>

        <Section title="3. API Keys and Accounts">
          <P>Access to the Service requires a valid API key issued upon registration. You are responsible for:</P>
          <Ul items={[
            "Keeping your API key confidential",
            "All activity that occurs under your API key",
            "Notifying us immediately at support@ledgerflow.io if you believe your key has been compromised",
          ]} />
          <P>We reserve the right to revoke any API key that we believe is being used in violation of these Terms.</P>
        </Section>

        <Section title="4. Subscription Plans and Billing">
          <P>The Service is offered under the following tiers:</P>
          <Ul items={[
            "Free — 10 conversions per 30-day period, no charge",
            "Starter — 100 conversions per 30-day period, billed monthly",
            "Pro — unlimited conversions, billed monthly",
          ]} />
          <P>Paid plans are billed in advance on a monthly basis through Stripe. All fees are non-refundable except where required by applicable law. We reserve the right to change pricing with 30 days' notice to your registered email address.</P>
          <P>Cancelling your subscription will not trigger a refund for the current billing period. Your paid access continues until the end of the period you have paid for.</P>
        </Section>

        <Section title="5. Acceptable Use">
          <P>You agree not to use the Service to:</P>
          <Ul items={[
            "Upload PDFs you do not own or do not have authorisation to process",
            "Process PDFs on behalf of others without their consent",
            "Attempt to reverse-engineer, scrape, or extract our parsing algorithms",
            "Circumvent rate limits or usage quotas by using multiple API keys",
            "Use the Service in any way that violates applicable law or regulation",
            "Resell or sublicense access to the Service without written permission",
          ]} />
        </Section>

        <Section title="6. Data and Privacy">
          <P>Your PDF files are transmitted to our servers over HTTPS for processing. We retain uploaded files only for the duration of the active request and delete them immediately after processing. We do not store the contents of your bank statements.</P>
          <P>Certain parsing requests may be relayed to Anthropic's Claude API as a fallback. In those cases, your data is subject to Anthropic's data handling policies. Anthropic does not use customer data to train models.</P>
          <P>Please review our <a href="/privacy" style={{ color: C.accent }}>Privacy Policy</a> for full details on data collection and handling.</P>
        </Section>

        <Section title="7. Intellectual Property">
          <P>The Service, including its software, design, and documentation, is owned by LedgerFlow and protected by intellectual property laws. These Terms do not grant you any right, title, or interest in the Service beyond the limited right to use it as described herein.</P>
          <P>You retain full ownership of any PDF files you upload and any transaction data you export.</P>
        </Section>

        <Section title="8. Disclaimers">
          <P>THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED. WE DO NOT WARRANT THAT:</P>
          <Ul items={[
            "The Service will be uninterrupted, timely, secure, or error-free",
            "Parsed transaction data will be complete, accurate, or suitable for any accounting purpose",
            "Any errors in parsed output will be corrected",
          ]} />
          <P>You are solely responsible for reviewing parsed transactions before importing them into your accounting software. Always reconcile against your original bank statement.</P>
        </Section>

        <Section title="9. Limitation of Liability">
          <P>TO THE MAXIMUM EXTENT PERMITTED BY LAW, LEDGERFLOW SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, INCLUDING LOSS OF PROFITS, DATA, OR GOODWILL, ARISING OUT OF OR RELATED TO YOUR USE OF THE SERVICE.</P>
          <P>OUR TOTAL LIABILITY TO YOU FOR ANY CLAIMS UNDER THESE TERMS SHALL NOT EXCEED THE AMOUNT YOU PAID US IN THE 12 MONTHS PRECEDING THE CLAIM, OR $50, WHICHEVER IS GREATER.</P>
        </Section>

        <Section title="10. Termination">
          <P>We may suspend or terminate your access to the Service at any time, with or without notice, for conduct that we believe violates these Terms or is harmful to other users, us, or third parties.</P>
          <P>You may stop using the Service at any time. Free-tier accounts that have been inactive for 12 months may be automatically deleted.</P>
        </Section>

        <Section title="11. Changes to Terms">
          <P>We reserve the right to modify these Terms at any time. We will provide notice of material changes by updating the effective date above and, where appropriate, by emailing registered users. Continued use of the Service after changes constitutes acceptance of the updated Terms.</P>
        </Section>

        <Section title="12. Governing Law">
          <P>These Terms are governed by the laws of the State of Delaware, United States, without regard to conflict of law principles. Any disputes shall be resolved in the courts of competent jurisdiction in Delaware.</P>
        </Section>

        <Section title="13. Contact">
          <P>Questions about these Terms? Contact us at <a href="mailto:support@ledgerflow.io" style={{ color: C.accent }}>support@ledgerflow.io</a>.</P>
        </Section>
      </div>

      {/* Footer */}
      <footer style={{ borderTop: `1px solid ${C.border}`, padding: "24px", textAlign: "center" }}>
        <div style={{ display: "flex", gap: 24, justifyContent: "center", flexWrap: "wrap" }}>
          {[["← Back to home", "/"], ["Privacy Policy", "/privacy"]].map(([label, href]) => (
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
