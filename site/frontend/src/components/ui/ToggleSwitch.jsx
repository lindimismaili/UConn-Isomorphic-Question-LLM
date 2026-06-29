/**
 * ToggleSwitch — the documented `.developer-toggle` boolean control
 * (DESIGN.md §6): a label-wrapped checkbox styled as a switch. Use this for any
 * on/off setting instead of a raw checkbox.
 *
 * @param {object} props
 * @param {boolean} props.checked
 * @param {(checked: boolean) => void} props.onChange
 * @param {import('react').ReactNode} props.label
 * @param {boolean} [props.disabled]
 * @param {string} [props.className]
 */
export function ToggleSwitch({
  checked,
  onChange,
  label,
  disabled = false,
  className = '',
  ...rest
}) {
  const classes = ['developer-toggle', className].filter(Boolean).join(' ');
  return (
    <label className={classes}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange?.(event.target.checked)}
        disabled={disabled}
        {...rest}
      />
      <span className="developer-toggle-track" aria-hidden="true">
        <span className="developer-toggle-thumb" />
      </span>
      {label != null ? <span className="developer-toggle-label">{label}</span> : null}
    </label>
  );
}
