import { useMemo, useState } from 'react';
import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import CriterionReasoningModal from './eval/CriterionReasoningModal.jsx';
import {
  DEFAULT_PASSING_THRESHOLD,
  buildAggregateRows,
  buildRubricMap,
  normalizeEvalRecord,
} from './eval/evalData.js';
import { useEvalItems } from './eval/useEvalItems.js';
import EvalSummaryStrip from './eval/EvalSummaryStrip.jsx';
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

  const [criterionDetail, setCriterionDetail] = useState(null);

  const spreadsheetFileName =
    typeof report?.spreadsheet_file === 'string' ? report.spreadsheet_file : '';
  const rows = useMemo(
    () => items.map((item, index) => normalizeEvalRecord(item, index, rubricByName, threshold)),
    [items, rubricByName, threshold],
  );
  const aggregateRows = useMemo(() => buildAggregateRows(report), [report]);

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
      title="Evaluations"
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
      <EvalSummaryStrip report={report} itemCount={rows.length} />

      {loadState === 'loading' && (
        <div className="modal-empty-state">Loading detailed eval items...</div>
      )}
      {loadState === 'error' && <div className="modal-empty-state">{error}</div>}
      {loadState === 'unavailable' && aggregateRows.length === 0 && (
        <div className="modal-empty-state">
          Detailed eval items are not available for this run.
        </div>
      )}

      {rows.length > 0 && <EvalItemsTable rows={rows} onCriterionClick={setCriterionDetail} />}

      {rows.length === 0 && aggregateRows.length > 0 && loadState !== 'loading' && (
        <EvalAggregateTable rows={aggregateRows} />
      )}

      <CriterionReasoningModal
        isOpen={!!criterionDetail}
        criterion={criterionDetail}
        onClose={() => setCriterionDetail(null)}
      />
    </Modal>
  );
}
