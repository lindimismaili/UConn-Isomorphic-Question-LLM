/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AccessGateModal from './AccessGateModal.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  document.body.innerHTML = '';
  container = null;
  root = null;
});

describe('AccessGateModal', () => {
  it('offers retry without re-entering a token retained after a transient failure', () => {
    const retry = vi.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <AccessGateModal
          isOpen
          loading={false}
          verifying={false}
          configAvailable
          hasSavedToken
          error="The saved application access token could not be verified."
          onVerify={() => {}}
          onRetry={retry}
        />,
      );
    });

    expect(document.querySelector('input[type="password"]')).toBeNull();
    const retryButton = [...document.querySelectorAll('button')]
      .find((button) => button.textContent.trim() === 'Retry connection');
    expect(retryButton).toBeTruthy();
    act(() => retryButton.click());
    expect(retry).toHaveBeenCalledOnce();
    expect(document.body.textContent).toContain('saved token was retained');
  });
});
