import { IconFile, IconUpload, IconZap } from './Icons.jsx';
import LogPanel from './LogPanel.jsx';

/**
 * Right-hand panel containing developer / debugging actions.
 *
 * @param {object} props - State and callbacks supplied by App.jsx.
 */
export default function DeveloperToolsPanel({
  isRunning,
  serviceInactive,
  isCreateDataRunning,
  onCreateData,
  onBatchEval,
  isBatchEvalRunning,
  onInputToAgent,
  onShowTestFiles,
  runEvaluations,
  onRunEvaluationsChange,
  logs,
}) {
  return (
    <section className="card developer-tools-card">
      <div className="card-header">
        <div className="card-header-row">
          <h2>Developer Tools</h2>
        </div>
        <p className="subtitle">
          Run evaluations, create synthetic test data, feed a specific agent, or
          browse stored fixtures.
        </p>
      </div>

      <div className="card-body">
        <div className="developer-tools-row">
          <label className="developer-toggle">
            <input
              type="checkbox"
              checked={runEvaluations}
              onChange={(event) => onRunEvaluationsChange(event.target.checked)}
              disabled={isRunning}
            />
            <span className="developer-toggle-track" aria-hidden="true">
              <span className="developer-toggle-thumb" />
            </span>
            <span className="developer-toggle-label">Run Evaluations</span>
          </label>

          <button
            className="btn btn-secondary developer-tool-btn"
            onClick={onCreateData}
            disabled={isRunning || (serviceInactive && !isCreateDataRunning)}
            type="button"
          >
            <IconZap className="btn-icon" />
            <span className="btn-label">
              {isCreateDataRunning ? 'Test Data Running...' : 'Create Test Data'}
            </span>
          </button>

          <button
            className="btn btn-secondary developer-tool-btn"
            onClick={onBatchEval}
            disabled={isRunning || (serviceInactive && !isBatchEvalRunning)}
            type="button"
          >
            <IconZap className="btn-icon" />
            <span className="btn-label">
              {isBatchEvalRunning ? 'Batch Eval Running...' : 'Batch Eval'}
            </span>
          </button>

          <button
            className="btn btn-secondary developer-tool-btn"
            onClick={onInputToAgent}
            disabled={isRunning || serviceInactive}
            type="button"
          >
            <IconUpload className="btn-icon" />
            <span className="btn-label">Input To Agent</span>
          </button>

          <button
            className="btn btn-secondary developer-tool-btn"
            onClick={onShowTestFiles}
            disabled={serviceInactive || isRunning}
            type="button"
          >
            <IconFile className="btn-icon" />
            <span className="btn-label">Browse Test Files</span>
          </button>
        </div>
        <div className="developer-log-area">
          <LogPanel logs={logs} embedded />
        </div>
      </div>
    </section>
  );
}
