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
  const [text, setText] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen || !url) {
      setText(null);
      setError(null);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setText(null);
    setError(null);

    fetch(url, { signal: controller.signal, cache: 'no-store' })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((t) => {
        setText(t);
        setLoading(false);
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        setError(err.message || 'Failed to load file.');
        setLoading(false);
      });

    return () => controller.abort();
  }, [url, isOpen]);

  return { text, loading, error };
}
