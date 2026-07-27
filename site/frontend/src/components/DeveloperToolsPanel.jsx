import { IconFile, IconUpload, IconZap } from './Icons.jsx';
import LogPanel from './LogPanel.jsx';
import { Button, Card, CardBody, CardHeader, ToggleSwitch } from './ui/index.js';

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
  evaluationPolicy = {
    maxItems: 100,
    graderCount: 8,
    graderMaxCompletionTokens: 4096,
  },
  logs,
}) {
  const maximumGraderCalls =
    Number(evaluationPolicy.maxItems || 100) *
    Number(evaluationPolicy.graderCount || 8);
  const graderMaxCompletionTokens = Number(
    evaluationPolicy.graderMaxCompletionTokens || 4096
  );
  return (
    <Card variant="developer-tools">
      <CardHeader
        title="Developer Tools"
        subtitle="Run evaluations, create synthetic test data, feed a specific agent, or browse stored fixtures."
      />

      <CardBody>
        <div className="developer-tools-row">
          <ToggleSwitch
            checked={runEvaluations}
            onChange={onRunEvaluationsChange}
            disabled={isRunning}
            label={`Evaluate the next run only (approve up to ${maximumGraderCalls} grader calls, ${graderMaxCompletionTokens} generated tokens each)`}
          />

          <Button
            variant="secondary"
            className="developer-tool-btn"
            onClick={onCreateData}
            disabled={isRunning || (serviceInactive && !isCreateDataRunning)}
            icon={<IconZap className="btn-icon" />}
          >
            <span className="btn-label">
              {isCreateDataRunning ? 'Test Data Running...' : 'Create Test Data'}
            </span>
          </Button>

          <Button
            variant="secondary"
            className="developer-tool-btn"
            onClick={onBatchEval}
            disabled={isRunning || (serviceInactive && !isBatchEvalRunning)}
            icon={<IconZap className="btn-icon" />}
          >
            <span className="btn-label">
              {isBatchEvalRunning ? 'Batch Eval Running...' : 'Batch Eval'}
            </span>
          </Button>

          <Button
            variant="secondary"
            className="developer-tool-btn"
            onClick={onInputToAgent}
            disabled={isRunning || serviceInactive}
            icon={<IconUpload className="btn-icon" />}
          >
            <span className="btn-label">Input To Agent</span>
          </Button>

          <Button
            variant="secondary"
            className="developer-tool-btn"
            onClick={onShowTestFiles}
            disabled={serviceInactive || isRunning}
            icon={<IconFile className="btn-icon" />}
          >
            <span className="btn-label">Browse Test Files</span>
          </Button>
        </div>
        <div className="developer-log-area">
          <LogPanel logs={logs} embedded />
        </div>
      </CardBody>
    </Card>
  );
}
