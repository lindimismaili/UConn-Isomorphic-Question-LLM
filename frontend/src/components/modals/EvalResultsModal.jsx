import { useMemo } from 'react';
import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import {
  DEFAULT_PASSING_THRESHOLD,
  buildAggregateRows,
  buildRubricMap,
  normalizeEvalRecord,
  normalizeFileReference,
} from './eval/evalData.js';
import { useEvalItems } from './eval/useEvalItems.js';
import EvalSummaryStrip from './eval/EvalSummaryStrip.jsx';
import EvalReferencePanel from './eval/EvalReferencePanel.jsx';
import EvalItemsTable from './eval/EvalItemsTable.jsx';
import EvalAggregateTable from './eval/EvalAggregateTable.jsx';

/**
 * Displays detailed evaluation output and lets the user download source files.
 *
 * Orchestrates the eval/* sub-components: it loads the items export, derives the
 * report-driven data, and chooses between the detailed and aggregate tables.
 *
 * @param {object} props
 */
export default function EvalResultsModal({
  isOpen,
  onClose,
  report,
  itemsUrl,
  itemsFileName,
  reportFileName,
  onDownload,
}) {
  const { items, error, loadState } = useEvalItems(isOpen, itemsUrl);

  const threshold = typeof report?.passing_score_threshold === 'number'
    ? report.passing_score_threshold
    : DEFAULT_PASSING_THRESHOLD;
  const rubricByName = useMemo(() => buildRubricMap(report), [report]);
  const runInputFiles = useMemo(
    () =>
      (Array.isArray(report?.run_inputs) ? report.run_inputs : [])
        .map((file) => normalizeFileReference(file))
        .filter(Boolean),
    [report],
  );
  const testingFiles = useMemo(
    () =>
      (Array.isArray(report?.testing_files) ? report.testing_files : [])
        .map((file) => normalizeFileReference(file))
        .filter(Boolean),
    [report],
  );
  const rubricFiles = useMemo(() => {
    const sourcePath =
      typeof report?.rubric_source_path === 'string' && report.rubric_source_path
        ? report.rubric_source_path
        : 'data/rubric.json';
    if (typeof report?.rubric_file === 'string' && report.rubric_file) {
      const ref = normalizeFileReference({
        name: report.rubric_file,
        label: 'Rubric snapshot',
        kind: 'rubric',
        display_path: sourcePath,
      });
      return ref ? [ref] : [];
    }
    if (Array.isArray(report?.rubric) && report.rubric.length > 0) {
      const ref = normalizeFileReference({
        label: 'Rubric config',
        kind: 'rubric',
        display_path: sourcePath,
      });
      return ref ? [ref] : [];
    }
    return [];
  }, [report]);

  const spreadsheetFileName =
    typeof report?.spreadsheet_file === 'string' ? report.spreadsheet_file : '';
  const rows = useMemo(
    () => items.map((item, index) => normalizeEvalRecord(item, index, rubricByName, threshold)),
    [items, rubricByName, threshold],
  );
  const aggregateRows = useMemo(() => buildAggregateRows(report), [report]);

  const totalPassed = rows.filter((row) => row.status === 'passed').length;
  const totalFailed = rows.filter((row) => row.status === 'failed').length;

  const downloadButton = (fileName, label) => (
    <button className="btn btn-secondary" onClick={() => onDownload(fileName)} type="button">
      <IconDownload className="btn-icon" />
      {label}
    </button>
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Evaluator Results"
      size="full"
      contentClassName="eval-results-modal"
      footer={(
        <div className="modal-footer-actions eval-modal-footer-actions">
          {spreadsheetFileName && downloadButton(spreadsheetFileName, 'Spreadsheet CSV')}
          {itemsFileName && downloadButton(itemsFileName, 'Items JSONL')}
          {reportFileName && downloadButton(reportFileName, 'Report JSON')}
          <button className="btn btn-primary" onClick={onClose} type="button">
            Close
          </button>
        </div>
      )}
    >
      <EvalSummaryStrip
        report={report}
        itemCount={rows.length}
        totalPassed={totalPassed}
        totalFailed={totalFailed}
      />

      <div className="eval-reference-grid">
        <EvalReferencePanel
          title="Run Inputs"
          files={runInputFiles}
          emptyText="No input files recorded for this run."
        />
        <EvalReferencePanel
          title="Testing Files"
          files={testingFiles}
          emptyText="No testing artifacts were recorded."
          onDownload={onDownload}
        />
        <EvalReferencePanel
          title="Rubric"
          files={rubricFiles}
          emptyText="No rubric file metadata is available."
          onDownload={onDownload}
        />
      </div>

      {loadState === 'loading' && (
        <div className="modal-empty-state">Loading detailed eval items...</div>
      )}
      {loadState === 'error' && <div className="modal-empty-state">{error}</div>}
      {loadState === 'unavailable' && aggregateRows.length === 0 && (
        <div className="modal-empty-state">
          Detailed eval items are not available for this run.
        </div>
      )}

      {rows.length > 0 && <EvalItemsTable rows={rows} />}

      {rows.length === 0 && aggregateRows.length > 0 && loadState !== 'loading' && (
        <EvalAggregateTable rows={aggregateRows} />
      )}
    </Modal>
  );
}
