/**
 * Status + feedback primitives (DESIGN.md §10).
 */

/**
 * Status dot (`.status-indicator` + status). 12px circle with the matching
 * semantic color + glow; `thinking` pulses.
 *
 * @param {object} props
 * @param {'inactive'|'thinking'|'completed'|'errored'|'cancelled'} props.status
 * @param {string} [props.className]
 */
export function StatusDot({ status = 'inactive', className = '', ...rest }) {
  const classes = ['status-indicator', status, className].filter(Boolean).join(' ');
  return <span className={classes} data-status={status} {...rest} />;
}

/**
 * Single log line (`.log-item` + `.log-{level}`) with a `.log-time` timestamp.
 *
 * @param {object} props
 * @param {'success'|'warning'|'error'|'info'} props.level
 * @param {string} [props.time]
 * @param {import('react').ReactNode} props.children - Message.
 */
export function LogItem({ level = 'info', time, children, className = '' }) {
  const classes = ['log-item', `log-${level}`, className].filter(Boolean).join(' ');
  return (
    <div className={classes}>
      {time != null ? <span className="log-time">[{time}]</span> : null}
      <span>{children}</span>
    </div>
  );
}

/**
 * Log list — renders `LogItem`s or an `EmptyState` fallback. Entries are
 * `{id, level, time, message}`.
 *
 * @param {object} props
 * @param {Array<{id: string|number, level: string, time?: string, message: import('react').ReactNode}>} props.entries
 * @param {import('react').ReactNode} [props.emptyText]
 */
export function LogList({ entries = [], emptyText = 'Logs will appear here when you start a workflow.' }) {
  if (!entries.length) {
    return <EmptyState>{emptyText}</EmptyState>;
  }
  return (
    <div className="log-list">
      {entries.map((entry) => (
        <LogItem key={entry.id} level={entry.level} time={entry.time}>
          {entry.message}
        </LogItem>
      ))}
    </div>
  );
}

/**
 * Empty-state block (`.log-empty-state` / `.modal-empty-state`, + error variant).
 *
 * @param {object} props
 * @param {'log'|'modal'} [props.variant]
 * @param {boolean} [props.error] - Adds `.modal-empty-state-error` (modal variant).
 * @param {import('react').ReactNode} props.children
 */
export function EmptyState({
  variant = 'log',
  error = false,
  className = '',
  children,
  role,
  'aria-live': ariaLive,
  ...rest
}) {
  const base = variant === 'modal' ? 'modal-empty-state' : 'log-empty-state';
  const classes = [base, error ? 'modal-empty-state-error' : '', className]
    .filter(Boolean)
    .join(' ');
  return (
    <div
      className={classes}
      role={error ? 'alert' : role}
      aria-live={error ? 'assertive' : ariaLive}
      {...rest}
    >
      {children}
    </div>
  );
}
