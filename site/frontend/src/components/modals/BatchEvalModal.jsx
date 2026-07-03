import { useEffect, useRef, useState } from 'react';
import Modal from './Modal.jsx';
import CompareProfessorPanel from './CompareProfessorPanel.jsx';
import { IconZap, IconUpload, IconFile } from '../Icons.jsx';
import { Badge, Button, Pill } from '../ui/index.js';

function ScoreBadge({ score }) {
  if (score == null) return null;
  const pct = Math.round(score * 100);
  const cls = pct >= 70 ? 'score-badge-pass' : pct >= 50 ? 'score-badge-mid' : 'score-badge-fail';
  return <span className={`score-badge ${cls}`}>{pct}%</span>;
}

function statusLabel(s) {
  return { queued: 'Queued', running: 'Running', completed: 'Done', errored: 'Error', cancelled: 'Stopped', stopping: 'Stopping', starting: 'Starting' }[s] || 'Idle';
}

// Prior Runs tab
function PriorRunsTab({ availableRuns, loadingRuns, selected, onToggle, maxSelect }) {
  return (
    <div className="batch-eval-run-list">
      {loadingRuns && <div className="batch-eval-hint">Loading runs&hellip;</div>}
      {!loadingRuns && availableRuns.length === 0 && (
        <div className="batch-eval-hint">No completed runs found in <code>outputs/</code>.</div>
      )}
      {availableRuns.map(run => {
        const checked = selected.has(run.folder);
        const disabled = !checked && selected.size >= maxSelect;
        return (
          <label key={run.folder} className={`batch-eval-run-row ${disabled ? 'is-disabled' : ''} ${checked ? 'is-checked' : ''}`}>
            <input type="checkbox" checked={checked} disabled={disabled} onChange={() => onToggle(run.folder)} />
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
      <div className="batch-eval-upload-zone" onClick={() => srcRef.current?.click()}>
        <IconUpload />
        <span>{sourceFile ? sourceFile.name : 'Click to select source PDF'}</span>
        <input ref={srcRef} type="file" accept=".pdf" style={{ display: 'none' }} onChange={e => onSourceChange(e.target.files[0] || null)} />
      </div>
      <div className="batch-eval-upload-zone batch-eval-upload-zone-multi" onClick={() => genRef.current?.click()}>
        <IconFile />
        <span>{generatedFiles.length > 0 ? `${generatedFiles.length} generated PDF(s) selected` : `Click to select generated PDFs (up to ${maxGenerated})`}</span>
        <input ref={genRef} type="file" accept=".pdf" multiple style={{ display: 'none' }} onChange={e => onGeneratedChange(Array.from(e.target.files || []).slice(0, maxGenerated))} />
      </div>
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

export default function BatchEvalModal({ isOpen, onClose, batchEval, loadRuns }) {
  const [tab, setTab] = useState('prior_runs');
  const [selectedFolders, setSelectedFolders] = useState(new Set());
  const [sourceFile, setSourceFile] = useState(null);
  const [generatedFiles, setGeneratedFiles] = useState([]);
  const MAX_SELECT = 20;

  const isActive = batchEval.isStarting || batchEval.isRunning;
  const isIdle = !isActive && !['completed','errored','cancelled'].includes(batchEval.status);

  useEffect(() => {
    if (isOpen && tab === 'prior_runs' && batchEval.availableRuns.length === 0 && !batchEval.loadingRuns) {
      loadRuns();
    }
  }, [isOpen, tab, loadRuns, batchEval.availableRuns.length, batchEval.loadingRuns]);

  const handleToggle = (folder) => {
    setSelectedFolders(prev => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder); else next.add(folder);
      return next;
    });
  };

  const handleStart = () => {
    if (tab === 'prior_runs') {
      batchEval.start({ mode: 'prior_runs', selectedFolders: Array.from(selectedFolders) });
    } else {
      batchEval.start({ mode: 'upload', sourceFile, generatedFiles });
    }
  };

  const canStart = tab === 'prior_runs' ? selectedFolders.size > 0 : (sourceFile && generatedFiles.length > 0);

  const showProgress = batchEval.status !== 'idle' && batchEval.entries.length > 0;

  const progressText = batchEval.totalFiles > 0
    ? `${batchEval.doneFiles}/${batchEval.totalFiles} done`
    : 'Preparing…';

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Batch Evaluation" large
      footer={
        <div className="create-data-footer-actions">
          {isIdle && (
            <Button variant="primary" onClick={handleStart} disabled={!canStart} icon={<IconZap className="btn-icon" />}>
              Run Batch Eval
            </Button>
          )}
          {isActive && (
            <Button variant="danger" onClick={batchEval.stop} disabled={batchEval.status === 'stopping'}>
              Stop
            </Button>
          )}
          <Button variant="secondary" onClick={onClose}>Close</Button>
        </div>
      }
    >
      <div className="create-data-modal">
        {/* Tabs — only show when idle or no run in progress */}
        {(isIdle || batchEval.status === 'idle') && (
          <div className="batch-eval-tabs">
            <Button variant="secondary" className={`batch-eval-tab ${tab === 'prior_runs' ? 'is-active' : ''}`} onClick={() => setTab('prior_runs')}>Prior Runs</Button>
            <Button variant="secondary" className={`batch-eval-tab ${tab === 'upload' ? 'is-active' : ''}`} onClick={() => setTab('upload')}>Upload PDFs</Button>
          </div>
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
              <span>{batchEval.completedFiles} completed{batchEval.failedFiles > 0 ? `, ${batchEval.failedFiles} failed` : ''}</span>
            </div>
          </div>
        )}

        {/* Completed summary */}
        {['completed','errored','cancelled'].includes(batchEval.status) && (
          <div className="create-data-summary">
            <div className="create-data-summary-title">
              <IconZap />
              <div>
                <div className="create-data-heading">Batch Evaluation Complete</div>
                <div className="create-data-subheading">
                  {batchEval.completedFiles}/{batchEval.totalFiles} evals completed
                  {batchEval.outputFolder ? ` · outputs/${batchEval.outputFolder}` : ''}
                </div>
              </div>
            </div>
            <CompareProfessorPanel outputFolder={batchEval.outputFolder} />
          </div>
        )}

        {batchEval.error && <div className="create-data-alert">{batchEval.error}</div>}

        {/* Tab content — only when idle */}
        {batchEval.status === 'idle' && tab === 'prior_runs' && (
          <>
            <div className="batch-eval-hint">Select up to {MAX_SELECT} prior runs to evaluate. {selectedFolders.size > 0 && `(${selectedFolders.size} selected)`}</div>
            <PriorRunsTab availableRuns={batchEval.availableRuns} loadingRuns={batchEval.loadingRuns} selected={selectedFolders} onToggle={handleToggle} maxSelect={MAX_SELECT} />
          </>
        )}
        {batchEval.status === 'idle' && tab === 'upload' && (
          <>
            <div className="batch-eval-hint">Upload a source PDF and up to {MAX_SELECT} generated PDFs to evaluate.</div>
            <UploadTab sourceFile={sourceFile} generatedFiles={generatedFiles} onSourceChange={setSourceFile} onGeneratedChange={setGeneratedFiles} maxGenerated={MAX_SELECT} />
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
