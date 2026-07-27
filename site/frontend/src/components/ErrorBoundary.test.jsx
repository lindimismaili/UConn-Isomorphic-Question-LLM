/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ErrorBoundary from './ErrorBoundary.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const renderError = new Error('private render details');

function ThrowingChild() {
  throw renderError;
}

let container;
let root;

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container, { onCaughtError: () => {} });
  act(() => {
    root.render(element);
  });
}

afterEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
  container = null;
  root = null;
  vi.restoreAllMocks();
});

describe('ErrorBoundary', () => {
  it('renders descendants when they do not fail', () => {
    render(
      <ErrorBoundary>
        <p>Application content</p>
      </ErrorBoundary>,
    );

    expect(container.textContent).toContain('Application content');
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows an accessible fallback without exposing exception details', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert.getAttribute('aria-label')).toBe('Unexpected application error');
    expect(alert.classList.contains('failed-card')).toBe(true);
    expect(alert.textContent).toContain('Something went wrong');
    expect(alert.textContent).toContain('may still be processing on the server');
    expect(alert.textContent).not.toContain(renderError.message);
    expect(alert.querySelector('.modal-empty-state-error')).not.toBeNull();
  });

  it('reports the caught error and runs the injected reload action', () => {
    const onError = vi.fn();
    const onReload = vi.fn();

    render(
      <ErrorBoundary onError={onError} onReload={onReload}>
        <ThrowingChild />
      </ErrorBoundary>,
    );

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBe(renderError);
    expect(onError.mock.calls[0][1].componentStack).toContain('ThrowingChild');

    const reloadButton = [...container.querySelectorAll('button')]
      .find((button) => button.textContent === 'Reload application');
    expect(reloadButton).toBeTruthy();

    act(() => {
      reloadButton.click();
    });
    expect(onReload).toHaveBeenCalledTimes(1);
  });
});
