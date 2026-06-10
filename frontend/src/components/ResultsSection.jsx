import { useEffect, useRef, useState } from 'react';
import {
  IconCheckCircle,
  IconFolder,
  IconDownload,
  IconMonitor,
  IconList,
  IconBrain,
} from './Icons.jsx';
import EvalResultsModal from './modals/EvalResultsModal.jsx';
import WorkflowAgentsModal from './modals/WorkflowAgentsModal.jsx';
import FinalResultModal from './modals/FinalResultModal.jsx';
import useFileViewer from './modals/useFileViewer.jsx';
import { baseName, collectResultFiles, isPdfFile, isTexFile } from '../utils/files.js';
import { useAgentRegistry } from '../hooks/useAgentRegistry';

const AGENT_METADATA_FILE = 'agent_outputs/agent_metadata_output.json';

/**
 * Normalizes the result output-folder label shown to the user.
 *
 * @param {string} folder
 * @returns {string}
 */
function formatOutputFolderPath(folder) {
  if (!folder) return 'outputs/';
  return folder.startsWith('outputs/') ? folder : `outputs/${folder}`;
}

/**
 * Formats the workflow elapsed time for the summary header.
 *
 * @param {number} seconds
 * @returns {string}
 */
function formatElapsed(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
  return `${seconds.toFixed(2)}s`;
}

function findFileName(files, preferredName) {
  const preferredBaseName = baseName(preferredName);
  return (
    files.find(
      (file) => file.name === preferredName || baseName(file.name) === preferredBaseName,
    )?.name || ''
  );
}

function formatAgentLabel(agentKey, agentLabels) {
  const fallback = agentKey.replace(/_/g, ' ');
  return agentLabels?.[agentKey] || fallback.charAt(0).toUpperCase() + fallback.slice(1);
}

function parseAgentArtifact(fileName) {
  const match = /^agent_outputs\/([^/]+)\/(agent_input|agent_output)\.jsonl$/.exec(fileName);
  if (!match) return null;
  return {
    agentKey: match[1],
    kind: match[2] === 'agent_input' ? 'input' : 'output',
  };
}

function buildAgentOutputSections(agentFiles, registryData) {
  const agentOrder = registryData?.pipelineKeys ?? [];
  const metadataFiles = agentFiles.filter(
    (file) => file.name === 'agent_outputs/agent_metadata_output.json',
  );
  const groupsByAgent = new Map();

  agentFiles.forEach((file) => {
    const parsed = parseAgentArtifact(file.name);
    if (!parsed) return;

    if (!groupsByAgent.has(parsed.agentKey)) {
      groupsByAgent.set(parsed.agentKey, {
        key: parsed.agentKey,
        label: formatAgentLabel(parsed.agentKey, registryData?.agentLabels),
        inputFile: '',
        outputFile: '',
      });
    }

    const group = groupsByAgent.get(parsed.agentKey);
    if (parsed.kind === 'input') {
      group.inputFile = file.name;
    } else {
      group.outputFile = file.name;
    }
  });

  const agentGroups = Array.from(groupsByAgent.values()).sort((a, b) => {
    const aIndex = agentOrder.indexOf(a.key);
    const bIndex = agentOrder.indexOf(b.key);
    const normalizedAIndex = aIndex === -1 ? agentOrder.length : aIndex;
    const normalizedBIndex = bIndex === -1 ? agentOrder.length : bIndex;
    return normalizedAIndex - normalizedBIndex || a.label.localeCompare(b.label);
  });

  return { metadataFiles, agentGroups };
}

/**
 * Splits result files into main downloads, evaluation files, and agent logs.
 *
 * @param {Array<{name: string}>} files
 * @returns {{mainFiles: Array<object>, evaluationFiles: Array<object>, agentFiles: Array<object>}}
 */
function splitDownloadFiles(files) {
  const mainFiles = files
    .filter(
      (file) =>
        !file.name.startsWith('evaluation_outputs/') && !file.name.startsWith('agent_outputs/'),
    )
    .sort((a, b) => {
      const priority = (file) => {
        if (isPdfFile(file.name)) return 0;
        if (isTexFile(file.name)) return 1;
        if (baseName(file.name) === 'final_result.json') return 2;
        return 3;
      };
      return priority(a) - priority(b) || a.name.localeCompare(b.name);
    });
  const evaluationFiles = files.filter((file) => file.name.startsWith('evaluation_outputs/'));
  const agentFiles = files.filter(
    (file) => file.name.startsWith('agent_outputs/'),
  );

  return { mainFiles, evaluationFiles, agentFiles };
}

/**
 * Renders the completed workflow summary and download surface.
 *
 * @param {{result: object}} props
 */
export default function ResultsSection({ result }) {
  const sectionRef = useRef(null);
  const registryData = useAgentRegistry();
  const [evalModalOpen, setEvalModalOpen] = useState(false);
  const [finalResultOpen, setFinalResultOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const {
    openFile,
    downloadFile,
    getFileUrl,
    viewerModals,
  } = useFileViewer(result?.workflowId);

  useEffect(() => {
    if (result && sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [result]);

  if (!result) return null;

  const files = collectResultFiles(result);
  const { mainFiles, evaluationFiles, agentFiles } = splitDownloadFiles(files);
  const { metadataFiles, agentGroups } = buildAgentOutputSections(agentFiles, registryData);
  const folderPath = formatOutputFolderPath(result.outputFolder);
  const fileCount = files.length;
  const elapsedText = formatElapsed(result.elapsedTimeSeconds);
  const evaluationReport = result.workflowResult?.evaluation || null;
  const evalItemsFileName = findFileName(
    files,
    evaluationReport?.output_items_file || 'evaluation_outputs/evaluation_agents.jsonl',
  );
  const evalReportFileName = findFileName(files, 'evaluation_outputs/evaluation_report.json');
  const hasEvaluation = Boolean(evaluationReport || evalItemsFileName || evalReportFileName);
  const finalResultFileName = findFileName(files, 'final_result.json');
  const agentMetadataFileName = findFileName(files, AGENT_METADATA_FILE);

  const handleEvalModalOpen = () => {
    setEvalModalOpen(true);
  };

  const handleEvalModalClose = () => {
    setEvalModalOpen(false);
  };

  const handleViewInDepth = () => {
    setFinalResultOpen(false);
    setEvalModalOpen(true);
  };

  const evalItemsUrl = evalItemsFileName ? getFileUrl(evalItemsFileName, true) : '';
  const finalResultUrl = finalResultFileName ? getFileUrl(finalResultFileName, true) : '';
  const agentMetadataUrl = agentMetadataFileName ? getFileUrl(agentMetadataFileName, true) : '';

  const renderFileRow = (file) => {
    const fileName = file.name;
    const displayName = baseName(fileName);
    return (
      <div key={fileName} className="download-item">
        <span className="download-item-name" title={fileName}>
          {displayName}
        </span>
        <div className="download-item-actions">
          <button
            className="btn btn-primary btn-preview"
            onClick={() => openFile(fileName)}
            type="button"
          >
            <IconMonitor className="btn-icon" />
            View
          </button>
          <button
            className="btn btn-secondary btn-download"
            onClick={() => downloadFile(fileName)}
            type="button"
          >
            <IconDownload className="btn-icon" />
            Download
          </button>
        </div>
      </div>
    );
  };

  const renderAgentArtifactButton = (fileName, label, artifactLabel) => (
    <div className="agent-artifact-btn-group">
      {fileName && (
        <button
          className="btn btn-primary btn-preview agent-output-view"
          onClick={() => openFile(fileName)}
          title={`View ${artifactLabel} artifact`}
          type="button"
        >
          <IconMonitor className="btn-icon" />
          View
        </button>
      )}
      <button
        className="btn btn-secondary btn-download agent-output-download"
        disabled={!fileName}
        onClick={() => downloadFile(fileName)}
        title={fileName || `${artifactLabel} artifact unavailable`}
        type="button"
      >
        <IconDownload className="btn-icon" />
        {label}
      </button>
    </div>
  );

  return (
    <>
      <section className="card completed-card" ref={sectionRef}>
        <div className="card-header">
          <div className="completed-header">
            <div className="completed-header-left">
              <IconCheckCircle className="completed-icon" />
              <h2>Completed!</h2>
              <span className="completed-title-sep" aria-hidden="true" />
              <h4 className="downloads-inline-label">
                Download Files{' '}
                <span className="download-count">
                  {fileCount} file{fileCount !== 1 ? 's' : ''}
                </span>
              </h4>
              <div className="results-view-buttons">
                <button
                  className="btn btn-primary btn-sm"
                  disabled={!finalResultFileName}
                  onClick={() => setFinalResultOpen(true)}
                  type="button"
                >
                  <IconMonitor className="btn-icon" />
                  View Results
                </button>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={!hasEvaluation}
                  onClick={handleEvalModalOpen}
                  type="button"
                >
                  <IconList className="btn-icon" />
                  View Evaluations
                </button>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={!agentMetadataFileName}
                  onClick={() => setAgentsOpen(true)}
                  type="button"
                >
                  <IconBrain className="btn-icon" />
                  View Agents
                </button>
              </div>
            </div>
            <div className="completed-elapsed-time">
              {elapsedText ? `Elapsed Time: ${elapsedText}` : ''}
            </div>
          </div>
        </div>
        <div className="card-body">
          <div className="downloads-section">
            <div className="downloads-columns">
              {[
                { title: 'Main', files: mainFiles, empty: 'No main output files.' },
                {
                  title: 'Evaluator',
                  files: evaluationFiles,
                  empty: 'No evaluator files.',
                },
              ].map((column) => (
                <div className="download-column" key={column.title}>
                  <h5 className="download-column-title">{column.title}</h5>
                  <div className="download-column-list">
                    {column.files.length === 0 && (
                      <div className="download-empty-state">{column.empty}</div>
                    )}
                    {column.files.map(renderFileRow)}
                  </div>
                </div>
              ))}
              <div className="download-column agent-outputs-column">
                <h5 className="download-column-title">Agent Outputs</h5>
                <div className="download-column-list agent-output-list">
                  {metadataFiles.length === 0 && agentGroups.length === 0 && (
                    <div className="download-empty-state">No agent output files.</div>
                  )}
                  {metadataFiles.map(renderFileRow)}
                  {agentGroups.length > 0 && (
                    <div className="agent-output-groups">
                      {agentGroups.map((agent) => (
                        <section className="agent-output-agent-section" key={agent.key}>
                          <h6 className="agent-output-agent-heading">{agent.label}</h6>
                          <div className="agent-output-actions">
                            <div className="agent-output-action-group">
                              <span className="agent-output-kind">Input</span>
                              {renderAgentArtifactButton(agent.inputFile, 'Download', 'Input')}
                            </div>
                            <div className="agent-output-action-group">
                              <span className="agent-output-kind">Output</span>
                              {renderAgentArtifactButton(agent.outputFile, 'Download', 'Output')}
                            </div>
                          </div>
                        </section>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div className="output-folder-note">
              <IconFolder />
              <span>
                Files saved to: <code>{folderPath}</code>
              </span>
            </div>
          </div>
        </div>
      </section>

      {viewerModals}
      <EvalResultsModal
        isOpen={evalModalOpen}
        onClose={handleEvalModalClose}
        report={evaluationReport}
        itemsUrl={evalItemsUrl}
        itemsFileName={evalItemsFileName}
        reportFileName={evalReportFileName}
        onDownload={downloadFile}
      />
      <FinalResultModal
        isOpen={finalResultOpen}
        onClose={() => setFinalResultOpen(false)}
        fileName={finalResultFileName}
        url={finalResultUrl}
        onDownload={downloadFile}
        onViewInDepth={hasEvaluation ? handleViewInDepth : undefined}
      />
      <WorkflowAgentsModal
        isOpen={agentsOpen}
        onClose={() => setAgentsOpen(false)}
        fileName={agentMetadataFileName}
        url={agentMetadataUrl}
        finalResultUrl={finalResultUrl}
        onDownload={downloadFile}
        onViewFile={openFile}
        totalTimeSeconds={result.elapsedTimeSeconds}
        onViewResults={finalResultFileName ? () => setFinalResultOpen(true) : undefined}
        onViewEvaluations={hasEvaluation ? handleEvalModalOpen : undefined}
      />
    </>
  );
}
