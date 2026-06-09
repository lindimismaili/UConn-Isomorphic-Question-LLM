import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';

/**
 * Parses a CSV string into a 2D array of strings.
 * Handles RFC-4180 quoted fields.
 */
function parseCsv(text) {
  const rows = [];
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    if (!line.trim()) continue;
    const fields = [];
    let i = 0;
    while (i < line.length) {
      if (line[i] === '"') {
        let value = '';
        i++; // skip opening quote
        while (i < line.length) {
          if (line[i] === '"' && line[i + 1] === '"') {
            value += '"';
            i += 2;
          } else if (line[i] === '"') {
            i++; // skip closing quote
            break;
          } else {
            value += line[i++];
          }
        }
        fields.push(value);
        if (line[i] === ',') i++;
      } else {
        const end = line.indexOf(',', i);
        if (end === -1) {
          fields.push(line.slice(i));
          break;
        } else {
          fields.push(line.slice(i, end));
          i = end + 1;
        }
      }
    }
    rows.push(fields);
  }
  return rows;
}

const PASS_FAIL_COLS = new Set(['status', 'preserve_physical_content_passed', 'scenario_variation_passed',
  'numerical_variation_passed', 'physics_correctness_passed', 'numeric_reasonableness_passed',
  'distractor_structure_preservation_passed', 'clerical_quality_passed', 'solvable_and_specified_passed']);

const SCORE_COLS = new Set(['preserve_physical_content_score', 'scenario_variation_score',
  'numerical_variation_score', 'physics_correctness_score', 'numeric_reasonableness_score',
  'distractor_structure_preservation_score', 'clerical_quality_score', 'solvable_and_specified_score']);

function CellValue({ header, value }) {
  const h = header?.toLowerCase() || '';
  const v = value || '';

  if (h === 'status' || h.endsWith('_passed')) {
    const isPass = v === 'passed' || v.toUpperCase() === 'TRUE' || v === '1';
    const isFail = v === 'failed' || v.toUpperCase() === 'FALSE' || v === '0';
    if (isPass) return <span className="viewer-pill viewer-pill-pass">Pass</span>;
    if (isFail) return <span className="viewer-pill viewer-pill-fail">Fail</span>;
    if (!v) return <span className="viewer-cell-muted">—</span>;
  }

  if (SCORE_COLS.has(h)) {
    if (!v) return <span className="viewer-cell-muted">—</span>;
    const num = parseFloat(v);
    return <span className="viewer-cell-score">{isNaN(num) ? v : num.toFixed(1)}</span>;
  }

  if (h === 'source_question_text' || h === 'generated_question_text' || h === 'failed_criteria') {
    return <span className="viewer-cell-text" title={v}>{v || <span className="viewer-cell-muted">—</span>}</span>;
  }

  return <>{v || <span className="viewer-cell-muted">—</span>}</>;
}

export default function CsvViewerModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);
  const rows = text ? parseCsv(text) : [];
  const headers = rows[0] || [];
  const dataRows = rows.slice(1);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={fileName ? fileName.split('/').pop() : 'CSV Viewer'}
      contentClassName="csv-viewer-modal" size="full"
      footer={(
        <div className="modal-footer-actions">
          {onDownload && fileName && (
            <button className="btn btn-secondary" onClick={() => onDownload(fileName)} type="button">
              <IconDownload className="btn-icon" />
              Download
            </button>
          )}
          <button className="btn btn-primary" onClick={onClose} type="button">Close</button>
        </div>
      )}
    >
      {loading && <div className="modal-empty-state">Loading…</div>}
      {error && <div className="modal-empty-state modal-empty-state-error">{error}</div>}
      {!loading && !error && headers.length > 0 && (
        <div className="file-viewer-table-shell">
          <table className="file-viewer-table">
            <thead>
              <tr>
                {headers.map((h, i) => (
                  <th key={i} className={PASS_FAIL_COLS.has(h) || SCORE_COLS.has(h) ? 'viewer-th-narrow' : ''}>
                    {h.replaceAll('_', ' ')}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dataRows.map((row, ri) => (
                <tr key={ri}>
                  {headers.map((h, ci) => (
                    <td key={ci}>
                      <CellValue header={h} value={row[ci] || ''} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && !error && headers.length === 0 && (
        <div className="modal-empty-state">No data found in file.</div>
      )}
    </Modal>
  );
}
