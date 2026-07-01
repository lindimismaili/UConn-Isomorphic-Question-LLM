/**
 * App shell primitives (DESIGN.md §3 scaffold). Thin wrappers over the
 * documented `.outage-banner` / `.header` / `.main-content` / `.content-shell`
 * / `.footer` classes so the App shell and the design-system showcase compose
 * from primitives instead of duplicating raw markup. No new layout CSS — the
 * only added class is the pointer-inert `.header-motif` hero overlay (§3).
 */

/**
 * Banner — the full-width notice strip above the header (replaces the raw
 * `.outage-banner`). Rendered only when shown; `tone` reserved for future
 * variants (today only the outage/error look is styled).
 *
 * @param {object} props
 * @param {import('react').ReactNode} props.children - Primary message.
 * @param {import('react').ReactNode} [props.detail] - Trailing muted detail.
 * @param {string} [props.role] - ARIA role (default `alert`).
 * @param {string} [props.className]
 */
export function Banner({ children, detail, role = 'alert', className = '', ...rest }) {
  const classes = ['outage-banner', className].filter(Boolean).join(' ');
  return (
    <div className={classes} role={role} aria-live="assertive" {...rest}>
      {children}
      {detail != null ? <span className="outage-banner-detail"> ({detail})</span> : null}
    </div>
  );
}

/**
 * PageHeader / Hero — the sticky navy masthead (DESIGN.md §3). Richer than a
 * plain bar: an optional physics motif image sits behind the content as a
 * subtle academic accent. Left `brand` slot + right `aside` slot.
 *
 * @param {object} props
 * @param {import('react').ReactNode} props.brand - Left lockup (logo + title).
 * @param {import('react').ReactNode} [props.aside] - Right slot (brand mark).
 * @param {string} [props.motif] - URL of a background motif SVG; when set the
 *   header renders the `.header-motif` overlay.
 * @param {string} [props.className]
 */
export function PageHeader({ brand, aside, motif, className = '', ...rest }) {
  const classes = ['header', motif ? 'has-hero' : '', className].filter(Boolean).join(' ');
  return (
    <header className={classes} {...rest}>
      {motif ? (
        <div
          className="header-motif"
          aria-hidden="true"
          style={{ '--header-motif-image': `url("${motif}")` }}
        />
      ) : null}
      <div className="header-content">
        {brand}
        {aside}
      </div>
    </header>
  );
}

/**
 * Footer — the centered footer band (DESIGN.md §3).
 *
 * @param {object} props
 * @param {import('react').ReactNode} props.children - `.footer-content` contents.
 * @param {string} [props.className]
 */
export function Footer({ children, className = '', ...rest }) {
  const classes = ['footer', className].filter(Boolean).join(' ');
  return (
    <footer className={classes} {...rest}>
      <div className="footer-content">{children}</div>
    </footer>
  );
}

/**
 * AppShell — the outer scaffold: optional `banner`, a `header`, the centered
 * `.main-content` / `.content-shell` region (children), and an optional
 * `footer`. Composes the whole page from one primitive so `App.jsx` and the
 * showcase share the same chrome.
 *
 * @param {object} props
 * @param {import('react').ReactNode} [props.banner]
 * @param {import('react').ReactNode} [props.header]
 * @param {import('react').ReactNode} [props.footer]
 * @param {import('react').ReactNode} props.children - Main content sections.
 * @param {string} [props.mainClassName] - Extra class on `.content-shell`.
 */
export function AppShell({ banner, header, footer, children, mainClassName = '' }) {
  const shellClasses = ['content-shell', mainClassName].filter(Boolean).join(' ');
  return (
    <>
      {banner}
      {header}
      <main className="main-content">
        <div className={shellClasses}>{children}</div>
      </main>
      {footer}
    </>
  );
}
