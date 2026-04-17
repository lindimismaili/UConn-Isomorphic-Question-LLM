import { IconComputer, IconLightbulb, IconPatchCheck, IconPatchExclamation } from './Icons.jsx';

const STATUS_COLORS = {
  inactive: 'var(--color-text-muted)',
  queued: 'var(--color-text)',
  thinking: 'var(--color-warning)',
  completed: 'var(--color-success)',
  errored: 'var(--color-error)',
  cancelled: 'var(--color-text-muted)',
};

const DEFAULT_TIME_ZONE = 'America/New_York';

/**
 * Coerces display fields into a stable, human-readable string.
 *
 * @param {unknown} value
 * @param {string} fallback
 * @returns {string}
 */
function displayValue(value, fallback = 'Unavailable') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

/**
 * Formats timestamps for the agent card metadata row.
 *
 * @param {string | number | Date | null | undefined} value
 * @returns {string}
 */
function formatClock(value) {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try {
    return date.toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: DEFAULT_TIME_ZONE,
    });
  } catch {
    return date.toLocaleTimeString();
  }
}

/**
 * Displays one workflow node in the pipeline grid.
 *
 * @param {{agentKey: string, agent: object, onClick: Function}} props
 */
export default function AgentCard({ agentKey, agent, onClick }) {
  const nodeKind = agent.kind || agent.nodeKind || (agentKey === 'setup' || agentKey === 'output' ? 'runtime' : 'agent');
  const isRuntime = nodeKind === 'runtime';
  const statusColor = STATUS_COLORS[agent.status] || STATUS_COLORS.inactive;
  const statusLabel = agent.status.charAt(0).toUpperCase() + agent.status.slice(1);
  const executionText = typeof agent.executionTime === 'number' ? `${agent.executionTime.toFixed(2)}s` : '';
  const modelText = displayValue(agent.model);
  const reasoningText = displayValue(agent.reasoningLevel, 'none');
  const startText = agent.startTime ? formatClock(agent.startTime) : '';
  const endText = agent.endTime ? formatClock(agent.endTime) : '';

  return (
    <div
      className="card agent-card clickable"
      data-agent={agentKey}
      data-status={agent.status}
      data-node-kind={nodeKind}
      onClick={() => onClick(agentKey)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick(agentKey)}
      aria-label={`${agent.name} ${isRuntime ? 'runtime' : 'agent'} - ${statusLabel}`}
    >
      <div className="agent-header">
        <div className="agent-info">
          <div className="agent-icon" aria-hidden="true">
            <IconComputer className="agent-icon-default" />
            <IconLightbulb className="agent-icon-thinking" />
            <IconPatchCheck className="agent-icon-success" />
            <IconPatchExclamation className="agent-icon-error" />
          </div>
          <div className="agent-text">
            <div className="agent-name-row">
              <div
                className={`status-indicator ${agent.status}`}
                data-status={agent.status}
              />
              <h4>{agent.name}</h4>
            </div>
          </div>
        </div>
      </div>
      <p className="agent-description">{agent.description}</p>
      <div className="agent-metadata" aria-label="Agent configuration">
        <span title={`Model: ${modelText}`}>{modelText}</span>
        {!isRuntime && <span title={`Reasoning: ${reasoningText}`}>{reasoningText}</span>}
      </div>
      <div className="agent-timing" aria-label="Agent timing">
        <span title={startText ? `Start time: ${startText}` : 'Start'}>
          {startText ? `Start ${startText}` : 'Start'}
        </span>
        <span title={endText ? `End time: ${endText}` : 'End'}>
          {endText ? `End ${endText}` : 'End'}
        </span>
      </div>
      <div className="agent-footer-row">
        <div className="agent-status-text" style={{ color: statusColor }}>
          {statusLabel}
        </div>
        <div className={`agent-execution-time${executionText ? ' is-visible' : ''}`}>
          {executionText}
        </div>
      </div>
    </div>
  );
}
