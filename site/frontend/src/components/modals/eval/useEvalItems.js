import { useEffect, useState } from 'react';

import { EMPTY_ITEMS, parseJsonl } from './evalData.js';

/**
 * Loads and parses the evaluation items JSONL for an open modal.
 *
 * @param {boolean} isOpen - Whether the modal is open (gates the fetch).
 * @param {string} itemsUrl - URL of the JSONL export.
 * @returns {{ items: Array<object>, error: string, loadState: 'unavailable'|'loading'|'loaded'|'error' }}
 */
export function useEvalItems(isOpen, itemsUrl) {
  const [result, setResult] = useState({ url: '', items: [], error: '' });

  useEffect(() => {
    if (!isOpen || !itemsUrl) return undefined;

    const controller = new AbortController();
    fetch(itemsUrl, { signal: controller.signal, cache: 'no-store' })
      .then((response) => {
        if (!response.ok) throw new Error(`Unable to load eval items (${response.status})`);
        return response.text();
      })
      .then((text) => setResult({ url: itemsUrl, items: parseJsonl(text), error: '' }))
      .catch((err) => {
        if (err.name === 'AbortError') return;
        setResult({ url: itemsUrl, items: [], error: err.message });
      });

    return () => controller.abort();
  }, [isOpen, itemsUrl]);

  const matched = result.url === itemsUrl;
  const items = matched ? result.items : EMPTY_ITEMS;
  const error = matched ? result.error : '';
  const loadState = !itemsUrl ? 'unavailable' : error ? 'error' : matched ? 'loaded' : 'loading';

  return { items, error, loadState };
}
