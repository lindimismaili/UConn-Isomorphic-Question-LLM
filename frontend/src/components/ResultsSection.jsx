import { useEffect, useRef, useState } from 'react';
import {
  IconCheckCircle,
  IconFolder,
  IconDownload,
  IconMonitor,
  IconList,
} from './Icons.jsx';
import PdfPreviewModal from './modals/PdfPreviewModal.jsx';
import EvalResultsModal from './modals/EvalResultsModal.jsx';
import TexViewerModal from './modals/TexViewerModal.jsx';
import CsvViewerModal from './modals/CsvViewerModal.jsx';
import AgentMetadataModal from './modals/AgentMetadataModal.jsx';
import AgentArtifactModal from './modals/AgentArtifactModal.jsx';
import EvalDatasetModal from './modals/EvalDatasetModal.jsx';
import FinalResultModal from './modals/FinalResultModal.jsx';

/** Preferred ordering for artifact groups in the download list. */
const AGENT_ORDER = [
  'normalizer',
  'extracter',
  'verifier',
  'generator',
  'latexifier',
  'evaluation',
  'output',
];

/** Human-readable labels for artifact group headings. */
const AGENT_LABELS = {
  normalizer: 'Normalizer',
  extracter: 'Extracter',
  verifier: 'Verifier',
  generator: 'Generator',
  latexifier: 'Latexifier',
  evaluation: 'Evaluator',
  output: 'Output',
};

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

/**
 * Merges the workflow file list with evaluation and artifact files.
 *
 * @param {object} result - Final workflow result payload.
 * @returns {Array<object>} Deduplicated download entries.
 */
function collectResultFiles(result) {
  const mergedFiles = [];
  const seen = new Set();

  const addFile = (file) => {
    const fileName = typeof file === 'string' ? file : file?.name || file;
    if (!fileName || seen.has(fileName)) return;
    seen.add(fileName);
    mergedFiles.push(typeof file === 'string' ? { name: fileName } : { ...file, name: fileName });
  };

  (result.files || []).forEach(addFile);

  const workflowResult = result.workflowResult || {};
  const evaluation = workflowResult.evaluation || {};
  const hasEvaluationReport = Boolean(workflowResult.evaluation);
  [
    workflowResult.latex_file,
    workflowResult.pdf_file,
    evaluation.output_items_file,
    evaluation.dataset_file,
    evaluation.spreadsheet_file,
    evaluation.rubric_file,
    hasEvaluationReport ? 'evaluation_outputs/evaluation_report.json' : '',
  ].forEach(addFile);

  return mergedFiles;
}

function baseName(fileName) {
  return typeof fileName === 'string' ? fileName.split('/').pop() || fileName : '';
}

function isPdfFile(fileName) {
  return typeof fileName === 'string' && fileName.toLowerCase().endsWith('.pdf');
}

function isTexFile(fileName) {
  return typeof fileName === 'string' && fileName.toLowerCase().endsWith('.tex');
}

/**
 * Returns the viewer modal type for a given file, or null if no viewer exists.
 *
 * @param {string} fileName
 * @returns {string | null}
 */
function getViewerType(fileName) {
  if (!fileName) return null;
  const base = baseName(fileName);
  if (base === 'final_result.json') return 'final-result';
  if (base === 'evaluation_spreadsheet.csv') return 'csv';
  if (isTexFile(fileName)) return 'tex';
  if (base === 'agent_metadata_output.json') return 'agent-metadata';
  if (/^evaluation_outputs\/evaluation_dataset\.jsonl$/.test(fileName)) return 'eval-dataset';
  if (/^evaluation_outputs\/evaluation_agents\.jsonl$/.test(fileName)) return 'eval-results';
  if (/^agent_outputs\/[^/]+\/agent_(input|output)\.jsonl$/i.test(fileName)) return 'agent-artifact';
  return null;
}

function findFileName(files, preferredName) {
  const preferredBaseName = baseName(preferredName);
  return (
    files.find(
      (file) => file.name === preferredName || baseName(file.name) === preferredBaseName,
    )?.name || ''
  );
}

function formatAgentLabel(agentKey) {
  return AGENT_LABELS[agentKey] || agentKey.replace(/_/g, ' ');
}

function parseAgentArtifact(fileName) {
  const match = /^agent_outputs\/([^/]+)\/(agent_input|agent_output)\.jsonl$/.exec(fileName);
  if (!match) return null;
  return {
    agentKey: match[1],
    kind: match[2] === 'agent_input' ? 'input' : 'output',
  };
}

function buildAgentOutputSections(agentFiles) {
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
        label: formatAgentLabel(parsed.agentKey),
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
    const aIndex = AGENT_ORDER.indexOf(a.key);
    const bIndex = AGENT_ORDER.indexOf(b.key);
    const normalizedAIndex = aIndex === -1 ? AGENT_ORDER.length : aIndex;
    const normalizedBIndex = bIndex === -1 ? AGENT_ORDER.length : bIndex;
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
  const previewCloseTimerRef = useRef(null);
  const [previewFile, setPreviewFile] = useState('');
  const [isPdfPreviewOpen, setIsPdfPreviewOpen] = useState(false);
  const [evalModalOpen, setEvalModalOpen] = useState(false);
  const [viewerFile, setViewerFile] = useState('');
  const [viewerType, setViewerType] = useState(null);

  useEffect(() => {
    if (result && sectionRef.current) {
      sectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [result]);

  useEffect(() => () => {
    clearTimeout(previewCloseTimerRef.current);
  }, []);

  if (!result) return null;

  const files = collectResultFiles(result);
  const { mainFiles, evaluationFiles, agentFiles } = splitDownloadFiles(files);
  const { metadataFiles, agentGroups } = buildAgentOutputSections(agentFiles);
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

  const getFileUrl = (fileName, inline = false) => {
    if (!result.workflowId) return '';
    const encodedFileName = fileName.split('/').map(encodeURIComponent).join('/');
    const inlineQuery = inline ? '?inline=1' : '';
    return `/api/download/${result.workflowId}/${encodedFileName}${inlineQuery}`;
  };

  const handleDownload = async (fileName) => {
    try {
      const url = getFileUrl(fileName);
      if (!url) {
        throw new Error('Workflow download link is unavailable.');
      }
      const link = document.createElement('a');
      link.href = url;
      link.download = baseName(fileName);
      link.click();
    } catch (err) {
      alert(`Download failed: ${err.message}`);
    }
  };

  const handlePreviewOpen = (fileName) => {
    clearTimeout(previewCloseTimerRef.current);
    setPreviewFile(fileName);
    setIsPdfPreviewOpen(true);
  };

  const handlePreviewClose = () => {
    setIsPdfPreviewOpen(false);
    clearTimeout(previewCloseTimerRef.current);
    previewCloseTimerRef.current = setTimeout(() => setPreviewFile(''), 260);
  };

  const handleEvalModalOpen = () => {
    setEvalModalOpen(true);
  };

  const handleEvalModalClose = () => {
    setEvalModalOpen(false);
  };

  const handleViewOpen = (fileName) => {
    const type = getViewerType(fileName);
    if (type === 'eval-results') {
      setEvalModalOpen(true);
      return;
    }
    if (type) {
      setViewerFile(fileName);
      setViewerType(type);
    }
  };

  const handleViewClose = () => {
    setViewerFile('');
    setViewerType(null);
  };

  const previewUrl = previewFile ? getFileUrl(previewFile, true) : '';
  const evalItemsUrl = evalItemsFileName ? getFileUrl(evalItemsFileName, true) : '';
  const viewerUrl = viewerFile ? getFileUrl(viewerFile, true) : '';

  const renderFileRow = (file) => {
    const fileName = file.name;
    const displayName = baseName(fileName);
    const pdfFile = isPdfFile(fileName);
    const evalReportFile = fileName === evalReportFileName;
    const viewerTypeForFile = !pdfFile && !evalReportFile ? getViewerType(fileName) : null;
    return (
      <div key={fileName} className="download-item">
        <span className="download-item-name" title={fileName}>
          {displayName}
        </span>
        <div className="download-item-actions">
          {pdfFile && (
            <button
              className="btn btn-primary btn-preview"
              onClick={() => handlePreviewOpen(fileName)}
              type="button"
            >
              <IconMonitor className="btn-icon" />
              View
            </button>
          )}
          {evalReportFile && hasEvaluation && (
            <button
              className="btn btn-primary btn-eval-results"
              onClick={handleEvalModalOpen}
              type="button"
            >
              <IconList className="btn-icon" />
              View
            </button>
          )}
          {viewerTypeForFile && (
            <button
              className="btn btn-primary btn-preview"
              onClick={() => handleViewOpen(fileName)}
              type="button"
            >
              <IconMonitor className="btn-icon" />
              View
            </button>
          )}
          <button
            className="btn btn-secondary btn-download"
            onClick={() => handleDownload(fileName)}
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
          className="btn btn-secondary btn-sm agent-output-view"
          onClick={() => handleViewOpen(fileName)}
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
        onClick={() => handleDownload(fileName)}
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

      <PdfPreviewModal
        isOpen={isPdfPreviewOpen}
        onClose={handlePreviewClose}
        fileName={previewFile}
        pdfUrl={previewUrl}
        onDownload={handleDownload}
      />
      <EvalResultsModal
        isOpen={evalModalOpen}
        onClose={handleEvalModalClose}
        report={evaluationReport}
        itemsUrl={evalItemsUrl}
        itemsFileName={evalItemsFileName}
        reportFileName={evalReportFileName}
        onDownload={handleDownload}
      />
      <TexViewerModal
        isOpen={viewerType === 'tex'}
        onClose={handleViewClose}
        fileName={viewerFile}
        url={viewerUrl}
        onDownload={handleDownload}
      />
      <CsvViewerModal
        isOpen={viewerType === 'csv'}
        onClose={handleViewClose}
        fileName={viewerFile}
        url={viewerUrl}
        onDownload={handleDownload}
      />
      <AgentMetadataModal
        isOpen={viewerType === 'agent-metadata'}
        onClose={handleViewClose}
        fileName={viewerFile}
        url={viewerUrl}
        onDownload={handleDownload}
      />
      <AgentArtifactModal
        isOpen={viewerType === 'agent-artifact'}
        onClose={handleViewClose}
        fileName={viewerFile}
        url={viewerUrl}
        onDownload={handleDownload}
      />
      <EvalDatasetModal
        isOpen={viewerType === 'eval-dataset'}
        onClose={handleViewClose}
        fileName={viewerFile}
        url={viewerUrl}
        onDownload={handleDownload}
      />
      <FinalResultModal
        isOpen={viewerType === 'final-result'}
        onClose={handleViewClose}
        fileName={viewerFile}
        url={viewerUrl}
        onDownload={handleDownload}
      />
    </>
  );
}
