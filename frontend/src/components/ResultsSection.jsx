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

const AGENT_ORDER = [
  'normalizer',
  'extracter',
  'verifier',
  'generator',
  'latex',
  'evaluation',
  'output',
];

const AGENT_LABELS = {
  normalizer: 'Normalizer',
  extracter: 'Extracter',
  verifier: 'Verifier',
  generator: 'Generator',
  latex: 'LaTeX Formatter',
  evaluation: 'Evaluation',
  output: 'Output',
};

function formatOutputFolderPath(folder) {
  if (!folder) return 'outputs/';
  return folder.startsWith('outputs/') ? folder : `outputs/${folder}`;
}

function formatElapsed(seconds) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
  return `${seconds.toFixed(2)}s`;
}

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

export default function ResultsSection({ result }) {
  const sectionRef = useRef(null);
  const previewCloseTimerRef = useRef(null);
  const [previewFile, setPreviewFile] = useState('');
  const [isPdfPreviewOpen, setIsPdfPreviewOpen] = useState(false);
  const [evalModalOpen, setEvalModalOpen] = useState(false);

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
    evaluationReport?.output_items_file || 'evaluation_outputs/evaluation_items.jsonl',
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

  const previewUrl = previewFile ? getFileUrl(previewFile, true) : '';
  const evalItemsUrl = evalItemsFileName ? getFileUrl(evalItemsFileName, true) : '';

  const renderFileRow = (file) => {
    const fileName = file.name;
    const pdfFile = isPdfFile(fileName);
    const evalReportFile = fileName === evalReportFileName;
    return (
      <div key={fileName} className="download-item">
        <span className="download-item-name" title={fileName}>
          {fileName}
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
  );

  return (
    <>
      <section className="card completed-card" ref={sectionRef}>
        <div className="card-header">
          <div className="completed-header">
            <div className="completed-header-left">
              <IconCheckCircle className="completed-icon" />
              <h2>Completed!</h2>
            </div>
            <div className="completed-elapsed-time">
              {elapsedText ? `Elapsed Time: ${elapsedText}` : ''}
            </div>
          </div>
        </div>
        <div className="card-body">
          <div className="downloads-section">
            <div className="downloads-header">
              <h4>
                Download Files{' '}
                <span className="download-count">
                  {fileCount} file{fileCount !== 1 ? 's' : ''}
                </span>
              </h4>
            </div>
            <div className="downloads-columns">
              {[
                { title: 'Main', files: mainFiles, empty: 'No main output files.' },
                {
                  title: 'Evaluation',
                  files: evaluationFiles,
                  empty: 'No evaluation files.',
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
                          <div className="agent-output-subcolumns">
                            <div className="agent-output-subcolumn">
                              <span className="agent-output-kind">Input</span>
                              {renderAgentArtifactButton(agent.inputFile, 'Download', 'Input')}
                            </div>
                            <div className="agent-output-subcolumn">
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
    </>
  );
}
