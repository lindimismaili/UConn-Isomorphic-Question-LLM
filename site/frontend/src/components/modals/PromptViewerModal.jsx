import { useState, useEffect } from 'react';
import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';
import { Button } from '../ui/index.js';
import { fetchWithAbort } from '../../utils/api.js';

/** Load prompt text through the authenticated backend API. */
async function fetchPromptContent(promptName) {
  const res = await fetchWithAbort(`/api/prompts/${promptName}`);
  if (!res.ok) throw new Error(`Server responded with ${res.status}`);
  return res.text();
}

/**
 * Generic content viewer for prompt documents.
 *
 * Pass `promptName` so the modal can fetch the corresponding backend file.
 */
export default function PromptViewerModal({ isOpen, onClose, title, promptName }) {
  const [content, setContent] = useState('Loading...');
  const fileLocation = promptName
    ? {
        label: `data/prompts/${promptName}.txt`,
        href: `/api/prompts/${promptName}`,
      }
    : null;

  useEffect(() => {
    if (!isOpen) return;

    const load = async () => {
      setContent('Loading...');
      try {
        if (promptName) {
          setContent(await fetchPromptContent(promptName));
        }
      } catch (err) {
        setContent(`Error loading content: ${err.message}`);
      }
    };

    load();
  }, [isOpen, promptName]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title || 'Viewer'}
      large
      modalBodyClassName="modal-body-flush"
      footer={
        <div className="modal-footer-row">
          <div className="output-folder-note modal-file-location">
            <IconFile />
            <span>
              File located in:{' '}
              <code>{fileLocation?.label || 'Unavailable'}</code>
            </span>
          </div>
          <div className="modal-footer-actions">
            <Button variant="primary" onClick={onClose}>Close</Button>
          </div>
        </div>
      }
    >
      <div className="prompt-viewer-body">
        <pre className="prompt-viewer-content">{content}</pre>
      </div>
    </Modal>
  );
}
