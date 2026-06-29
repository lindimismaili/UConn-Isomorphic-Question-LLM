/**
 * Table primitives (DESIGN.md §8/§10). Thin wrappers over the documented table
 * classes; cell-level classes stay pass-through so the existing eval/file cells
 * keep working.
 */

const TABLE_VARIANTS = {
  eval: 'eval-table',
  'file-viewer': 'file-viewer-table',
};

/**
 * Scrollable table. `variant` selects the documented base class. Wrap in the
 * matching `*-shell` container at the call site when a scroll frame is needed.
 *
 * @param {object} props
 * @param {'eval'|'file-viewer'} [props.variant]
 * @param {string} [props.className]
 * @param {import('react').ReactNode} props.children - `<thead>` / `<tbody>`.
 */
export function DataTable({ variant = 'file-viewer', className = '', children, ...rest }) {
  const classes = [TABLE_VARIANTS[variant] || TABLE_VARIANTS['file-viewer'], className]
    .filter(Boolean)
    .join(' ');
  return (
    <table className={classes} {...rest}>
      {children}
    </table>
  );
}

/**
 * Key/value table (`.viewer-kv-table`) for config-style rows.
 *
 * @param {object} props
 * @param {Array<{key: import('react').ReactNode, value: import('react').ReactNode}>} props.rows
 * @param {string} [props.className]
 */
export function KeyValueTable({ rows = [], className = '' }) {
  const classes = ['viewer-kv-table', className].filter(Boolean).join(' ');
  return (
    <div className={classes}>
      {rows.map((row, index) => (
        <div className="viewer-kv-row" key={index}>
          <div className="viewer-kv-key">{row.key}</div>
          <div className="viewer-kv-value">{row.value}</div>
        </div>
      ))}
    </div>
  );
}
