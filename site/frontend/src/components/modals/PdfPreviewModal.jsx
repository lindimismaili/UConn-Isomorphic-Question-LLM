import { useEffect, useState } from 'react';
import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import { Button, EmptyState } from '../ui/index.js';
import { fetchWithAbort, readErrorDetail } from '../../utils/api.js';

/**
 * Builds an iframe URL that prefers browser-native PDF controls.
 *
 * @param {string} pdfUrl
 * @returns {string}
 */
function buildBrowserPdfViewerUrl(pdfUrl) {
  if (!pdfUrl) return 'about:blank';
  if (pdfUrl.startsWith('blob:')) {
    return `${pdfUrl}#toolbar=1&navpanes=1&scrollbar=1&view=FitH`;
  }
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
  const [preview, setPreview] = useState({
    sourceUrl: '',
    objectUrl: '',
    loading: false,
    error: '',
  });
  const activeUrl = isOpen ? pdfUrl : '';
  if (preview.sourceUrl !== activeUrl) {
    setPreview({
      sourceUrl: activeUrl,
      objectUrl: '',
      loading: Boolean(activeUrl),
      error: '',
    });
  }
  const previewMatches = preview.sourceUrl === activeUrl;
  const pdfViewerUrl = buildBrowserPdfViewerUrl(
    previewMatches ? preview.objectUrl : '',
  );
  const title = fileName ? `PDF Preview: ${fileName}` : 'PDF Preview';

  useEffect(() => {
    if (!activeUrl) return undefined;
    const controller = new AbortController();
    let objectUrl = '';

    fetchWithAbort(activeUrl, { cache: 'no-store' }, controller.signal)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            await readErrorDetail(response, `Unable to load PDF (${response.status})`),
          );
        }
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setPreview({
          sourceUrl: activeUrl,
          objectUrl,
          loading: false,
          error: '',
        });
      })
      .catch((error) => {
        if (error.name === 'AbortError') return;
        setPreview({
          sourceUrl: activeUrl,
          objectUrl: '',
          loading: false,
          error: error.message || 'Unable to load PDF.',
        });
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [activeUrl]);

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
        {!previewMatches || preview.loading ? (
          <EmptyState variant="modal">Loading PDF preview…</EmptyState>
        ) : preview.error ? (
          <EmptyState variant="modal" error>{preview.error}</EmptyState>
        ) : (
          <iframe
            key={pdfViewerUrl}
            className="pdf-preview-frame"
            src={pdfViewerUrl}
            title={`Preview of ${fileName}`}
          />
        )}
      </div>
    </Modal>
  );
}
