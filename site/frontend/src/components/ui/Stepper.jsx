/**
 * Stepper — a compact inline process-rail (DESIGN.md §15). Renders a workflow's
 * nodes in order with a per-node status dot and a kind/status glyph: runtime
 * nodes show a computer, guardrail nodes a warning→shield (pass) / shield-alert
 * (fail), and LLM agents a brain that becomes a lightbulb while thinking and a
 * success/alert patch when done. Clicking a node fires `onNodeClick(key)` so the
 * host can open that node's modal. The node list is caller-supplied (the host
 * derives it from the agent registry — the Stepper never hardcodes keys).
 *
 * While a node is `thinking`, a cycling status word ("Thinking…",
 * "Combobulating…", …) shows above its icon, and the rail auto-scrolls to keep
 * the active node centered as the workflow advances. Labels are always a single
 * ellipsized line so every node stays the same height.
 */

import { useEffect, useRef, useState } from 'react';
import {
  IconShield,
  IconShieldExclamation,
  IconWarning,
  IconBrain,
  IconComputer,
  IconLightbulb,
  IconPatchCheck,
  IconPatchExclamation,
} from '../Icons.jsx';

/** Status → semantic dot class (mirrors the `.status-indicator` families). */
const STEP_STATUS = {
  inactive: 'inactive',
  queued: 'queued',
  thinking: 'thinking',
  completed: 'completed',
  blocked: 'blocked',
  errored: 'errored',
  cancelled: 'cancelled',
};

/** Playful status words cycled above the active node's icon while it works. */
const THINKING_WORDS = [
  'Thinking',
  'Combobulating',
  'Cogitating',
  'Percolating',
  'Synthesizing',
  'Calculating',
  'Pondering',
  'Distilling',
  'Reasoning',
  'Assembling',
  'Calibrating',
  'Conjuring',
];

/** How often the thinking word above the active node swaps (ms). */
const THINKING_WORD_INTERVAL_MS = 1400;

/**
 * Cycles through THINKING_WORDS (never repeating back-to-back) while active.
 * Always opens on "Thinking…" and shuffles from there on each interval tick.
 */
function useThinkingWord(active) {
  const [word, setWord] = useState(THINKING_WORDS[0]);
  useEffect(() => {
    if (!active) return undefined;
    const pickNext = (prev) => {
      let next = prev;
      while (next === prev) {
        next = THINKING_WORDS[Math.floor(Math.random() * THINKING_WORDS.length)];
      }
      return next;
    };
    const id = setInterval(() => setWord((prev) => pickNext(prev)), THINKING_WORD_INTERVAL_MS);
    return () => clearInterval(id);
  }, [active]);
  return word;
}

/** Picks the glyph for a node by kind + status. */
function StepIcon({ kind, status }) {
  // Runtime nodes (setup/output) always keep the computer glyph.
  if (kind === 'runtime') return <IconComputer className="stepper-node-icon" />;
  // Guardrail nodes: a warning at rest, the passing shield on success, the
  // shield-alert on failure, and the lightbulb while checking.
  if (kind === 'guardrail') {
    if (status === 'completed') return <IconShield className="stepper-node-icon" />;
    if (status === 'errored' || status === 'blocked') return <IconShieldExclamation className="stepper-node-icon" />;
    if (status === 'thinking') return <IconLightbulb className="stepper-node-icon" />;
    return <IconWarning className="stepper-node-icon" />;
  }
  // LLM agent nodes: a brain at rest, a lightbulb while thinking, and the
  // success / alert patch when the stage finishes.
  if (status === 'completed') return <IconPatchCheck className="stepper-node-icon" />;
  if (status === 'errored' || status === 'blocked') return <IconPatchExclamation className="stepper-node-icon" />;
  if (status === 'thinking') return <IconLightbulb className="stepper-node-icon" />;
  return <IconBrain className="stepper-node-icon" />;
}

/**
 * @param {object} props
 * @param {Array<{key: string, label: string, status?: string, kind?: string}>} props.nodes
 * @param {(key: string) => void} [props.onNodeClick]
 * @param {string} [props.activeKey] - Node currently working (auto-scrolled into view).
 * @param {string} [props.className]
 * @param {string} [props['aria-label']]
 */
export function Stepper({ nodes = [], onNodeClick, activeKey, className = '', ...rest }) {
  const railRef = useRef(null);
  const anyThinking = nodes.some((node) => node.status === 'thinking');
  const word = useThinkingWord(anyThinking);

  // Keep the active node centered in the rail as the workflow advances.
  useEffect(() => {
    const rail = railRef.current;
    if (!rail || !activeKey) return;
    const node = rail.querySelector(`[data-node-key="${activeKey}"]`);
    if (!node) return;
    const railRect = rail.getBoundingClientRect();
    const nodeRect = node.getBoundingClientRect();
    const target = rail.scrollLeft + (nodeRect.left - railRect.left) - (railRect.width - nodeRect.width) / 2;
    if (typeof rail.scrollTo === 'function') {
      rail.scrollTo({ left: Math.max(0, target), behavior: 'smooth' });
    } else {
      rail.scrollLeft = Math.max(0, target);
    }
  }, [activeKey]);

  const classes = ['stepper', className].filter(Boolean).join(' ');
  return (
    <div
      className={classes}
      role="list"
      aria-label={rest['aria-label'] || 'Workflow progress'}
      ref={railRef}
      {...rest}
    >
      {nodes.map((node, index) => {
        const status = STEP_STATUS[node.status] || 'inactive';
        const kind = node.kind || 'agent';
        const isActive = node.key === activeKey;
        const isThinking = status === 'thinking';
        return (
          <div className="stepper-item" role="listitem" key={node.key}>
            {index > 0 ? (
              <span
                className="stepper-connector"
                data-status={status}
                aria-hidden="true"
              />
            ) : null}
            <button
              type="button"
              className={`stepper-node${isActive ? ' is-active' : ''}`}
              data-status={status}
              data-node-kind={kind}
              data-node-key={node.key}
              onClick={() => onNodeClick?.(node.key)}
              disabled={!onNodeClick}
              title={
                onNodeClick
                  ? `${node.label} — ${status}`
                  : `${node.label} — ${status} (status only in this view)`
              }
              aria-label={`${node.label} (${status})`}
            >
              <span className="stepper-node-word" aria-hidden="true">
                {isThinking ? (
                  <span className="stepper-node-word-text" key={word}>
                    {word}…
                  </span>
                ) : null}
              </span>
              <span className="stepper-node-badge" data-status={status}>
                <StepIcon kind={kind} status={status} />
              </span>
              <span className="stepper-node-label">{node.label}</span>
              <span className={`status-indicator ${status}`} data-status={status} aria-hidden="true" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
