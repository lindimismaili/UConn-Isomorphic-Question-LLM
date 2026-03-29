import { useState, useEffect } from 'react';
import Modal from './Modal.jsx';
import PromptViewerModal from './PromptViewerModal.jsx';
import { IconZap } from '../Icons.jsx';

const FALLBACK_SKILLS = [
  'check-for-answers', 'confirm-answer', 'convert-to-latex',
  'create-function-for-question', 'determine-question-concept',
  'determine-question-difficulty', 'determine-question-theme',
  'evaluate-via-python', 'extract-docx', 'extract-image',
  'extract-pdf', 'extract-variables', 'generate-distractors',
  'generate-question', 'generate-quick-reason', 'generate-solution-process',
  'identify-author', 'identify-date', 'identify-other', 'identify-questions',
  'identify-title', 'verify-answer', 'verify-latex',
];

async function fetchSkillsList() {
  try {
    const res = await fetch('/api/skills');
    if (!res.ok) throw new Error(`Server responded with ${res.status}`);
    const payload = await res.json();
    if (Array.isArray(payload.skills) && payload.skills.length > 0) return payload.skills;
    throw new Error('Skills list is empty');
  } catch {
    return FALLBACK_SKILLS;
  }
}

export default function SkillsModal({ isOpen, onClose }) {
  const [skills, setSkills] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState(null);

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    fetchSkillsList().then((list) => {
      setSkills([...list].sort((a, b) => a.localeCompare(b)));
      setLoading(false);
    });
  }, [isOpen]);

  return (
    <>
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title="Skills"
        modalBodyClassName="modal-body-flush"
        footer={
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        }
      >
        <div className="skills-list-body">
          <p className="skills-list-intro">
            Click a skill to view its <code>SKILL.md</code>:
          </p>
          <div className="skills-list">
            {loading && (
              <div className="modal-empty-state">Loading skills...</div>
            )}
            {!loading && skills && skills.length > 0 && skills.map((skillName) => (
              <button
                key={skillName}
                className="skill-list-item"
                onClick={() => setSelectedSkill(skillName)}
              >
                <IconZap className="skill-icon" />
                {skillName}
              </button>
            ))}
            {!loading && skills && skills.length === 0 && (
              <div className="modal-empty-state">No skills were returned by the server.</div>
            )}
          </div>
        </div>
      </Modal>

      <PromptViewerModal
        isOpen={!!selectedSkill}
        onClose={() => setSelectedSkill(null)}
        title={selectedSkill || ''}
        skillName={selectedSkill}
      />
    </>
  );
}
