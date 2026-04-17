import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

/**
 * Mounts the React app into the `#root` container.
 *
 * This file is intentionally minimal so Vite can bootstrap the UI quickly.
 */
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
