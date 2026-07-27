/**
 * Dropdown — a custom accessible listbox (DESIGN.md §5). Replaces the native
 * `<select>` for the app's list selectors so the control + option list are fully
 * site-themed. The popup is portaled to `document.body` and fixed-positioned
 * from the trigger, so it is never clipped by an `overflow:hidden` chat shell or
 * a scrolling modal body, and it opens upward when there's no room below.
 *
 * Controlled: pass `value` + `onChange(value)` — `onChange` receives the selected
 * VALUE directly (not a DOM event). The trigger uses the select-only combobox
 * pattern: DOM focus stays on it while the active listbox option is exposed via
 * `aria-activedescendant`, which also keeps it safe inside a modal focus trap.
 */

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/** How long the typeahead buffer persists between keystrokes (ms). */
const TYPEAHEAD_RESET_MS = 500;

/** The trigger chevron (rotates when open); inherits `currentColor`. */
function DropdownChevron() {
  return (
    <svg
      className="dropdown-chevron"
      viewBox="0 0 12 8"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M1 1.5 6 6.5l5-5" />
    </svg>
  );
}

/**
 * @param {object} props
 * @param {string} [props.value] - Selected value ('' = none).
 * @param {(value: string) => void} [props.onChange] - Called with the chosen value.
 * @param {Array<{value: string, label: string, disabled?: boolean}>} [props.options]
 * @param {string} [props.placeholder] - Adds a selectable leading "none" entry + muted trigger text.
 * @param {'default'|'sm'} [props.size] - Control height tier (44 / 36).
 * @param {'default'|'onDark'} [props.variant] - `onDark` = navy-masthead trigger treatment.
 * @param {boolean} [props.disabled]
 * @param {string} [props.className] - Lands on the root `.dropdown` wrapper.
 * @param {string} [props.id] - Lands on the trigger button (label `htmlFor` target).
 * @param {string} [props['aria-label']]
 */
export function Dropdown({
  value = '',
  onChange,
  options = [],
  placeholder,
  size = 'default',
  variant = 'default',
  disabled = false,
  className = '',
  id,
  'aria-label': ariaLabel,
  ...rest
}) {
  const reactId = useId();
  const baseId = id || `dropdown-${reactId}`;
  const menuId = `${baseId}-menu`;
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const typeahead = useRef({ buffer: '', timer: 0 });

  // The placeholder is modeled as a real selectable leading option (parity with
  // the native leading `<option value="">`), so keyboard nav can return to it.
  const items =
    placeholder != null
      ? [{ value: '', label: placeholder, isPlaceholder: true }, ...options]
      : options;

  const selected = items.find((opt) => opt.value === value);
  const selectedLabel = selected && !selected.isPlaceholder ? selected.label : null;

  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const [menuStyle, setMenuStyle] = useState(null);
  const [dropUp, setDropUp] = useState(false);

  const isEnabled = (opt) => Boolean(opt) && !opt.disabled;
  const firstEnabled = () => items.findIndex(isEnabled);
  const lastEnabled = () => {
    for (let i = items.length - 1; i >= 0; i -= 1) if (isEnabled(items[i])) return i;
    return -1;
  };
  // Move to the next enabled option in `dir` (no wrap — matches native select).
  const step = (from, dir) => {
    let i = from;
    for (let n = 0; n < items.length; n += 1) {
      i += dir;
      if (i < 0 || i >= items.length) return from;
      if (isEnabled(items[i])) return i;
    }
    return from;
  };

  const computePosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const menuH = menuRef.current ? menuRef.current.scrollHeight : 0;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const spaceBelow = vh - rect.bottom;
    const spaceAbove = rect.top;
    const up = spaceBelow < Math.min(menuH || 200, 240) && spaceAbove > spaceBelow;
    const maxHeight = Math.max(120, (up ? spaceAbove : spaceBelow) - 8);
    const width = rect.width;
    let left = rect.left;
    if (left + width > vw - 4) left = Math.max(4, vw - width - 4);
    const top = up ? Math.max(4, rect.top - Math.min(menuH, maxHeight) - 4) : rect.bottom + 4;
    setDropUp(up);
    setMenuStyle({ position: 'fixed', top, left, width, maxHeight });
  }, []);

  // Position on open, and keep the menu glued to the trigger on scroll/resize.
  useLayoutEffect(() => {
    if (!open) return undefined;
    // Measure the trigger + menu and position before paint.
    computePosition();
    let raf = 0;
    const onScrollOrResize = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        computePosition();
      });
    };
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
    };
  }, [open, computePosition]);

  // Outside pointerdown closes the menu (without moving focus).
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (triggerRef.current?.contains(event.target) || menuRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [open]);

  // Keep the highlighted option scrolled into view.
  useEffect(() => {
    if (!open || highlighted < 0 || !menuRef.current) return;
    const el = menuRef.current.querySelector(`[data-index="${highlighted}"]`);
    if (typeof el?.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  }, [open, highlighted]);

  useEffect(() => () => clearTimeout(typeahead.current.timer), []);

  const closeAndFocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const commit = (index) => {
    const opt = items[index];
    if (!isEnabled(opt)) return;
    onChange?.(opt.value);
    closeAndFocus();
  };

  const openMenu = (startIndex) => {
    setOpen(true);
    const selIdx = items.findIndex((opt) => opt.value === value);
    setHighlighted(startIndex ?? (selIdx >= 0 && isEnabled(items[selIdx]) ? selIdx : firstEnabled()));
  };

  const runTypeahead = (char) => {
    const state = typeahead.current;
    clearTimeout(state.timer);
    state.buffer += char.toLowerCase();
    state.timer = setTimeout(() => {
      state.buffer = '';
    }, TYPEAHEAD_RESET_MS);
    const match = items.findIndex(
      (opt) => isEnabled(opt) && String(opt.label).toLowerCase().startsWith(state.buffer),
    );
    if (match >= 0) {
      setOpen(true);
      setHighlighted(match);
    }
  };

  const isPrintable = (event) =>
    event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey;

  const onKeyDown = (event) => {
    if (disabled) return;
    const { key } = event;
    if (!open) {
      if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Enter' || key === ' ' || key === 'Spacebar') {
        event.preventDefault();
        openMenu(key === 'ArrowUp' ? lastEnabled() : undefined);
      } else if (isPrintable(event)) {
        event.preventDefault();
        runTypeahead(key);
      }
      return;
    }
    // Keys the open menu fully owns also stop propagation so they don't reach
    // ancestor handlers (e.g. a host Modal's Escape-to-close). Tab is the
    // exception: it closes the menu and is left to flow to the modal focus trap.
    switch (key) {
      case 'ArrowDown':
        event.preventDefault();
        event.stopPropagation();
        setHighlighted((h) => step(h < 0 ? firstEnabled() - 1 : h, 1));
        break;
      case 'ArrowUp':
        event.preventDefault();
        event.stopPropagation();
        setHighlighted((h) => step(h < 0 ? lastEnabled() + 1 : h, -1));
        break;
      case 'Home':
        event.preventDefault();
        event.stopPropagation();
        setHighlighted(firstEnabled());
        break;
      case 'End':
        event.preventDefault();
        event.stopPropagation();
        setHighlighted(lastEnabled());
        break;
      case 'Enter':
      case ' ':
      case 'Spacebar':
        event.preventDefault();
        event.stopPropagation();
        if (highlighted >= 0) commit(highlighted);
        break;
      case 'Escape':
        event.preventDefault();
        event.stopPropagation();
        closeAndFocus();
        break;
      case 'Tab':
        setOpen(false);
        break;
      default:
        if (isPrintable(event)) {
          event.preventDefault();
          event.stopPropagation();
          runTypeahead(key);
        }
    }
  };

  const rootClass = [
    'dropdown',
    open ? 'is-open' : '',
    disabled ? 'is-disabled' : '',
    size === 'sm' ? 'dropdown-sm' : '',
    variant === 'onDark' ? 'dropdown-onDark' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const activeOptionId = open && highlighted >= 0 ? `${baseId}-opt-${highlighted}` : undefined;

  return (
    <div className={rootClass}>
      <button
        type="button"
        role="combobox"
        id={baseId}
        ref={triggerRef}
        className="dropdown-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-activedescendant={activeOptionId}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        {...rest}
      >
        <span className={`dropdown-trigger-label${selectedLabel ? '' : ' is-placeholder'}`}>
          {selectedLabel ?? placeholder ?? ''}
        </span>
        <DropdownChevron />
      </button>

      {open
        ? createPortal(
            <ul
              id={menuId}
              ref={menuRef}
              role="listbox"
              aria-label={ariaLabel}
              aria-labelledby={ariaLabel ? undefined : baseId}
              className={`dropdown-menu${dropUp ? ' is-up' : ''}${size === 'sm' ? ' dropdown-sm' : ''}`}
              style={menuStyle || { position: 'fixed', top: 0, left: 0 }}
            >
              {items.map((opt, index) => {
                const optDisabled = Boolean(opt.disabled);
                const optionClass = [
                  'dropdown-option',
                  opt.value === value ? 'is-selected' : '',
                  index === highlighted ? 'is-highlighted' : '',
                  optDisabled ? 'is-disabled' : '',
                  opt.isPlaceholder ? 'is-placeholder' : '',
                ]
                  .filter(Boolean)
                  .join(' ');
                return (
                  <li
                    key={`${opt.value}-${index}`}
                    id={`${baseId}-opt-${index}`}
                    data-index={index}
                    role="option"
                    aria-selected={opt.value === value}
                    aria-disabled={optDisabled || undefined}
                    className={optionClass}
                    onMouseEnter={() => !optDisabled && setHighlighted(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => !optDisabled && commit(index)}
                  >
                    {opt.label}
                  </li>
                );
              })}
            </ul>,
            document.body,
          )
        : null}
    </div>
  );
}
