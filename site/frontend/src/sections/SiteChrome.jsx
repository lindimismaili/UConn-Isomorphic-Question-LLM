/**
 * Site chrome — exported section templates composed from the `ui/` shell
 * primitives (AppShell, PageHeader, Banner, Footer). These are the reusable
 * "build templates, then compose the site from them" objects: both the real
 * app (`App.jsx`) and the design-system showcase render the SAME chrome from
 * here, so the header/footer/banner exist once, not duplicated as raw markup.
 */

import { Banner, PageHeader, Footer } from '../components/ui/index.js';

/** Physics-academic motif surfaced behind the navy masthead. */
export const HEADER_MOTIF = '/assets/physics-motif.svg';

/** The UConn Physics Research brand mark (right slot of the masthead). */
export function UConnMark() {
  return (
    <div className="uconn-mark" aria-label="UCONN brand">
      <div className="uconn-text">UCONN</div>
      <div className="uconn-subtext">Physics Research</div>
    </div>
  );
}

/**
 * SiteHeader — the physics-academic masthead: AI logo + serif title on the
 * left, the UConn brand mark on the right, with the physics motif behind.
 *
 * @param {object} [props]
 * @param {string} [props.title]
 * @param {import('react').ReactNode} [props.aside] - Override the right slot.
 */
export function SiteHeader({ title = 'Isomorphic Question Generator', aside }) {
  return (
    <PageHeader
      motif={HEADER_MOTIF}
      brand={
        <div className="logo">
          <span className="logo-icon logo-icon-ai" aria-hidden="true" />
          <h1>{title}</h1>
        </div>
      }
      aside={aside ?? <UConnMark />}
    />
  );
}

/**
 * SiteBanner — the outage/notice strip. Renders nothing when not shown.
 *
 * @param {object} props
 * @param {boolean} props.show
 * @param {import('react').ReactNode} props.children
 * @param {import('react').ReactNode} [props.detail]
 */
export function SiteBanner({ show, children, detail }) {
  if (!show) return null;
  return <Banner detail={detail}>{children}</Banner>;
}

/**
 * SiteFooter — the standard footer (attribution + version line).
 *
 * @param {object} props
 * @param {import('react').ReactNode} [props.version]
 */
export function SiteFooter({ version }) {
  return (
    <Footer>
      <p className="footer-primary">
        UCONN Physics Research - Dr. Xian Wu (2025 - 2026)
      </p>
      <p className="footer-primary">Created by Lindim Ismaili</p>
      {version != null ? <p className="footer-secondary">{version}</p> : null}
    </Footer>
  );
}
