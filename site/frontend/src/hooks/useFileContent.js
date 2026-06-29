import { useEffect, useState } from 'react';

/**
 * Fetches raw text content from a URL when the modal is open.
 * Aborts on close or URL change.
 *
 * @param {string} url
 * @param {boolean} isOpen
 * @returns {{ text: string | null, loading: boolean, error: string | null }}
 */
export default function useFileContent(url, isOpen) {
  const key = isOpen && url ? url : '';
  const [state, setState] = useState({ key: '', text: null, loading: false, error: null });

  // Render-phase reset keyed on the active URL: clears stale content the
  // moment the target changes instead of one effect-pass later.
  if (state.key !== key) {
    setState({ key, text: null, loading: Boolean(key), error: null });
  }

  useEffect(() => {
    if (!key) return undefined;

    const controller = new AbortController();

    fetch(key, { signal: controller.signal, cache: 'no-store' })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((text) => {
        setState((prev) => (prev.key === key ? { ...prev, text, loading: false } : prev));
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        setState((prev) => (
          prev.key === key
            ? { ...prev, error: err.message || 'Failed to load file.', loading: false }
            : prev
        ));
      });

    return () => controller.abort();
  }, [key]);

  return { text: state.text, loading: state.loading, error: state.error };
}
