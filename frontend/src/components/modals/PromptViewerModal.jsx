import { useState, useEffect } from 'react';
import Modal from './Modal.jsx';

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

async function fetchSkillContent(skillName) {
  try {
    const res = await fetch(`/api/skills/${skillName}`);
    if (!res.ok) throw new Error(`Server responded with ${res.status}`);
    return await res.text();
  } catch (apiError) {
    try {
      const res = await fetch(`/skills/${skillName}/SKILL.md`);
      if (!res.ok) throw new Error(`Local fallback unavailable (${res.status})`);
      return await res.text();
    } catch (localError) {
      throw new Error(`API: ${apiError.message}; Local: ${localError.message}`);
    }
  }
}

/**
 * Generic content viewer modal. Pass either promptName or skillName (not both).
 */
export default function PromptViewerModal({ isOpen, onClose, title, promptName, skillName }) {
  const [content, setContent] = useState('Loading...');

  useEffect(() => {
    if (!isOpen) return;
    setContent('Loading...');

    const load = async () => {
      try {
        if (promptName) {
          setContent(await fetchPromptContent(promptName));
        } else if (skillName) {
          setContent(await fetchSkillContent(skillName));
        }
      } catch (err) {
        setContent(`Error loading content: ${err.message}`);
      }
    };

    load();
  }, [isOpen, promptName, skillName]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title || 'Viewer'}
      large
      footer={
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
      }
    >
      <div className="prompt-viewer-body" style={{ padding: 0 }}>
        <pre className="prompt-viewer-content">{content}</pre>
      </div>
    </Modal>
  );
}
