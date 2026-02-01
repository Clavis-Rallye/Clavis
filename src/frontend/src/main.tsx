import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import CssBaseline from '@mui/material/CssBaseline'
import { polyfillCountryFlagEmojis } from 'country-flag-emoji-polyfill'
import '@fontsource/noto-sans/400.css'
import '@fontsource/noto-sans/500.css'
import '@fontsource/noto-sans/600.css'
import '@fontsource/noto-sans/700.css'

// Self-hosted to keep CSP `font-src 'self'` strict (no external CDN).
polyfillCountryFlagEmojis('Twemoji Country Flags', '/TwemojiCountryFlags.woff2')
import '@fontsource/roboto/400.css'
import '@fontsource/roboto/500.css'
import '@fontsource/roboto/600.css'
import '@fontsource/roboto-mono/400.css'
import './styles/design-tokens.css'
import './index.css'
import App from './App.tsx'
import AppThemeProvider from './AppThemeProvider'
import { loadInstanceConfig } from './config/instanceConfig'
import { registerSW } from 'virtual:pwa-register'

registerSW({
  onRegistered(registration) {
    if (registration) {
      setInterval(() => {
        registration.update()
      }, 60 * 60 * 1000)
    }
  },
  onRegisterError(error) {
    console.error('[PWA] Service worker registration failed', error)
  },
  immediate: true
})

// Resolve the instance config (brand, bootstrap gate, single-org slug)
// before the first render, so headlines, the document title and the
// register and onboarding gates never flash a wrong value. The fetch is
// bounded (about 3 s) and never rejects: offline, the PWA renders with the
// defaults.
void loadInstanceConfig().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <AppThemeProvider>
        <CssBaseline />
        <App />
      </AppThemeProvider>
    </StrictMode>,
  )
})
