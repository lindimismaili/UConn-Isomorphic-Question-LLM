/**
 * Badge / chip / pill primitives (DESIGN.md §10). Status meaning is carried by
 * the documented classes — never a raw hex.
 */

const BADGE_KINDS = {
  'source-id': 'eval-source-id',
  'reference-kind': 'eval-reference-kind',
  'rubric-index': 'rubric-index',
  'rubric-category': 'rubric-category',
  'step-chip': 'create-data-step-chip',
};

/**
 * Small tinted tag. `kind` selects the documented badge class; `tone` adds the
 * reference-kind color variant (`is-input` / `is-test` / `is-rubric`).
 *
 * @param {object} props
 * @param {keyof typeof BADGE_KINDS} props.kind
 * @param {'input'|'test'|'rubric'} [props.tone]
 * @param {string} [props.className]
 * @param {import('react').ReactNode} [props.children]
 */
export function Badge({ kind = 'source-id', tone, className = '', children, ...rest }) {
  const classes = [
    BADGE_KINDS[kind] || BADGE_KINDS['source-id'],
    tone ? `is-${tone}` : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <span className={classes} {...rest}>
      {children}
    </span>
  );
}

/**
 * Eval status chip (`.eval-status-chip` + `.eval-status-{status}`).
 *
 * @param {object} props
 * @param {string} props.status - e.g. `passed|completed|failed|error|skipped|…`.
 * @param {import('react').ReactNode} [props.children] - Label (defaults to status).
 */
export function StatusChip({ status, className = '', children, ...rest }) {
  const classes = ['eval-status-chip', `eval-status-${status}`, className]
    .filter(Boolean)
    .join(' ');
  return (
    <span className={classes} {...rest}>
      {children ?? status}
    </span>
  );
}

/**
 * Eval score chip (`.eval-score-chip` + `.is-pass|.is-fail|.is-neutral`).
 *
 * @param {object} props
 * @param {'pass'|'fail'|'neutral'} props.tone
 * @param {string} [props.className]
 * @param {import('react').ReactNode} [props.children]
 */
export function ScoreChip({ tone = 'neutral', className = '', children, ...rest }) {
  const classes = ['eval-score-chip', `is-${tone}`, className].filter(Boolean).join(' ');
  return (
    <span className={classes} {...rest}>
      {children}
    </span>
  );
}

/**
 * Create-data status pill (`.create-data-pill` + `.is-{state}`).
 *
 * @param {object} props
 * @param {string} props.state - e.g. `idle|queued|running|completed|errored|cancelled`.
 * @param {import('react').ReactNode} [props.children]
 */
export function Pill({ state = 'idle', className = '', children, ...rest }) {
  const classes = ['create-data-pill', `is-${state}`, className].filter(Boolean).join(' ');
  return (
    <span className={classes} {...rest}>
      {children}
    </span>
  );
}
