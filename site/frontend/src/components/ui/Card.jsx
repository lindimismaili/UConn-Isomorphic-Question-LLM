/**
 * Card primitives (DESIGN.md §7). `.card` + optional documented status/context
 * variant. Cards never lift on hover.
 */

const CARD_VARIANTS = {
  completed: 'completed-card',
  failed: 'failed-card',
  main: 'main-card',
  'developer-tools': 'developer-tools-card',
  workflow: 'workflow-panel',
  'log-panel': 'log-panel-card',
};

/**
 * @param {object} props
 * @param {keyof typeof CARD_VARIANTS} [props.variant] - Documented status/context variant.
 * @param {string} [props.className]
 * @param {import('react').ReactNode} [props.children]
 */
export function Card({ variant, className = '', children, ...rest }) {
  const classes = ['card', variant ? CARD_VARIANTS[variant] || '' : '', className]
    .filter(Boolean)
    .join(' ');
  return (
    <section className={classes} {...rest}>
      {children}
    </section>
  );
}

/**
 * Card header band. Renders `title` as the h2, optional `subtitle`,
 * `disclaimer`, and an `actions` group (`.header-buttons`).
 *
 * @param {object} props
 * @param {import('react').ReactNode} [props.title]
 * @param {import('react').ReactNode} [props.subtitle]
 * @param {import('react').ReactNode} [props.disclaimer]
 * @param {import('react').ReactNode} [props.actions]
 * @param {string} [props.className]
 * @param {import('react').ReactNode} [props.children]
 */
export function CardHeader({
  title,
  subtitle,
  disclaimer,
  actions,
  className = '',
  children,
}) {
  const classes = ['card-header', className].filter(Boolean).join(' ');
  return (
    <div className={classes}>
      {title != null || actions != null ? (
        <div className="card-header-row">
          {title != null ? <h2>{title}</h2> : null}
          {actions != null ? <div className="header-buttons">{actions}</div> : null}
        </div>
      ) : null}
      {disclaimer != null ? <p className="disclaimer-text">{disclaimer}</p> : null}
      {subtitle != null ? <p className="subtitle">{subtitle}</p> : null}
      {children}
    </div>
  );
}

/**
 * Card body. Applies `--panel-padding` via `.card-body`.
 *
 * @param {object} props
 * @param {string} [props.className]
 * @param {import('react').ReactNode} [props.children]
 */
export function CardBody({ className = '', children, ...rest }) {
  const classes = ['card-body', className].filter(Boolean).join(' ');
  return (
    <div className={classes} {...rest}>
      {children}
    </div>
  );
}

/**
 * Standalone section heading (h2 + optional subtitle) for non-card headers.
 *
 * @param {object} props
 * @param {import('react').ReactNode} props.title
 * @param {import('react').ReactNode} [props.subtitle]
 */
export function SectionHeading({ title, subtitle }) {
  return (
    <>
      <h2>{title}</h2>
      {subtitle != null ? <p className="subtitle">{subtitle}</p> : null}
    </>
  );
}
