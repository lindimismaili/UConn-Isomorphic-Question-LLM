import Components from './sections/Components.jsx';
import Foundations from './sections/Foundations.jsx';
import Patterns from './sections/Patterns.jsx';

/**
 * Design-system showcase page. Renders the real `ui/` primitives inside the
 * app's own header/main/footer scaffold so every specimen sits in the live
 * theme. Organized Foundations → Components → Patterns (Claude-style).
 */
export default function DesignSystem() {
  return (
    <>
      <header className="header">
        <div className="header-content">
          <div className="logo">
            <span>IsoGen Design System</span>
          </div>
          <nav
            aria-label="Design system sections"
            style={{ display: 'flex', gap: 'var(--spacing-sm)' }}
          >
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
        </div>
      </header>

      <main className="main-content">
        <div
          className="content-shell"
          style={{ display: 'flex', flexDirection: 'column', gap: 'var(--section-gap)' }}
        >
          <section>
            <h1>Design System</h1>
            <p className="subtitle">
              The template building blocks the UI is composed from. Compose features only from
              these primitives (<code>src/components/ui/</code>); any new element must be added here
              first, then used. Styling lives in <code>app.css</code> — these are thin wrappers, not
              new styles.
            </p>
          </section>
          <Foundations />
          <Components />
          <Patterns />
        </div>
      </main>

      <footer className="footer">
        <div className="footer-content">
          <span className="subtitle">
            See frontend/DESIGN.md for the full design system contract.
          </span>
        </div>
      </footer>
    </>
  );
}
