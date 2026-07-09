import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import * as Sentry from "@sentry/react";
import LandingPage from "./components/LandingPage";
import ReviewUI    from "./components/ReviewUI";
import TermsPage   from "./components/TermsPage";
import PrivacyPage from "./components/PrivacyPage";

const SentryRoutes = Sentry.withSentryReactRouterV7Routing(Routes);

export default function App() {
  return (
    <Sentry.ErrorBoundary fallback={<ErrorFallback />} showDialog={false}>
      <BrowserRouter>
        <SentryRoutes>
          <Route path="/"       element={<LandingPage />} />
          <Route path="/app"    element={<ReviewUI />} />
          <Route path="/terms"  element={<TermsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          {/* catch-all: unknown paths → landing */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </SentryRoutes>
      </BrowserRouter>
    </Sentry.ErrorBoundary>
  );
}

function ErrorFallback() {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", height: "100vh",
      background: "#0b1120", color: "#f1f5f9", fontFamily: "system-ui, sans-serif",
      gap: 16, padding: 24, textAlign: "center",
    }}>
      <div style={{ fontSize: 36 }}>⚠️</div>
      <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Something went wrong</h2>
      <p style={{ margin: 0, color: "#94a3b8", maxWidth: 380 }}>
        The error has been reported automatically. Try refreshing the page.
      </p>
      <button
        onClick={() => window.location.reload()}
        style={{
          marginTop: 8, padding: "10px 24px", borderRadius: 8,
          background: "#10b981", color: "#0b1120", border: "none",
          fontWeight: 700, fontSize: 14, cursor: "pointer",
        }}
      >
        Refresh page
      </button>
    </div>
  );
}
