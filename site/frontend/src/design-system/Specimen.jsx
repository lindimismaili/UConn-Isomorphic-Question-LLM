import { useState } from 'react';

/**
 * Showcase-only layout helpers for the design-system page. These are catalog
 * chrome (not reusable site building blocks), so they use token-referencing
 * inline styles rather than adding new classes to app.css.
 */

/** Resolves a CSS custom property's computed value from :root (tokens are static). */
function useTokenValue(token) {
  const [value] = useState(() =>
    getComputedStyle(document.documentElement).getPropertyValue(token).trim(),
  );
  return value;
}

/** Vertical stack with a token gap. */
export function Stack({ gap = 'var(--spacing-md)', children, style }) {
  return <div style={{ display: 'flex', flexDirection: 'column', gap, ...style }}>{children}</div>;
}

/** Horizontal wrapping row with a token gap and aligned items. */
export function Row({ gap = 'var(--spacing-md)', align = 'center', children, style }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: align, gap, ...style }}>
      {children}
    </div>
  );
}

/** Auto-fit grid for swatches / specimen tiles. */
export function Grid({ min = '200px', gap = 'var(--spacing-md)', children }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(auto-fill, minmax(${min}, 1fr))`,
        gap,
      }}
    >
      {children}
    </div>
  );
}

/** A labeled specimen: a small muted caption above the rendered element. */
export function Spec({ label, children, style }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)', ...style }}>
      {label != null ? (
        <span
          style={{
            fontSize: '0.72rem',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            color: 'var(--color-text-muted)',
          }}
        >
          {label}
        </span>
      ) : null}
      <Row>{children}</Row>
    </div>
  );
}

/**
 * Tile chrome for one manifest entry in the Components showcase: a bordered,
 * padded card-like frame around a live specimen + its metadata (name,
 * summary, prop table, usage snippet). Not the real `Card` primitive — this
 * is catalog-only chrome, so it stays a token-referencing inline style like
 * the other helpers in this file.
 */
export function SpecimenTile({ children, style }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--spacing-sm)',
        padding: 'var(--spacing-md)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        background: 'var(--color-surface)',
        minWidth: 0,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** A color swatch tile showing the token chip, name, and resolved value. */
export function Swatch({ token, label }) {
  const value = useTokenValue(token);
  return (
    <div
      style={{
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        background: 'var(--color-surface)',
      }}
    >
      <div style={{ height: 64, background: `var(${token})` }} />
      <div style={{ padding: 'var(--spacing-sm)', display: 'flex', flexDirection: 'column', gap: 2 }}>
        <code style={{ fontSize: '0.74rem', color: 'var(--color-text)' }}>{label || token}</code>
        <code style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)' }}>{value || token}</code>
      </div>
    </div>
  );
}

/** A token row that renders a sized box (for spacing / radius scales). */
export function SizeBox({ token, axis = 'size' }) {
  const value = useTokenValue(token);
  const boxStyle =
    axis === 'width'
      ? { width: `var(${token})`, height: 16 }
      : axis === 'radius'
        ? { width: 64, height: 64, borderRadius: `var(${token})` }
        : { width: `var(${token})`, height: `var(${token})` };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-xs)' }}>
      <div style={{ background: 'var(--gradient-primary)', ...boxStyle }} />
      <code style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)' }}>
        {token} {value ? `(${value})` : ''}
      </code>
    </div>
  );
}
