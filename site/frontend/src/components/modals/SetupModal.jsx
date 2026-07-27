import Modal from './Modal.jsx';
import { useAgentRegistry } from '../../hooks/useAgentRegistry';
import { Button, KeyValueTable } from '../ui/index.js';

/**
 * Details for the runtime starter phase shown in the pipeline: the runtime
 * parameters used to provision isolated provider containers plus the inputs
 * (files and prompt) the run started with.
 *
 * @param {object} props
 * @param {boolean} props.isOpen
 * @param {Function} props.onClose
 * @param {string | null} props.workflowId - Backend id of the latest run.
 * @param {{prompt: string, files: string[], startAgent: string} | null} props.runInputs
 */
export default function SetupModal({ isOpen, onClose, workflowId, runInputs }) {
  const registryData = useAgentRegistry();
  const setupKey = registryData?.runtimeAgentKeys?.[0] || registryData?.pipelineKeys?.[0];
  const setupEntry = setupKey ? registryData?.registry?.[setupKey] : null;

  const packages = Array.isArray(setupEntry?.containerPythonPackages)
    ? setupEntry.containerPythonPackages
    : [];
  const domains = Array.isArray(setupEntry?.allowedDomains) ? setupEntry.allowedDomains : [];
  const inputFiles = runInputs?.files || [];
  const promptText = runInputs?.prompt || '';

  const parameters = [
    { key: 'OpenAI Runtime', value: setupEntry?.model || 'Unavailable' },
    { key: 'Workflow ID', value: workflowId || 'Not started yet' },
    {
      key: 'Container Isolation',
      value: 'Disposable extraction container, then a clean downstream container',
    },
    ...(runInputs?.startAgent
      ? [{ key: 'Started From Agent', value: runInputs.startAgent }]
      : []),
  ];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Starting Runtime"
      large
      footer={
        <Button variant="primary" onClick={onClose}>Close</Button>
      }
    >
      <div className="setup-modal-body modal-body-stack">
        <p>
          <strong>Starting Runtime</strong> is not an agent. It covers the time
          after you click <strong>Send</strong> while the system uploads your
          files, provisions the isolated extraction runtime, and queues the
          agents. If the content guardrail accepts the extracted structure,
          downstream reasoning and LaTeX work continue in a fresh container
          that never receives the source-file attachments.
        </p>

        <h4>Runtime Parameters</h4>
        <KeyValueTable rows={parameters} />

        {packages.length > 0 && (
          <>
            <h4>Installed Packages</h4>
            <div className="runtime-pill-list">
              {packages.map((pkg) => (
                <span className="viewer-pill viewer-pill-neutral" key={pkg}>{pkg}</span>
              ))}
            </div>
          </>
        )}

        {domains.length > 0 && (
          <>
            <h4>Allowed Domains</h4>
            <div className="runtime-pill-list">
              {domains.map((domain) => (
                <span className="viewer-pill viewer-pill-neutral" key={domain}>{domain}</span>
              ))}
            </div>
          </>
        )}

        <h4>Input Files</h4>
        {inputFiles.length > 0 ? (
          <ul className="viewer-source-files">
            {inputFiles.map((name) => (
              <li className="viewer-source-file" key={name}>{name}</li>
            ))}
          </ul>
        ) : (
          <p className="runtime-empty-note">
            {runInputs ? 'No files were attached to this run.' : 'Run a workflow to see its inputs here.'}
          </p>
        )}

        {promptText && (
          <>
            <h4>Input Text</h4>
            <pre className="detail-text-box runtime-input-text">{promptText}</pre>
          </>
        )}
      </div>
    </Modal>
  );
}
