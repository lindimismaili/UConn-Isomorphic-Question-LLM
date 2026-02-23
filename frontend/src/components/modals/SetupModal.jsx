import Modal from './Modal.jsx';

export default function SetupModal({ isOpen, onClose }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Setup"
      footer={
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
      }
    >
      <div className="setup-modal-body">
        <p>
          <strong>Setup</strong> is not an agent. It represents the time
          immediately after you click <strong>Send</strong> while the system
          prepares to run the pipeline.
        </p>
        <h4>What it does</h4>
        <ul>
          <li>Create/prepare the execution environment</li>
          <li>Start the container / runtime</li>
          <li>Upload files (if any)</li>
          <li>Start the workflow and queue the agents</li>
        </ul>
      </div>
    </Modal>
  );
}
