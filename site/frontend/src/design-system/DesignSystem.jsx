import { AppShell, PageHeader, Footer } from '../components/ui/index.js';
import { HEADER_MOTIF } from '../sections/SiteChrome.jsx';
import Components from './sections/Components.jsx';
import Foundations from './sections/Foundations.jsx';
import Patterns from './sections/Patterns.jsx';

/**
 * Design-system showcase page. Renders the real `ui/` primitives inside the
 * app's own shell (the SAME AppShell / PageHeader / Footer primitives the site
 * ships) so every specimen sits in the live theme and the chrome is no longer
 * duplicated as raw markup. Organized Foundations → Components → Patterns.
 */
export default function DesignSystem() {
  return (
    <AppShell
      header={
        <PageHeader
          motif={HEADER_MOTIF}
          brand={
            <div className="logo">
              <span className="logo-icon logo-icon-ai" aria-hidden="true" />
              <h1>IsoGen Design System</h1>
            </div>
          }
          aside={
            <nav aria-label="Design system sections" style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
              <a className="btn btn-ghost" href="#foundations">
                Foundations
              </a>
              <a className="btn btn-ghost" href="#components">
                Components
              </a>
              <a className="btn btn-ghost" href="#patterns">
                Patterns
              </a>
            </nav>
          }
        />
      }
      footer={
        <Footer>
          <span className="subtitle">See frontend/DESIGN.md for the full design system contract.</span>
        </Footer>
      }
    >
      <section>
        <h1>Design System</h1>
        <p className="subtitle">
          The template building blocks the UI is composed from. Compose features only from these
          primitives (<code>src/components/ui/</code>); any new element must be added here first,
          then used. Styling lives in <code>app.css</code> — these are thin wrappers, not new styles.
        </p>
      </section>
      <Foundations />
      <Components />
      <Patterns />
    </AppShell>
  );
}
