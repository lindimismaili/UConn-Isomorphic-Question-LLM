import { useEffect, useRef, useState } from 'react';
import PdfPreviewModal from './PdfPreviewModal.jsx';
import TexViewerModal from './TexViewerModal.jsx';
import CsvViewerModal from './CsvViewerModal.jsx';
import JsonTextViewerModal from './JsonTextViewerModal.jsx';
import { baseName, buildWorkflowFileUrl, getViewerType } from '../../utils/files.js';
import { downloadApiFile } from '../../utils/api.js';

/**
 * Shared view/download wiring for workflow output files.
 *
 * Owns the open state for the four file viewers (PDF preview, .tex source,
 * CSV table, JSON/plain text) so any surface can offer View/Download buttons
 * without re-implementing the modals. Render `viewerModals` once alongside
 * the host component's JSX.
 *
 * @param {string} workflowId - Workflow whose files are being viewed.
 * @returns {{
 *   openFile: (fileName: string) => void,
 *   downloadFile: (fileName: string) => void,
 *   getFileUrl: (fileName: string, inline?: boolean) => string,
 *   viewerModals: JSX.Element,
 * }}
 */
export default function useFileViewer(workflowId) {
  const previewCloseTimerRef = useRef(null);
  const [previewFile, setPreviewFile] = useState('');
  const [isPdfPreviewOpen, setIsPdfPreviewOpen] = useState(false);
  const [viewerFile, setViewerFile] = useState('');
  const [viewerType, setViewerType] = useState(null);

  useEffect(() => () => {
    clearTimeout(previewCloseTimerRef.current);
  }, []);

  const getFileUrl = (fileName, inline = false) =>
    buildWorkflowFileUrl(workflowId, fileName, inline);

  const openFile = (fileName) => {
    const type = getViewerType(fileName);
    if (!type) return;
    if (type === 'pdf') {
      clearTimeout(previewCloseTimerRef.current);
      setPreviewFile(fileName);
      setIsPdfPreviewOpen(true);
      return;
    }
    setViewerFile(fileName);
    setViewerType(type);
  };

  const downloadFile = async (fileName) => {
    const url = getFileUrl(fileName);
    if (!url) {
      alert('Download failed: workflow download link is unavailable.');
      return;
    }
    try {
      await downloadApiFile(url, baseName(fileName));
    } catch (error) {
      alert(`Download failed: ${error.message}`);
    }
  };

  const handlePreviewClose = () => {
    setIsPdfPreviewOpen(false);
    clearTimeout(previewCloseTimerRef.current);
    // Keep the file name through the modal close transition.
    previewCloseTimerRef.current = setTimeout(() => setPreviewFile(''), 260);
  };

  const handleViewerClose = () => {
    setViewerFile('');
    setViewerType(null);
  };

  const viewerModals = (
    <>
      <PdfPreviewModal
        isOpen={isPdfPreviewOpen}
        onClose={handlePreviewClose}
        fileName={previewFile}
        pdfUrl={previewFile ? getFileUrl(previewFile, true) : ''}
        onDownload={downloadFile}
      />
      <TexViewerModal
        isOpen={viewerType === 'tex'}
        onClose={handleViewerClose}
        fileName={viewerFile}
        url={viewerFile ? getFileUrl(viewerFile, true) : ''}
        onDownload={downloadFile}
      />
      <CsvViewerModal
        isOpen={viewerType === 'csv'}
        onClose={handleViewerClose}
        fileName={viewerFile}
        url={viewerFile ? getFileUrl(viewerFile, true) : ''}
        onDownload={downloadFile}
      />
      <JsonTextViewerModal
        isOpen={viewerType === 'json-text'}
        onClose={handleViewerClose}
        fileName={viewerFile}
        url={viewerFile ? getFileUrl(viewerFile, true) : ''}
        onDownload={downloadFile}
      />
    </>
  );

  return { openFile, downloadFile, getFileUrl, viewerModals };
}
