import { useEffect, useRef, useState } from 'react';
import Modal from './Modal.jsx';
import CompareProfessorPanel from './CompareProfessorPanel.jsx';
import { IconZap, IconUpload, IconFile } from '../Icons.jsx';
import {
  Badge,
  Button,
  Checkbox,
  Chip,
  Pill,
  Tabs,
  ToggleSwitch,
} from '../ui/index.js';
import { downloadApiFile } from '../../utils/api.js';

const UPLOAD_SAFETY_CHECKS = 'moderation and content-classifier checks also run for every extracted PDF; their exact call count varies.';
const UPLOAD_SAFETY_DISCLOSURE = `Safety ${UPLOAD_SAFETY_CHECKS}`;
const UPLOAD_SAFETY_ACKNOWLEDGEMENT = `I understand that safety ${UPLOAD_SAFETY_CHECKS}`;

function ScoreBadge({ score }) {
  if (score == null) return null;
  const pct = Math.round(score * 100);
  const tone = pct >= 70 ? 'success' : pct >= 50 ? 'warning' : 'error';
  return <Chip tone={tone}>{pct}%</Chip>;
}

function statusLabel(s) {
  return {
    queued: 'Queued',
    running: 'Running',
    completed: 'Done',
    errored: 'Error',
    cancelled: 'Stopped',
    blocked: 'Blocked',
    stopping: 'Stopping',
    starting: 'Starting',
    start_uncertain: 'Recovery needed',
  }[s] || 'Idle';
}

// Prior Runs tab
function PriorRunsTab({
  availableRuns,
  loadingRuns,
  runsError,
  selected,
  onToggle,
  maxSelect,
}) {
  return (
    <div className="batch-eval-run-list">
      {loadingRuns && <div className="batch-eval-hint">Loading runs&hellip;</div>}
      {!loadingRuns && runsError && (
        <div className="create-data-alert" role="alert">{runsError}</div>
      )}
      {!loadingRuns && !runsError && availableRuns.length === 0 && (
        <div className="batch-eval-hint">No completed runs found in <code>outputs/</code>.</div>
      )}
      {availableRuns.map(run => {
        const checked = selected.has(run.folder);
        const disabled = !checked && selected.size >= maxSelect;
        return (
          <label key={run.folder} className={`batch-eval-run-row ${disabled ? 'is-disabled' : ''} ${checked ? 'is-checked' : ''}`}>
            <Checkbox
              checked={checked}
              disabled={disabled}
              onChange={() => onToggle(run.folder)}
              aria-label={`Select ${run.label}`}
            />
            <div className="batch-eval-run-info">
              <span className="batch-eval-run-name">{run.label}</span>
              <span className="batch-eval-run-meta">{run.questionCount} questions{run.hasEval ? ' · already evaluated' : ''}</span>
            </div>
            {run.hasEval && <ScoreBadge score={run.existingScore} />}
          </label>
        );
      })}
    </div>
  );
}

// Upload tab
function UploadTab({ sourceFile, generatedFiles, onSourceChange, onGeneratedChange, maxGenerated }) {
  const srcRef = useRef(null);
  const genRef = useRef(null);
  return (
    <div className="batch-eval-upload">
      <Button
        variant={null}
        className="batch-eval-upload-zone"
        onClick={() => srcRef.current?.click()}
        aria-label="Select source PDF"
      >
        <IconUpload />
        <span>{sourceFile ? sourceFile.name : 'Click to select source PDF'}</span>
      </Button>
      <input ref={srcRef} type="file" accept=".pdf" style={{ display: 'none' }} onChange={e => onSourceChange(e.target.files[0] || null)} />
      <Button
        variant={null}
        className="batch-eval-upload-zone batch-eval-upload-zone-multi"
        onClick={() => genRef.current?.click()}
        aria-label={`Select up to ${maxGenerated} generated PDFs`}
      >
        <IconFile />
        <span>{generatedFiles.length > 0 ? `${generatedFiles.length} generated PDF(s) selected` : `Click to select generated PDFs (up to ${maxGenerated})`}</span>
      </Button>
      <input ref={genRef} type="file" accept=".pdf" multiple style={{ display: 'none' }} onChange={e => onGeneratedChange(Array.from(e.target.files || []).slice(0, maxGenerated))} />
      {generatedFiles.length > 0 && (
        <ul className="batch-eval-file-list">
          {generatedFiles.map((f, i) => <li key={i}><IconFile />{f.name}</li>)}
        </ul>
      )}
    </div>
  );
}

// Entry progress row
function EntryRow({ entry }) {
  return (
    <div className={`create-data-item is-${entry.status}`}>
      <div className="create-data-item-main">
        <div className="create-data-item-header">
          <div className="create-data-item-title"><IconFile /><span>{entry.name}</span></div>
          <div className="create-data-item-tracking">
            {entry.itemsEvaluated > 0 && <Badge kind="step-chip">{entry.itemsEvaluated} items</Badge>}
            <ScoreBadge score={entry.score} />
            <Pill state={entry.status}>{statusLabel(entry.status)}</Pill>
          </div>
        </div>
        {entry.outputFolder && <div className="create-data-item-output">{`outputs/${entry.outputFolder}`}</div>}
        {entry.error && <div className="create-data-item-error">{entry.error}</div>}
      </div>
    </div>
  );
}

export default function BatchEvalModal({
  isOpen,
  onClose,
  batchEval,
  loadRuns,
  serviceInactive = false,
}) {
  const [tab, setTab] = useState('prior_runs');
  const [selectedFolders, setSelectedFolders] = useState(new Set());
  const [sourceFile, setSourceFile] = useState(null);
  const [generatedFiles, setGeneratedFiles] = useState([]);
  const [costConfirmed, setCostConfirmed] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const policy = batchEval.policy || {
    maxEntries: 5,
    maxItems: 100,
    graderCount: 8,
    graderMaxCompletionTokens: 4096,
    maxUploadExtractionFiles: 6,
    maxUploadExtractionCalls: 12,
    maxUploadBootstrapCalls: 6,
  };
  const maxSelect = Number(policy.maxEntries || 5);
  const graderCount = Number(policy.graderCount || 8);
  const graderMaxCompletionTokens = Number(
    policy.graderMaxCompletionTokens || 4096,
  );
  const maxItems = Number(policy.maxItems || 100);
  const maxUploadExtractionFiles = Number(
    policy.maxUploadExtractionFiles ?? (maxSelect + 1),
  );
  const maxUploadExtractionCalls = Number(
    policy.maxUploadExtractionCalls ?? (maxUploadExtractionFiles * 2),
  );
  const maxUploadBootstrapCalls = Number(
    policy.maxUploadBootstrapCalls ?? maxUploadExtractionFiles,
  );
  const maxGenerated = Math.max(
    0,
    Math.min(
      maxSelect,
      maxUploadExtractionFiles - 1,
      Math.floor(maxUploadExtractionCalls / 2) - 1,
      maxUploadBootstrapCalls - 1,
    ),
  );
  const selectedItems = Array.from(selectedFolders).reduce((total, folder) => {
    const run = batchEval.availableRuns.find(candidate => candidate.folder === folder);
    return total + Number(run?.questionCount || 0);
  }, 0);
  const projectedItems = tab === 'prior_runs'
    ? selectedItems
    : maxItems;
  const projectedGraderCalls = projectedItems * graderCount;
  const projectedExtractionFiles = tab === 'upload'
    ? Number(Boolean(sourceFile)) + generatedFiles.length
    : 0;
  const projectedExtractionCalls = projectedExtractionFiles * 2;
  const projectedBootstrapCalls = projectedExtractionFiles;
  const overItemBudget = projectedItems > maxItems;
  const overExtractionBudget = projectedExtractionFiles > maxUploadExtractionFiles
    || projectedExtractionCalls > maxUploadExtractionCalls
    || projectedBootstrapCalls > maxUploadBootstrapCalls;
  const overBudget = overItemBudget || overExtractionBudget;

  const isActive = batchEval.isStarting || batchEval.isRunning;
  const isRecoveringResult = Boolean(batchEval.isLoadingResult);
  const isIdle = batchEval.status === 'idle' && !isActive;
  const isStartUncertain = batchEval.status === 'start_uncertain';

  // Load prior runs once per open. Keying the previous effect on
  // `availableRuns.length === 0` re-fired forever when the folder was genuinely
  // empty (load → empty → not loading → load again), flashing the
  // "Loading…"/"No runs found" hints. The ref fires exactly one load per open
  // and resets when the modal closes so reopening refreshes the list.
  const loadTriggeredRef = useRef(false);
  useEffect(() => {
    if (!isOpen) {
      loadTriggeredRef.current = false;
      return;
    }
    if (tab === 'prior_runs' && !loadTriggeredRef.current && !batchEval.loadingRuns) {
      loadTriggeredRef.current = true;
      loadRuns();
    }
  }, [isOpen, tab, loadRuns, batchEval.loadingRuns]);

  const handleToggle = (folder) => {
    setCostConfirmed(false);
    setSelectedFolders(prev => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder); else next.add(folder);
      return next;
    });
  };

  const handleStart = () => {
    if (tab === 'prior_runs') {
      batchEval.start({
        mode: 'prior_runs',
        selectedFolders: Array.from(selectedFolders),
        confirmGraderCalls: costConfirmed,
      });
    } else {
      batchEval.start({
        mode: 'upload',
        sourceFile,
        generatedFiles,
        confirmGraderCalls: costConfirmed,
      });
    }
  };

  const hasInputs = tab === 'prior_runs'
    ? selectedFolders.size > 0
    : (sourceFile && generatedFiles.length > 0);
  const canStart = hasInputs && costConfirmed && !overBudget;

  const showProgress = batchEval.status !== 'idle' && batchEval.entries.length > 0;
  const isTerminal = ['completed', 'errored', 'cancelled', 'blocked'].includes(
    batchEval.status,
  );
  const reportedGraderCalls = Number(batchEval.projectedGraderCalls || 0);
  const reportedExtractionFiles = Number(batchEval.projectedExtractionFiles || 0);
  const reportedExtractionCalls = Number(batchEval.projectedExtractionCalls || 0);
  const reportedBootstrapCalls = Number(batchEval.projectedBootstrapCalls || 0);
  const showReportedProjection = batchEval.status !== 'idle'
    && reportedGraderCalls > 0;

  const progressText = batchEval.totalFiles > 0
    ? `${batchEval.doneFiles}/${batchEval.totalFiles} done`
    : 'Preparing…';

  const handleDownload = async () => {
    if (!batchEval.batchId) return;
    setDownloading(true);
    setDownloadError('');
    try {
      const basename = batchEval.outputFolder?.split('/').pop() || 'batch-evaluation';
      await downloadApiFile(
        `/api/download-zip/${encodeURIComponent(batchEval.batchId)}`,
        `${basename}.zip`,
      );
    } catch (error) {
      setDownloadError(error.message || 'Batch download failed.');
    } finally {
      setDownloading(false);
    }
  };

  const handleReset = () => {
    if (!batchEval.reset?.()) return;
    setCostConfirmed(false);
    setSelectedFolders(new Set());
    setSourceFile(null);
    setGeneratedFiles([]);
    setDownloadError('');
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Batch Evaluation" large
      footer={
        <div className="create-data-footer-actions">
          {isIdle && (
            <Button
              variant="primary"
              onClick={handleStart}
              disabled={!canStart || serviceInactive}
              icon={<IconZap className="btn-icon" />}
            >
              Run Batch Eval
            </Button>
          )}
          {isStartUncertain && (
            <Button
              variant="primary"
              onClick={handleStart}
              disabled={!canStart || serviceInactive}
            >
              Recover Exact Start
            </Button>
          )}
          {isActive && (
            <Button variant="danger" onClick={batchEval.stop} disabled={batchEval.status === 'stopping'}>
              Stop
            </Button>
          )}
          {isTerminal && batchEval.batchId && batchEval.outputFolder && (
            <Button
              variant="secondary"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading ? 'Preparing download…' : 'Download Batch ZIP'}
            </Button>
          )}
          {isTerminal && (
            <Button
              variant="secondary"
              onClick={handleReset}
              disabled={isRecoveringResult}
            >
              {isRecoveringResult ? 'Loading Final Result…' : 'New Batch Evaluation'}
            </Button>
          )}
          <Button variant="secondary" onClick={onClose}>Close</Button>
        </div>
      }
    >
      <div className="create-data-modal">
        {/* Tabs — only show when idle or no run in progress */}
        {isIdle && (
          <Tabs
            className="batch-eval-tabs"
            value={tab}
            onChange={(nextTab) => {
              setTab(nextTab);
              setCostConfirmed(false);
            }}
            tabs={[
              { id: 'prior_runs', label: 'Prior Runs' },
              { id: 'upload', label: 'Upload PDFs' },
            ]}
          />
        )}

        {/* Active mode indicator while running */}
        {isActive && (
          <div className="create-data-summary">
            <div className="create-data-summary-title">
              <IconZap />
              <div>
                <div className="create-data-heading">Batch Evaluation Running</div>
                <div className="create-data-subheading">
                  {batchEval.mode === 'upload' ? 'Evaluating uploaded PDFs' : 'Evaluating selected runs'}
                </div>
              </div>
            </div>
            <div className="create-data-progress">
              <Pill state={batchEval.status}>{statusLabel(batchEval.status)}</Pill>
              <strong>{progressText}</strong>
              <span>
                {batchEval.completedFiles} completed
                {batchEval.failedFiles > 0 ? `, ${batchEval.failedFiles} failed` : ''}
                {batchEval.cancelledFiles > 0 ? `, ${batchEval.cancelledFiles} stopped` : ''}
                {batchEval.blockedFiles > 0 ? `, ${batchEval.blockedFiles} blocked` : ''}
              </span>
            </div>
          </div>
        )}

        {/* Completed summary */}
        {['completed', 'errored', 'cancelled', 'blocked'].includes(batchEval.status) && (
          <div className="create-data-summary">
            <div className="create-data-summary-title">
              <IconZap />
              <div>
                <div className="create-data-heading">Batch Evaluation Finished</div>
                <div className="create-data-subheading">
                  {batchEval.completedFiles}/{batchEval.totalFiles} evals completed
                  {batchEval.outputFolder ? ` · outputs/${batchEval.outputFolder}` : ''}
                </div>
              </div>
            </div>
            <CompareProfessorPanel outputFolder={batchEval.outputFolder} />
          </div>
        )}

        {showReportedProjection && (
          <div className="batch-eval-hint">
            {batchEval.mode === 'upload'
              ? `Server projection: up to ${reportedGraderCalls} grader calls; ${reportedExtractionFiles} isolated PDFs; up to ${reportedExtractionCalls} extraction agent calls; ${reportedBootstrapCalls} document-dependency bootstrap calls. ${UPLOAD_SAFETY_DISCLOSURE}`
              : `Server projection: up to ${reportedGraderCalls} grader calls.`}
            {' '}
            Each grader call is capped at {graderMaxCompletionTokens} generated tokens.
          </div>
        )}

        {batchEval.error && (
          <div className="create-data-alert" role="alert">{batchEval.error}</div>
        )}
        {downloadError && (
          <div className="create-data-alert" role="alert">{downloadError}</div>
        )}

        {/* Tab content — only when idle */}
        {batchEval.status === 'idle' && tab === 'prior_runs' && (
          <>
            <div className="batch-eval-hint">Select up to {maxSelect} prior runs to evaluate. {selectedFolders.size > 0 && `(${selectedFolders.size} selected)`}</div>
            <PriorRunsTab
              availableRuns={batchEval.availableRuns}
              loadingRuns={batchEval.loadingRuns}
              runsError={batchEval.runsError}
              selected={selectedFolders}
              onToggle={handleToggle}
              maxSelect={maxSelect}
            />
          </>
        )}
        {batchEval.status === 'idle' && tab === 'upload' && (
          <>
            <div className="batch-eval-hint">Upload a source PDF and up to {maxGenerated} generated PDFs to evaluate.</div>
            <UploadTab
              sourceFile={sourceFile}
              generatedFiles={generatedFiles}
              onSourceChange={(file) => {
                setCostConfirmed(false);
                setSourceFile(file);
              }}
              onGeneratedChange={(files) => {
                setCostConfirmed(false);
                setGeneratedFiles(files);
              }}
              maxGenerated={maxGenerated}
            />
          </>
        )}

        {batchEval.status === 'idle' && hasInputs && (
          <>
            <div className="batch-eval-hint">
              {overBudget
                ? (
                  overItemBudget
                    ? `Selection has ${projectedItems} questions, above the ${maxItems}-item batch limit.`
                    : 'The selected PDFs exceed the upload extraction-call policy.'
                )
                : (
                  tab === 'upload'
                    ? `Projected maximum: ${projectedItems} questions × ${graderCount} rubric graders = ${projectedGraderCalls} grader calls; ${projectedExtractionFiles} isolated PDFs require up to ${projectedExtractionCalls} extraction agent calls (one retry per PDF) and ${projectedBootstrapCalls} document-dependency bootstrap calls. ${UPLOAD_SAFETY_DISCLOSURE}`
                    : `Projected maximum: ${projectedItems} questions × ${graderCount} rubric graders = ${projectedGraderCalls} grader calls.`
                )}
              {!overBudget && (
                <> Each grader call is capped at {graderMaxCompletionTokens} generated tokens.</>
              )}
            </div>
            <ToggleSwitch
              checked={costConfirmed}
              onChange={setCostConfirmed}
              disabled={overBudget}
              label={
                tab === 'upload'
                  ? `I approve up to ${projectedGraderCalls} grader calls, ${projectedExtractionCalls} extraction agent calls, and ${projectedBootstrapCalls} document-dependency bootstrap calls for ${projectedExtractionFiles} isolated PDFs. ${UPLOAD_SAFETY_ACKNOWLEDGEMENT}`
                  : `I approve up to ${projectedGraderCalls} grader calls for this batch`
              }
            />
          </>
        )}

        {/* Progress entries */}
        {showProgress && (
          <div className="create-data-list">
            {batchEval.entries.map((entry, i) => <EntryRow key={`${entry.folder || entry.name}-${i}`} entry={entry} />)}
          </div>
        )}
      </div>
    </Modal>
  );
}
