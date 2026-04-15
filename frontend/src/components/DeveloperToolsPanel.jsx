import { IconFile, IconList, IconZap } from './Icons.jsx';

function ToolAction({
  title,
  description,
  icon,
  onClick,
  disabled = false,
}) {
  return (
    <div className="developer-tool-item">
      <button
        className="btn btn-secondary developer-tool-button"
        onClick={onClick}
        disabled={disabled}
        type="button"
      >
        {icon}
        <span className="btn-label">{title}</span>
      </button>
      <p className="developer-tool-description">{description}</p>
    </div>
  );
}

export default function DeveloperToolsPanel({
  serviceInactive,
  isRunning,
  isCreateDataRunning,
  onShowSkills,
  onShowTestFiles,
  onCreateData,
}) {
  return (
    <section className="card developer-tools-card">
      <div className="card-header">
        <h2>Developer Tools</h2>
        <p className="subtitle">Inspect prompts, use evaluation fixtures, and run test-data batches without leaving the main workspace.</p>
      </div>
      <div className="card-body developer-tools-body">
        <ToolAction
          title="View Skills"
          description="Browse the available `SKILL.md` files in a consistent viewer."
          icon={<IconList className="btn-icon" />}
          onClick={onShowSkills}
        />
        <ToolAction
          title={isCreateDataRunning ? 'Test Data Running...' : 'Create Test Data'}
          description="Open batch controls for the current prompt and files from `data/evaluation`."
          icon={<IconZap className="btn-icon" />}
          onClick={onCreateData}
          disabled={isRunning || (serviceInactive && !isCreateDataRunning)}
        />
        <ToolAction
          title="Browse Test Files"
          description="Select canned evaluation documents and queue them for the next workflow run."
          icon={<IconFile className="btn-icon" />}
          onClick={onShowTestFiles}
          disabled={serviceInactive || isRunning}
        />
      </div>
    </section>
  );
}
