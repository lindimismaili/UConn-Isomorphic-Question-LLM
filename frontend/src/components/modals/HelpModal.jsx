import Modal from './Modal.jsx';
import { IconFile } from '../Icons.jsx';

export default function HelpModal({ isOpen, onClose }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Summary"
      footer={
        <button className="btn btn-primary" onClick={onClose}>Got it!</button>
      }
    >
      <div className="help-modal-body">
        <p className="help-intro">
          This tool generates{' '}
          <strong>isomorphic multiple-choice physics questions</strong> based on
          your input questions.
        </p>
        <h4>Key Features</h4>
        <ul className="help-list">
          <li>
            <strong>Customizable Generation</strong> — Adjust difficulty, theme,
            and the number of questions generated per source
          </li>
          <li>
            <strong>Answer Verification</strong> — All generated questions are
            verified for correctness
          </li>
          <li>
            <strong>Plausible Distractors</strong> — Wrong answers are based on
            common student misconceptions
          </li>
          <li>
            <strong>Education-Ready Output</strong> — Questions are formatted for
            direct use in assessments
          </li>
          <li>
            <strong>LaTeX Support</strong> — Mathematical expressions are
            preserved with proper formatting
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
          <li>Upload your source questions (PDF or DOCX)</li>
          <li>Provide any additional instructions or preferences</li>
          <li>
            Click <strong>Send</strong> to start the generation pipeline
          </li>
          <li>Download your new isomorphic questions when complete</li>
        </ol>
      </div>
    </Modal>
  );
}
