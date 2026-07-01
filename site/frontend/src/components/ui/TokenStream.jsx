/**
 * TokenStream — a matrix-style live-output view (DESIGN.md §15). Renders the
 * accumulated streamed text for the active node/message with a fast, GPU-light
 * "matrix rain" backdrop (CSS transforms/opacity only). When no live stream is
 * available it shows a subtle synthetic shimmer while the node is `thinking`,
 * so the surface never looks dead. Fully honors `prefers-reduced-motion`
 * (the rain layer is decorative + aria-hidden; the text stays readable).
 *
 * Feed it with `text` (accumulated token deltas). Keep only a readable tail so
 * long streams don't grow the DOM without bound.
 */

import { useEffect, useRef, useState } from 'react';

/** Decorative glyph alphabet for the rain columns (physics/greek flavored). */
const RAIN_GLYPHS = 'αβγδεζηθλμνξπρστφχψω01∑∫∂∇√∞±×';

/** How many trailing characters of the live stream to keep on screen. */
const MAX_VISIBLE_CHARS = 600;

/** Builds a deterministic-enough set of rain columns for the backdrop. */
function buildColumns(count) {
  return Array.from({ length: count }, (_, i) => {
    const glyphs = Array.from({ length: 5 }, (_, j) =>
      RAIN_GLYPHS[(i * 7 + j * 3) % RAIN_GLYPHS.length],
    );
    return { id: i, glyphs, delay: (i % 6) * 0.28, duration: 1.6 + (i % 4) * 0.35 };
  });
}

/**
 * @param {object} props
 * @param {string} [props.text] - Accumulated streamed text (token deltas joined).
 * @param {boolean} [props.active] - The node is thinking (drives the synthetic shimmer).
 * @param {number} [props.columns] - Rain column count (backdrop density).
 * @param {string} [props.placeholder] - Shown when there is no text yet.
 * @param {string} [props.className]
 */
export function TokenStream({
  text = '',
  active = false,
  columns = 10,
  placeholder = 'Waiting for tokens…',
  className = '',
  ...rest
}) {
  const visible = text.length > MAX_VISIBLE_CHARS ? text.slice(-MAX_VISIBLE_CHARS) : text;
  const hasText = visible.length > 0;
  const [cols] = useState(() => buildColumns(columns));
  const bodyRef = useRef(null);

  // Keep the newest tokens in view as the stream grows.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [visible]);

  const classes = [
    'token-stream',
    active ? 'is-active' : '',
    hasText ? 'has-text' : 'is-synthetic',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} data-active={active ? 'true' : 'false'} {...rest}>
      <div className="token-stream-rain" aria-hidden="true">
        {cols.map((col) => (
          <span
            className="token-stream-col"
            key={col.id}
            style={{
              '--stream-delay': `${col.delay}s`,
              '--stream-duration': `${col.duration}s`,
            }}
          >
            {col.glyphs.map((g, j) => (
              <span className="token-stream-glyph" key={j}>
                {g}
              </span>
            ))}
          </span>
        ))}
      </div>
      <pre className="token-stream-body" ref={bodyRef} aria-live="polite">
        {hasText ? (
          <>
            {visible}
            <span className="token-stream-cursor" aria-hidden="true" />
          </>
        ) : (
          <span className="token-stream-placeholder">{placeholder}</span>
        )}
      </pre>
    </div>
  );
}
