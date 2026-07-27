import { useState, useEffect } from 'react';
import Modal from './Modal.jsx';
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
      footer={<Button variant="primary" onClick={onClose}>Close</Button>}
    >
      <div className="prompt-viewer-body">
        <pre className="prompt-viewer-content">{content}</pre>
      </div>
    </Modal>
  );
}
