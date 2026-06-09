import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';
import { parseJsonl } from './eval/evalData.js';

export default function EvalDatasetModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);

  let rows = [];
  if (text) {
    try {
      rows = parseJsonl(text).map((record) => {
        const item = record.item || record.datasource_item || record;
        return {
          sourceId: item.source_id ?? '',
          sourceText: item.source_question_text || item.source_question || '',
          generatedText: item.generated_question_text || item.generated_question || '',
        };
      });
    } catch { /* ignore */ }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={fileName ? fileName.split('/').pop() : 'Evaluation Dataset'}
      contentClassName="eval-dataset-modal" size="full"
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
      {!loading && !error && rows.length === 0 && (
        <div className="modal-empty-state">No items found.</div>
      )}
      {!loading && !error && rows.length > 0 && (
        <div className="file-viewer-table-shell">
          <table className="file-viewer-table eval-dataset-table">
            <thead>
              <tr>
                <th className="viewer-th-narrow">ID</th>
                <th>Source Question</th>
                <th>Generated Question</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  <td className="viewer-cell-id">{row.sourceId || i + 1}</td>
                  <td><div className="viewer-cell-text">{row.sourceText || <span className="viewer-cell-muted">—</span>}</div></td>
                  <td><div className="viewer-cell-text">{row.generatedText || <span className="viewer-cell-muted">—</span>}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
