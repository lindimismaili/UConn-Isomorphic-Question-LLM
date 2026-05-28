import Modal from './Modal.jsx';

/**
 * Explains the final runtime step that publishes completed artifacts.
 *
 * @param {object} props
 */
export default function OutputModal({ isOpen, onClose }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Workflow Runtime"
      footer={
        <button className="btn btn-primary" onClick={onClose}>Close</button>
      }
    >
      <div className="setup-modal-body modal-body-stack">
        <p>
          <strong>Workflow Runtime</strong> is not an agent. It represents the
          final workflow step after <strong>Evaluator</strong>, when the system
          writes and exposes the completed output artifacts.
        </p>
        <h4>What it does</h4>
        <ul>
          <li>Finalize the workflow result payload</li>
          <li>Write the final result JSON, LaTeX, and PDF files</li>
          <li>Collect evaluator and agent artifact download metadata</li>
          <li>Make the completed files available in the download panel</li>
        </ul>
      </div>
    </Modal>
  );
}
