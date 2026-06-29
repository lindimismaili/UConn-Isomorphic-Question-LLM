import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';
import { Button, DataTable, EmptyState } from '../ui/index.js';

/**
 * Parses a CSV string into a 2D array of strings.
 *
 * A single character state-machine over the whole document, so RFC-4180 quoted
 * fields containing commas or newlines stay inside their cell — rows only break
 * on unquoted line breaks.
 *
 * @param {string} text
 * @returns {Array<Array<string>>}
 */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  let started = false; // whether the current row has any content

  const pushField = () => {
    row.push(field);
    field = '';
  };
  const pushRow = () => {
    pushField();
    // Skip blank trailing rows produced by a final newline.
    if (!(row.length === 1 && row[0] === '')) rows.push(row);
    row = [];
    started = false;
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];

    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++; // consume the escaped quote
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
      started = true;
    } else if (ch === ',') {
      pushField();
      started = true;
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++; // CRLF
      if (started || field) pushRow();
    } else {
      field += ch;
      started = true;
    }
  }

  if (started || field) pushRow();
  return rows;
}

/** Pass/fail columns: the status column plus any rubric `*_passed` column. */
function isPassFailColumn(header) {
  const h = header?.toLowerCase() || '';
  return h === 'status' || h.endsWith('_passed');
}

/** Numeric rubric `*_score` columns. */
function isScoreColumn(header) {
  return (header?.toLowerCase() || '').endsWith('_score');
}

function CellValue({ header, value }) {
  const v = value || '';

  if (isPassFailColumn(header)) {
    const isPass = v === 'passed' || v.toUpperCase() === 'TRUE' || v === '1';
    const isFail = v === 'failed' || v.toUpperCase() === 'FALSE' || v === '0';
    if (isPass) return <span className="viewer-pill viewer-pill-pass">Pass</span>;
    if (isFail) return <span className="viewer-pill viewer-pill-fail">Fail</span>;
    if (!v) return <span className="viewer-cell-muted">—</span>;
  }

  if (isScoreColumn(header)) {
    if (!v) return <span className="viewer-cell-muted">—</span>;
    const num = parseFloat(v);
    return <span className="viewer-cell-score">{isNaN(num) ? v : num.toFixed(1)}</span>;
  }

  const h = header?.toLowerCase() || '';

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
            <Button variant="secondary" onClick={() => onDownload(fileName)} icon={<IconDownload className="btn-icon" />}>
              Download
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>Close</Button>
        </div>
      )}
    >
      {loading && <EmptyState variant="modal">Loading…</EmptyState>}
      {error && <EmptyState variant="modal" error>{error}</EmptyState>}
      {!loading && !error && headers.length > 0 && (
        <div className="file-viewer-table-shell">
          <DataTable variant="file-viewer">
            <thead>
              <tr>
                {headers.map((h, i) => (
                  <th key={i} className={isPassFailColumn(h) || isScoreColumn(h) ? 'viewer-th-narrow' : ''}>
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
          </DataTable>
        </div>
      )}
      {!loading && !error && headers.length === 0 && (
        <EmptyState variant="modal">No data found in file.</EmptyState>
      )}
    </Modal>
  );
}
