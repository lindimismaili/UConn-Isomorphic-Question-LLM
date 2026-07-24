import { useState, useEffect } from 'react';
import Modal from './Modal.jsx';
import { Button } from '../ui/index.js';

/**
 * Loads prompt text from the API first, then the bundled data directory.
 *
 * Touches `/api/prompts/:promptName` and `/data/prompts/:promptName.txt`.
 */
async function fetchPromptContent(promptName) {
  try {
    const res = await fetch(`/api/prompts/${promptName}`);
    if (!res.ok) throw new Error(`Server responded with ${res.status}`);
    return await res.text();
  } catch (apiError) {
    try {
      const res = await fetch(`/data/prompts/${promptName}.txt`);
      if (!res.ok) throw new Error(`Local fallback unavailable (${res.status})`);
      return await res.text();
    } catch (localError) {
      throw new Error(`API: ${apiError.message}; Local: ${localError.message}`);
    }
  }
}

/**
 * Generic content viewer for prompt documents.
 *
 * Pass `promptName` so the modal can fetch the corresponding backend or
 * bundled file.
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
