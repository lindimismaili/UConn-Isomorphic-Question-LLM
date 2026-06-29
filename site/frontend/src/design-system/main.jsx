import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles/app.css';
import DesignSystem from './DesignSystem.jsx';

/**
 * Entry for the design-system showcase page (design-system.html). It imports
 * the real app.css so every specimen renders with the live theme — the showcase
 * is a living catalog of the template building blocks, not a copy of them.
 */
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DesignSystem />
  </StrictMode>,
);
