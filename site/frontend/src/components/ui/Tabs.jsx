/**
 * Tabs primitive (DESIGN.md §8 viewer tabs). Renders the `.viewer-tab-bar`
 * with `.viewer-tab` buttons; the active tab gets `.is-active`.
 *
 * @param {object} props
 * @param {string} props.value - Active tab id.
 * @param {(id: string) => void} props.onChange
 * @param {Array<{id: string, label: import('react').ReactNode}>} props.tabs
 * @param {string} [props.className]
 */
export function Tabs({ value, onChange, tabs = [], className = '' }) {
  const classes = ['viewer-tab-bar', className].filter(Boolean).join(' ');
  return (
    <div className={classes} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          className={`viewer-tab${value === tab.id ? ' is-active' : ''}`}
          onClick={() => onChange?.(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
