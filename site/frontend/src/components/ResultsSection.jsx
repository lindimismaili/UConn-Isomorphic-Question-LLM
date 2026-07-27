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
import { Button, Card, CardBody, CardHeader } from './ui/index.js';
import { baseName, collectResultFiles, isPdfFile, isTexFile } from '../utils/files.js';
import { useAgentRegistry } from '../hooks/useAgentRegistry';
import { downloadApiFile } from '../utils/api.js';

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
export default function ResultsSection({ result, mode = 'user' }) {
  const sectionRef = useRef(null);
  const registryData = useAgentRegistry();
  const allowRawDetails = mode === 'dev';
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

  const files = collectResultFiles(result).filter(
    (file) => allowRawDetails || !file.name.startsWith('agent_outputs/'),
  );
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

  const handleZipDownload = async () => {
    if (!result.workflowId) return;
    try {
      const zipBaseName = baseName(result.outputFolder) || `workflow-${result.workflowId}`;
      await downloadApiFile(
        `/api/download-zip/${encodeURIComponent(result.workflowId)}`,
        `${zipBaseName}.zip`,
      );
    } catch (error) {
      alert(`ZIP download failed: ${error.message}`);
    }
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
          <Button
            variant="primary"
            className="btn-preview"
            onClick={() => openFile(fileName)}
            icon={<IconMonitor className="btn-icon" />}
          >
            View
          </Button>
          <Button
            variant="secondary"
            className="btn-download"
            onClick={() => downloadFile(fileName)}
            icon={<IconDownload className="btn-icon" />}
          >
            Download
          </Button>
        </div>
      </div>
    );
  };

  const renderAgentArtifactButton = (fileName, label, artifactLabel) => (
    <div className="agent-artifact-btn-group">
      {fileName && (
        <Button
          variant="primary"
          className="btn-preview agent-output-view"
          onClick={() => openFile(fileName)}
          title={`View ${artifactLabel} artifact`}
          icon={<IconMonitor className="btn-icon" />}
        >
          View
        </Button>
      )}
      <Button
        variant="secondary"
        className="btn-download agent-output-download"
        disabled={!fileName}
        onClick={() => downloadFile(fileName)}
        title={fileName || `${artifactLabel} artifact unavailable`}
        icon={<IconDownload className="btn-icon" />}
      >
        {label}
      </Button>
    </div>
  );

  return (
    <>
      <Card variant="completed" ref={sectionRef}>
        <CardHeader>
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
                <Button
                  variant="primary"
                  size="sm"
                  disabled={!finalResultFileName}
                  onClick={() => setFinalResultOpen(true)}
                  icon={<IconMonitor className="btn-icon" />}
                >
                  View Results
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={!hasEvaluation}
                  onClick={handleEvalModalOpen}
                  icon={<IconList className="btn-icon" />}
                >
                  View Evaluations
                </Button>
                {allowRawDetails ? (
                  <>
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={!agentMetadataFileName}
                      onClick={() => setAgentsOpen(true)}
                      icon={<IconBrain className="btn-icon" />}
                    >
                      View Agents
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={!result.workflowId}
                      onClick={handleZipDownload}
                      icon={<IconDownload className="btn-icon" />}
                    >
                      Download .ZIP
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
            <div className="completed-elapsed-time">
              {elapsedText ? `Elapsed Time: ${elapsedText}` : ''}
            </div>
          </div>
        </CardHeader>
        <CardBody>
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
              {allowRawDetails ? (
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
              ) : null}
            </div>
            <div className="output-folder-note">
              <IconFolder />
              <span>
                Files saved to: <code>{folderPath}</code>
              </span>
            </div>
          </div>
        </CardBody>
      </Card>

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
      {allowRawDetails ? (
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
      ) : null}
    </>
  );
}
