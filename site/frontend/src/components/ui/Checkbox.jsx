/**
 * Checkbox — the reusable native boolean-selection control.
 *
 * Use this for independent list selections. Use ToggleSwitch for settings that
 * take effect immediately.
 */
export function Checkbox({
  checked,
  onChange,
  disabled = false,
  ...rest
}) {
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange?.(event.target.checked)}
      disabled={disabled}
      {...rest}
    />
  );
}
