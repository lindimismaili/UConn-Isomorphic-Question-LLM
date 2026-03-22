import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';

export default function PdfPreviewModal({
  isOpen,
  onClose,
  fileName,
  pdfUrl,
  onDownload,
}) {
  if (!fileName || !pdfUrl) return null;

  const pdfViewerUrl = `${pdfUrl}${pdfUrl.includes('?') ? '&' : '?'}inline=1`;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`PDF Preview: ${fileName}`}
      size="full"
      modalClassName="pdf-preview-root"
      bodyClassName="pdf-preview-open"
      contentClassName="pdf-preview-modal"
      footer={(
        <div className="modal-footer-actions pdf-preview-footer-actions">
          <button
            className="btn btn-secondary"
            onClick={onClose}
            type="button"
          >
            Close
          </button>
          <button
            className="btn btn-primary pdf-preview-download"
            onClick={() => onDownload(fileName)}
            type="button"
          >
            <IconDownload className="btn-icon" />
            Download PDF
          </button>
        </div>
      )}
    >
      <div className="pdf-preview-frame-shell">
        <iframe
          key={pdfViewerUrl}
          className="pdf-preview-frame"
          src={pdfViewerUrl}
          title={`Preview of ${fileName}`}
        />
      </div>

      <p className="pdf-preview-hint">
        Use the modal scroll area and the PDF viewer scrollbars to inspect the full document.
      </p>
    </Modal>
  );
}
