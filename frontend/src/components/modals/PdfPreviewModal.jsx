import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';

function buildBrowserPdfViewerUrl(pdfUrl) {
  if (!pdfUrl) return 'about:blank';
  const inlineUrl = pdfUrl.includes('inline=1')
    ? pdfUrl
    : `${pdfUrl}${pdfUrl.includes('?') ? '&' : '?'}inline=1`;
  return `${inlineUrl}#toolbar=1&navpanes=1&scrollbar=1&view=FitH`;
}

export default function PdfPreviewModal({
  isOpen,
  onClose,
  fileName,
  pdfUrl,
  onDownload,
}) {
  const pdfViewerUrl = buildBrowserPdfViewerUrl(pdfUrl);
  const title = fileName ? `PDF Preview: ${fileName}` : 'PDF Preview';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      size="full"
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
            disabled={!fileName}
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
    </Modal>
  );
}
