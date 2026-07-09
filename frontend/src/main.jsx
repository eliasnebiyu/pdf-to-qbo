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
    // Don't send PII
    beforeSend(event) {
      if (event.user) delete event.user.email
      return event
    },
  })
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />)
