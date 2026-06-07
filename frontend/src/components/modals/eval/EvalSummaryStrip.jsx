import { formatScore } from './evalData.js';

/**
 * Top strip of the Evaluator Results modal: status, overall score, and counts.
 *
 * @param {object} props
 * @param {object} props.report - Evaluation report payload.
 * @param {number} props.itemCount - Number of normalized detail rows.
 * @param {number} props.totalPassed
 * @param {number} props.totalFailed
 */
export default function EvalSummaryStrip({ report, itemCount, totalPassed, totalFailed }) {
  const reportStatus = report?.status || 'unknown';
  return (
    <div className="eval-summary-strip">
      <div>
        <span className="eval-summary-label">Status</span>
        <span className={`eval-status-chip eval-status-${reportStatus}`}>{reportStatus}</span>
      </div>
      <div>
        <span className="eval-summary-label">Overall Score</span>
        <strong>{formatScore(report?.overall_score)}</strong>
      </div>
      <div>
        <span className="eval-summary-label">Items</span>
        <strong>{report?.items_evaluated ?? itemCount}</strong>
      </div>
      <div>
        <span className="eval-summary-label">Question Results</span>
        <strong>{totalPassed} pass / {totalFailed} fail</strong>
      </div>
    </div>
  );
}
