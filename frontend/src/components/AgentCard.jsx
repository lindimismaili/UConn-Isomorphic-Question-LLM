import { IconComputer, IconLightbulb, IconPatchCheck, IconPatchExclamation } from './Icons.jsx';

const STATUS_COLORS = {
  inactive: 'var(--color-text-muted)',
  queued: '#ffffff',
  thinking: 'var(--color-warning)',
  completed: 'var(--color-success)',
  errored: 'var(--color-error)',
  cancelled: 'var(--color-text-muted)',
};

const DEFAULT_TIME_ZONE = 'America/New_York';

function displayValue(value, fallback = 'Unavailable') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

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

export default function AgentCard({ agentKey, agent, onClick }) {
  const statusColor = STATUS_COLORS[agent.status] || STATUS_COLORS.inactive;
  const statusLabel = agent.status.charAt(0).toUpperCase() + agent.status.slice(1);
  const executionText = typeof agent.executionTime === 'number' ? `${agent.executionTime.toFixed(2)}s` : '';
  const modelText = displayValue(agent.model);
  const reasoningText = displayValue(agent.reasoningLevel, 'none');
  const startText = agent.startTime ? formatClock(agent.startTime) : 'Not started';
  const endText = agent.endTime ? formatClock(agent.endTime) : 'Not ended';

  return (
    <div
      className="card agent-card clickable"
      data-agent={agentKey}
      data-status={agent.status}
      onClick={() => onClick(agentKey)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick(agentKey)}
      aria-label={`${agent.name} agent - ${statusLabel}`}
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
        <span title={`Reasoning: ${reasoningText}`}>{reasoningText}</span>
      </div>
      <div className="agent-timing" aria-label="Agent timing">
        <span title={`Start time: ${startText}`}>Start {startText}</span>
        <span title={`End time: ${endText}`}>End {endText}</span>
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
