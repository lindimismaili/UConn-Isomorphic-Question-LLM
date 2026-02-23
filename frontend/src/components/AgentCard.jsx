import { IconComputer, IconLightbulb, IconPatchCheck, IconPatchExclamation } from './Icons.jsx';

const STATUS_COLORS = {
  inactive: 'var(--color-text-muted)',
  queued: '#ffffff',
  thinking: 'var(--color-warning)',
  completed: 'var(--color-success)',
  errored: 'var(--color-error)',
};

export default function AgentCard({ agentKey, agent, onClick }) {
  const statusColor = STATUS_COLORS[agent.status] || STATUS_COLORS.inactive;
  const statusLabel = agent.status.charAt(0).toUpperCase() + agent.status.slice(1);
  const executionText = typeof agent.executionTime === 'number' ? `${agent.executionTime.toFixed(2)}s` : '';

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
