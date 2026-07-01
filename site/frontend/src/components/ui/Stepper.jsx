/**
 * Stepper — a compact inline process-rail (DESIGN.md §15). Renders a workflow's
 * nodes in order with a per-node status dot and kind affordance (runtime,
 * guardrail = shield, plain agent). Clicking a node fires `onNodeClick(key)` so
 * the host can open that node's modal. The node list is caller-supplied (the
 * host derives it from the agent registry — the Stepper never hardcodes keys).
 *
 * An optional `stream` slot (usually a `TokenStream`) renders inside the active
 * node so live token output shows exactly where work is happening.
 */

import { IconShield, IconComputer, IconLightbulb, IconPatchCheck, IconPatchExclamation } from '../Icons.jsx';

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

/** Picks the glyph for a node by kind + status. */
function StepIcon({ kind, status }) {
  if (kind === 'guardrail') return <IconShield className="stepper-node-icon" />;
  if (kind === 'runtime') return <IconComputer className="stepper-node-icon" />;
  if (status === 'completed') return <IconPatchCheck className="stepper-node-icon" />;
  if (status === 'errored' || status === 'blocked') return <IconPatchExclamation className="stepper-node-icon" />;
  if (status === 'thinking') return <IconLightbulb className="stepper-node-icon" />;
  return <IconComputer className="stepper-node-icon" />;
}

/**
 * @param {object} props
 * @param {Array<{key: string, label: string, status?: string, kind?: string}>} props.nodes
 * @param {(key: string) => void} [props.onNodeClick]
 * @param {string} [props.activeKey] - Node currently working (gets the `stream` slot).
 * @param {import('react').ReactNode} [props.stream] - Live-output node (e.g. TokenStream).
 * @param {string} [props.className]
 * @param {string} [props['aria-label']]
 */
export function Stepper({ nodes = [], onNodeClick, activeKey, stream, className = '', ...rest }) {
  const classes = ['stepper', className].filter(Boolean).join(' ');
  return (
    <div className={classes} role="list" aria-label={rest['aria-label'] || 'Workflow progress'} {...rest}>
      {nodes.map((node, index) => {
        const status = STEP_STATUS[node.status] || 'inactive';
        const kind = node.kind || 'agent';
        const isActive = node.key === activeKey;
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
              onClick={() => onNodeClick?.(node.key)}
              title={`${node.label} — ${status}`}
              aria-label={`${node.label} (${status})`}
            >
              <span className="stepper-node-badge" data-status={status}>
                <StepIcon kind={kind} status={status} />
              </span>
              <span className="stepper-node-label">{node.label}</span>
              <span className={`status-indicator ${status}`} data-status={status} aria-hidden="true" />
              {isActive && stream != null ? (
                <span className="stepper-node-stream">{stream}</span>
              ) : null}
            </button>
          </div>
        );
      })}
    </div>
  );
}
