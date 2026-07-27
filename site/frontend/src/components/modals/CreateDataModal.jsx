import { useState } from "react";
import Modal from "./Modal.jsx";
import { IconFile, IconFolder, IconZap } from "../Icons.jsx";
import { useAgentRegistry } from "../../hooks/useAgentRegistry";
import { Badge, Button, Pill, ToggleSwitch } from "../ui/index.js";
import { downloadApiFile } from "../../utils/api.js";

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
    case "blocked":
      return "Blocked";
    case "stopping":
      return "Stopping";
    case "starting":
      return "Starting";
    case "start_uncertain":
      return "Recovery needed";
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
  serviceInactive = false,
  maxWorkflows = 5,
}) {
  const [costConfirmed, setCostConfirmed] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
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
  const isRecoveringResult = Boolean(createData.isLoadingResult);
  const isTerminal = ["completed", "errored", "cancelled", "blocked"].includes(
    createData.status,
  );
  const progressText =
    createData.totalFiles > 0
      ? `${createData.doneFiles}/${createData.totalFiles} files done`
      : "Preparing evaluation files";
  const outputFolder = formatOutputFolder(createData.outputFolder);
  const handleRun = () => {
    if (!costConfirmed) return;
    onRun?.({ confirmWorkflowCosts: true });
    setCostConfirmed(false);
  };
  const handleDownload = async () => {
    if (!createData.batchId) return;
    setDownloading(true);
    setDownloadError("");
    try {
      const basename = createData.outputFolder?.split("/").pop() || "create-data";
      await downloadApiFile(
        `/api/download-zip/${encodeURIComponent(createData.batchId)}`,
        `${basename}.zip`,
      );
    } catch (error) {
      setDownloadError(error.message || "Batch download failed.");
    } finally {
      setDownloading(false);
    }
  };

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
            onClick={isActive ? onStop : handleRun}
            disabled={
              createData.status === "stopping" ||
              isRecoveringResult ||
              (!isActive && (serviceInactive || !costConfirmed))
            }
          >
            {isActive
              ? "Stop"
              : isRecoveringResult
                ? "Loading Final Result…"
              : createData.status === "start_uncertain"
                ? "Recover Exact Start"
                : "Start"}
          </Button>
          {isTerminal && createData.batchId && createData.outputFolder ? (
            <Button
              variant="secondary"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading ? "Preparing download…" : "Download Batch ZIP"}
            </Button>
          ) : null}
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
                <div className="create-data-heading">Batch generation run</div>
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
              {createData.blockedFiles > 0
                ? `, ${createData.blockedFiles} blocked`
                : ""}
            </span>
          </div>
        </div>

        {createData.error && (
          <div className="create-data-alert" role="alert">{createData.error}</div>
        )}
        {downloadError && (
          <div className="create-data-alert" role="alert">{downloadError}</div>
        )}

        {!isActive && (
          <>
            <div className="batch-eval-hint">
              This can start up to {maxWorkflows} complete generation workflows.
              Rubric evaluation is not included; use Batch Evaluation separately.
            </div>
            <ToggleSwitch
              checked={costConfirmed}
              onChange={setCostConfirmed}
              disabled={isRecoveringResult}
              label={`I approve up to ${maxWorkflows} full generation workflows`}
            />
          </>
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
                    <Badge kind="step-chip">
                      {`${entry.stepLabel || fallbackStepLabel} ${entry.currentAgentName || fallbackAgentName}`.trim()}
                    </Badge>
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
