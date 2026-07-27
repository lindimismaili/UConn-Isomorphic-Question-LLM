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
  const moveFocus = (event, currentIndex) => {
    let nextIndex;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = tabs.length - 1;
    else return;

    event.preventDefault();
    onChange?.(tabs[nextIndex]?.id);
    event.currentTarget.parentElement
      ?.querySelectorAll('[role="tab"]')
      ?.[nextIndex]
      ?.focus();
  };

  return (
    <div className={classes} role="tablist">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          tabIndex={value === tab.id ? 0 : -1}
          className={`viewer-tab${value === tab.id ? ' is-active' : ''}`}
          onClick={() => onChange?.(tab.id)}
          onKeyDown={(event) => moveFocus(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
