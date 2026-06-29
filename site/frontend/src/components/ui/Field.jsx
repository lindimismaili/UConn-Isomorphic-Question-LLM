/**
 * Form-control primitives (DESIGN.md §5). All emit bare/native control markup
 * so the global input/select/textarea styling (44px, `--radius-sm`, focus ring)
 * applies. No new CSS.
 */

/**
 * Single-line text input. Inherits the global `input` styling.
 *
 * @param {object} props - Standard input props (`value`, `onChange`, `type`, …).
 */
export function TextInput({ className = '', type = 'text', ...rest }) {
  return <input type={type} className={className} {...rest} />;
}

/**
 * Multi-line text area. Defaults to the `.prompt-input` treatment
 * (180px min-height, `--radius-md`) per §5.
 *
 * @param {object} props - Standard textarea props.
 */
export function TextArea({ className = '', ...rest }) {
  const classes = ['prompt-input', className].filter(Boolean).join(' ');
  return <textarea className={classes} {...rest} />;
}

/**
 * Select control. Renders `options` (or `children`) inside the documented
 * `.prompt-dropdown-select`. Pass `placeholder` to add a leading empty option.
 *
 * @param {object} props
 * @param {Array<{value: string, label: string}>} [props.options]
 * @param {string} [props.placeholder]
 * @param {import('react').ReactNode} [props.children]
 */
export function Select({
  className = '',
  options,
  placeholder,
  children,
  ...rest
}) {
  const classes = ['prompt-dropdown-select', className].filter(Boolean).join(' ');
  return (
    <select className={classes} {...rest}>
      {placeholder ? <option value="">{placeholder}</option> : null}
      {Array.isArray(options)
        ? options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))
        : children}
    </select>
  );
}
