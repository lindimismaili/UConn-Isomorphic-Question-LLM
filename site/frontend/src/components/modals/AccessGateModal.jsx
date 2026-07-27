import { useState } from 'react';

import { Button, EmptyState, Modal, TextInput } from '../ui/index.js';

/**
 * Required application-access gate. This accepts only the server's application
 * access token; the OpenAI API key remains on the server.
 */
export default function AccessGateModal({
  isOpen,
  loading,
  verifying,
  configAvailable,
  hasSavedToken = false,
  error,
  onVerify,
  onRetry,
}) {
  const [token, setToken] = useState('');

  const handleVerify = async () => {
    const accepted = await onVerify(token);
    if (accepted) setToken('');
  };

  const handleKeyDown = (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    if (!verifying) handleVerify();
  };

  const needsConfigurationRetry = !loading && !configAvailable;
  const canRetrySavedToken = !loading
    && configAvailable
    && hasSavedToken
    && Boolean(error);

  return (
    <Modal
      isOpen={isOpen}
      onClose={() => {}}
      dismissible={false}
      title={loading ? 'Checking server access' : 'Application access required'}
      footer={
        needsConfigurationRetry || canRetrySavedToken ? (
          <Button variant="primary" onClick={onRetry}>
            Retry connection
          </Button>
        ) : loading ? null : (
          <Button
            variant="primary"
            onClick={handleVerify}
            disabled={verifying || !token.trim()}
          >
            {verifying ? 'Verifying…' : 'Unlock application'}
          </Button>
        )
      }
    >
      <div className="modal-body-stack">
        {loading ? (
          <EmptyState variant="modal">Contacting the local application server…</EmptyState>
        ) : needsConfigurationRetry ? (
          <EmptyState variant="modal" error>
            {error || 'The application server could not be reached.'}
          </EmptyState>
        ) : canRetrySavedToken ? (
          <EmptyState variant="modal" error>
            {error} The saved token was retained; retry when the server is available.
          </EmptyState>
        ) : (
          <>
            <p className="modal-supporting-text">
              Enter the application access token configured by the server
              administrator. This is not an OpenAI API key; OpenAI credentials
              stay on the server and are never entered into this browser.
            </p>
            <label className="input-to-agent-field" htmlFor="application-access-token">
              <span>Application access token</span>
              <TextInput
                id="application-access-token"
                controlSize="lg"
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                onKeyDown={handleKeyDown}
                autoComplete="off"
                spellCheck={false}
                disabled={verifying}
                autoFocus
              />
            </label>
            {error ? (
              <EmptyState variant="modal" error>{error}</EmptyState>
            ) : null}
          </>
        )}
      </div>
    </Modal>
  );
}
