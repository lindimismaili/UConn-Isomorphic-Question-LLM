/**
 * Button — the single button primitive. Wraps the documented `.btn` system
 * (DESIGN.md §4): always `.btn` + exactly one variant, or `.btn-icon-only`
 * when `iconOnly`. No new styling — height tiers still come from the container
 * (e.g. `.composer-controls`, `.developer-tools-row`).
 *
 * @param {object} props
 * @param {'primary'|'secondary'|'ghost'|'danger'|null} [props.variant] - Visual
 *   variant. Pass `null` to omit `.btn-{variant}` when a premade sub-variant in
 *   `className` already supplies the look (e.g. `btn-remove`).
 * @param {'sm'|'default'} [props.size] - `sm` adds `.btn-sm` (36px compact).
 * @param {import('react').ReactNode} [props.icon] - Leading icon node (pass it
 *   pre-classed with `btn-icon`, e.g. `<IconSend className="btn-icon" />`).
 * @param {boolean} [props.iconOnly] - Render the standalone `.btn-icon-only` square.
 * @param {boolean} [props.fixedWidth] - Adds `.btn-fixed-width` (144px).
 * @param {'success'|'error'} [props.feedbackState] - Transient post-click lock
 *   (`.btn-feedback` / `.btn-error-feedback`).
 * @param {string} [props.href] - When provided, renders an anchor with the same
 *   button classes so navigation keeps link semantics.
 * @param {string} [props.className] - Extra classes (use for premade sub-variants
 *   like `developer-tool-btn`, `help-btn`, `btn-download`).
 * @param {import('react').ReactNode} [props.children] - Button label/content.
 */
export function Button({
  variant = 'secondary',
  size,
  icon,
  iconOnly = false,
  fixedWidth = false,
  feedbackState,
  href,
  className = '',
  type = 'button',
  children,
  ...rest
}) {
  if (iconOnly) {
    const iconClasses = ['btn-icon-only', className].filter(Boolean).join(' ');
    return (
      <button type={type} className={iconClasses} {...rest}>
        {icon}
        {children}
      </button>
    );
  }

  const classes = [
    'btn',
    variant ? `btn-${variant}` : '',
    size === 'sm' ? 'btn-sm' : '',
    fixedWidth ? 'btn-fixed-width' : '',
    feedbackState === 'success' ? 'btn-feedback' : '',
    feedbackState === 'error' ? 'btn-error-feedback' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const content = (
    <>
      {icon}
      {children}
    </>
  );

  if (href) {
    return (
      <a href={href} className={classes} {...rest}>
        {content}
      </a>
    );
  }

  return (
    <button type={type} className={classes} {...rest}>
      {content}
    </button>
  );
}

/**
 * IconButton — the standalone `.btn-icon-only` 36×36 square control (close
 * buttons, compact icon actions). Requires an accessible label.
 *
 * @param {object} props
 * @param {import('react').ReactNode} props.icon - Icon node.
 * @param {string} props['aria-label'] - Accessible label (icon-only control).
 */
export function IconButton({ icon, className = '', children, ...rest }) {
  return (
    <Button iconOnly icon={icon} className={className} {...rest}>
      {children}
    </Button>
  );
}
