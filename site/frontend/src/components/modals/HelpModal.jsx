import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';
import { Button } from '../ui/index.js';

/**
 * Explains the workflow at a high level for the current app session.
 *
 * @param {object} props
 */
export default function HelpModal({ isOpen, onClose }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Summary"
      footer={
        <Button variant="primary" onClick={onClose}>Got it!</Button>
      }
    >
      <div className="help-modal-body modal-body-stack">
        <p className="help-intro">
          This tool generates{' '}
          <strong>isomorphic physics questions</strong> from your source
          questions and instructions.
        </p>
        <h4>Key Features</h4>
        <ul className="help-list">
          <li>
            <strong>Source Extraction</strong> — Extracts question text,
            answers, type, title, and theme from uploaded materials
          </li>
          <li>
            <strong>Optional Difficulty</strong> — Difficulty stays blank unless
            you explicitly ask for one
          </li>
          <li>
            <strong>Theme Guidance</strong> — Use the Theme Prompt selector or
            write your own instructions to steer the generated questions
          </li>
          <li>
            <strong>Answer Verification</strong> — All generated questions are
            verified for correctness
          </li>
          <li>
            <strong>Question Type Support</strong> — Supports multiple-choice,
            open-ended, and multi-part questions
          </li>
          <li>
            <strong>Education-Ready Output</strong> — Questions are formatted for
            PDF and LaTeX output, with evaluation and agent artifacts available
            for review
          </li>
        </ul>
        <div className="output-folder-note modal-file-location help-file-location">
          <IconFile />
          <span>
            File located in: <code>data/template.tex</code>
          </span>
        </div>
        <h4>How It Works</h4>
        <ol className="help-list numbered">
          <li>Upload source questions or choose test files</li>
          <li>Provide instructions, and optionally choose a Theme Prompt</li>
          <li>
            Click <strong>Send</strong> to start the generation pipeline
          </li>
          <li>View logs, inspect agent artifacts, and download results when complete</li>
        </ol>
      </div>
    </Modal>
  );
}
