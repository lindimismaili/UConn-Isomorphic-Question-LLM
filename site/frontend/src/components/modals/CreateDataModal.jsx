import Modal from "./Modal.jsx";
import CompareProfessorPanel from "./CompareProfessorPanel.jsx";
import { IconFile, IconFolder, IconZap } from "../Icons.jsx";
import { useAgentRegistry } from "../../hooks/useAgentRegistry";
import { Button, Pill } from "../ui/index.js";

/**
 * Normalizes the batch output folder label for display.
 *
 * @param {string} folder
 * @returns {string}
 */
function formatOutputFolder(folder) {
  if (!folder) return "outputs/";
  return folder.startsWith("outputs/") ? folder : `outputs/${folder}`;
}

/**
 * Converts backend batch states into short user-facing labels.
 *
 * @param {string} status
 * @returns {string}
 */
function getStatusLabel(status) {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "completed":
      return "Done";
    case "errored":
      return "Error";
    case "cancelled":
      return "Stopped";
    case "stopping":
      return "Stopping";
    case "starting":
      return "Starting";
    default:
      return "Idle";
  }
}

/**
 * Displays the batch-evaluation run state and progress per file.
 *
 * @param {object} props
 */
export default function CreateDataModal({
  isOpen,
  onClose,
  createData,
  onRun,
  onStop,
}) {
  const registryData = useAgentRegistry();
  // Fallback chip shown before the backend reports a step: first pipeline
  // stage out of the full agents.json sequence.
  const fallbackStepLabel = registryData
    ? `1/${registryData.pipelineKeys.length}`
    : "";
  const fallbackAgentName = registryData
    ? registryData.agentLabels[registryData.pipelineKeys[0]]
    : "";
  const isActive = createData.isStarting || createData.isRunning;
  const progressText =
    createData.totalFiles > 0
      ? `${createData.doneFiles}/${createData.totalFiles} files done`
      : "Preparing evaluation files";
  const outputFolder = formatOutputFolder(createData.outputFolder);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create Data"
      large
      footer={
        <div className="create-data-footer-actions">
          <Button
            variant={isActive ? "danger" : "primary"}
            onClick={isActive ? onStop : onRun}
            disabled={createData.status === "stopping"}
          >
            {isActive ? "Stop" : "Start"}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      <div className="create-data-modal">
        <div className="create-data-summary">
          <div className="create-data-summary-title">
            <IconZap />
            <div>
              <div className="create-data-heading">Batch evaluation run</div>
              <div className="create-data-subheading">
                Runs the full workflow on every file in{" "}
                <code>data/evaluation</code>.
              </div>
            </div>
          </div>
          <div className="create-data-progress">
            <Pill state={createData.status}>
              {getStatusLabel(createData.status)}
            </Pill>
            <strong>{progressText}</strong>
            <span>
              {createData.completedFiles} completed
              {createData.failedFiles > 0
                ? `, ${createData.failedFiles} failed`
                : ""}
              {createData.cancelledFiles > 0
                ? `, ${createData.cancelledFiles} stopped`
                : ""}
            </span>
          </div>
        </div>

        {createData.error && (
          <div className="create-data-alert">{createData.error}</div>
        )}

        {["completed", "errored", "cancelled"].includes(createData.status) &&
          createData.outputFolder && (
            <CompareProfessorPanel outputFolder={createData.outputFolder} />
          )}

        <div className="output-folder-note">
          <IconFolder />
          <span>
            Batch folder:{" "}
            <code>
              {createData.outputFolder ? outputFolder : "outputs/"}
            </code>
          </span>
        </div>

        <div className="create-data-list">
          {createData.entries.map((entry) => (
            <div
              key={`${entry.relativePath}-${entry.folderName}`}
              className={`create-data-item is-${entry.status}`}
            >
              <div className="create-data-item-main">
                <div className="create-data-item-header">
                  <div className="create-data-item-title">
                    <IconFile />
                    <span>{entry.name}</span>
                  </div>
                  <div className="create-data-item-tracking">
                    <span className="create-data-step-chip">
                      {`${entry.stepLabel || fallbackStepLabel} ${entry.currentAgentName || fallbackAgentName}`.trim()}
                    </span>
                    <Pill state={entry.status}>
                      {getStatusLabel(entry.status)}
                    </Pill>
                  </div>
                </div>
                <div className="create-data-item-meta">
                  <span>{`data/evaluation/${entry.relativePath}`}</span>
                  <span>{`folder: ${entry.folderName}`}</span>
                </div>
                <div className="create-data-item-output">
                  {entry.status === "completed" && entry.outputFolder
                    ? `outputs/${entry.outputFolder}`
                    : "outputs/"}
                </div>
                {entry.error ? (
                  <div className="create-data-item-error">{entry.error}</div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
