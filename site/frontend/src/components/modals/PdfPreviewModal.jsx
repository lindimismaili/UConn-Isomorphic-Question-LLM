import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import { Button } from '../ui/index.js';

/**
 * Builds an iframe URL that prefers browser-native PDF controls.
 *
 * @param {string} pdfUrl
 * @returns {string}
 */
function buildBrowserPdfViewerUrl(pdfUrl) {
  if (!pdfUrl) return 'about:blank';
  const inlineUrl = pdfUrl.includes('inline=1')
    ? pdfUrl
    : `${pdfUrl}${pdfUrl.includes('?') ? '&' : '?'}inline=1`;
  return `${inlineUrl}#toolbar=1&navpanes=1&scrollbar=1&view=FitH`;
}

/**
 * Shows an inline preview for generated PDFs and links back to download.
 *
 * @param {object} props
 */
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
          <Button variant="primary" className="pdf-preview-action" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="secondary"
            className="pdf-preview-action pdf-preview-download"
            onClick={() => onDownload(fileName)}
            disabled={!fileName}
            icon={<IconDownload className="btn-icon" />}
          >
            Download PDF
          </Button>
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
