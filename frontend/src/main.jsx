import React from 'react'
import ReactDOM from 'react-dom/client'
import * as Sentry from '@sentry/react'
import App from './App'

// ── Sentry — only initialised when VITE_SENTRY_DSN is set ────────────────────
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.MODE,          // "development" | "production"
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText:   true,   // mask all text in replays — privacy first
        blockAllMedia: true,
      }),
    ],
    // Trace 10 % of navigations in production; 100 % in dev for easier debugging
    tracesSampleRate:   import.meta.env.PROD ? 0.1 : 1.0,
    replaysSessionSampleRate:     0.05,  // record 5 % of sessions
    replaysOnErrorSampleRate:     1.0,   // always record on error
    // Strip local-variable values from every exception frame to prevent
    // parsed financial data (amounts, account numbers) from leaking to Sentry.
    // send_default_pii=false does NOT suppress exception locals.
    beforeSend(event) {
      const excValues = event?.exception?.values ?? []
      for (const exc of excValues) {
        const frames = exc?.stacktrace?.frames ?? []
        for (const frame of frames) {
          delete frame.vars  // local variable bindings
        }
      }
      // Also clear any attached user PII
      if (event.user) event.user = {}
      return event
    },
  })
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />)
