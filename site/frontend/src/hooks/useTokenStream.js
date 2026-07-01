/**
 * useTokenStream — cosmetic SSE consumer for live agent token output.
 *
 * Opens an `EventSource` on `GET /api/workflow/{workflowId}/stream` when a
 * workflow id is set and routes `token` deltas into per-agent streamed-text
 * state. This is PURELY cosmetic — the `useWorkflow` poller remains the
 * authoritative status source. The hook degrades gracefully when the stream is
 * unavailable (404 / connection error / no EventSource): it simply reports no
 * streamed text and the UI falls back to the TokenStream's synthetic shimmer.
 *
 * SSE contract (backend, lands in parallel):
 *   event: token  → data: {"agent":"<registry key>","delta":"<text chunk>"}
 *   event: status → data: <same payload as /status>  (ignored here; poller owns it)
 *   event: done   → data: {}   (close the stream)
 */

import { useEffect, useRef, useState } from 'react';

/** Terminal workflow statuses that should close the cosmetic stream. */
const TERMINAL_STATUSES = new Set(['completed', 'errored', 'cancelled', 'blocked']);

/** Cap accumulated per-agent text so a long run can't grow memory unbounded. */
const MAX_AGENT_CHARS = 4000;

/**
 * Subscribe to the token stream for a workflow.
 *
 * @param {string|null} workflowId - Active workflow id (null when idle).
 * @param {object} [options]
 * @param {string} [options.status] - Latest known workflow status (from polling);
 *   the stream self-closes once this is terminal.
 * @param {boolean} [options.enabled] - Master switch (default true).
 * @returns {{
 *   streams: Record<string, string>,
 *   activeAgent: string | null,
 *   isStreaming: boolean,
 *   getStream: (agentKey: string) => string,
 * }}
 */
export function useTokenStream(workflowId, { status, enabled = true } = {}) {
  const [streams, setStreams] = useState({});
  const [activeAgent, setActiveAgent] = useState(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const sourceRef = useRef(null);
  const statusRef = useRef(status);

  // Keep the latest status in a ref without touching it during render (so the
  // open-effect can consult it without re-subscribing on every status tick).
  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  // Reset accumulated text whenever the workflow id changes (new run). This is
  // an external-subscription reset (the EventSource is the external system), so
  // the synchronous setState is intentional here.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStreams({});
    setActiveAgent(null);
  }, [workflowId]);

  // Close the stream once polling reports a terminal status. Closing the
  // EventSource is the external-system teardown; flipping the cosmetic flag
  // alongside it keeps the returned `isStreaming` accurate.
  useEffect(() => {
    if (status && TERMINAL_STATUSES.has(status) && sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsStreaming(false);
    }
  }, [status]);

  useEffect(() => {
    if (!enabled || !workflowId) return undefined;
    if (typeof EventSource === 'undefined') return undefined;
    // Don't open if the run already finished before we subscribed.
    if (statusRef.current && TERMINAL_STATUSES.has(statusRef.current)) return undefined;

    let source;
    try {
      source = new EventSource(`/api/workflow/${workflowId}/stream`);
    } catch {
      return undefined;
    }
    sourceRef.current = source;
    let closed = false;

    const close = () => {
      if (closed) return;
      closed = true;
      setIsStreaming(false);
      try {
        source.close();
      } catch {
        // ignore
      }
      if (sourceRef.current === source) sourceRef.current = null;
    };

    source.addEventListener('open', () => {
      if (!closed) setIsStreaming(true);
    });

    source.addEventListener('token', (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      const agent = payload?.agent;
      const delta = typeof payload?.delta === 'string' ? payload.delta : '';
      if (!agent || !delta) return;
      setActiveAgent(agent);
      setStreams((prev) => {
        const nextText = `${prev[agent] || ''}${delta}`;
        const capped = nextText.length > MAX_AGENT_CHARS ? nextText.slice(-MAX_AGENT_CHARS) : nextText;
        return { ...prev, [agent]: capped };
      });
    });

    source.addEventListener('done', () => {
      close();
    });

    // `status` events are ignored on purpose — the poller is authoritative.
    // A raw connection error (e.g. the stream endpoint 404s) also lands here;
    // we just close and let the UI fall back to the synthetic shimmer.
    source.addEventListener('error', () => {
      // EventSource auto-reconnects on transient errors; only give up when the
      // connection is truly closed (readyState CLOSED === 2).
      if (source.readyState === 2) close();
    });

    return close;
  }, [enabled, workflowId]);

  return {
    streams,
    activeAgent,
    isStreaming,
    getStream: (agentKey) => streams[agentKey] || '',
  };
}
