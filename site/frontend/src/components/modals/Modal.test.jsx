/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Modal from './Modal.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let container;

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(element);
  });
}

async function flushFrame() {
  await act(async () => {
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
  });
}

afterEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  container?.remove();
  document.body.innerHTML = '';
  root = null;
  container = null;
});

describe('Modal', () => {
  it('does not expose a dialog when closed', () => {
    render(<Modal isOpen={false} onClose={() => {}} title="Closed">Body</Modal>);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('labels the dialog, traps focus, closes on Escape, and restores focus', async () => {
    const opener = document.createElement('button');
    opener.textContent = 'Open';
    document.body.appendChild(opener);
    opener.focus();
    const onClose = vi.fn();

    render(
      <Modal isOpen onClose={onClose} title="Prompt Details">
        <button type="button">First action</button>
        <button type="button">Second action</button>
      </Modal>,
    );
    await flushFrame();

    const dialog = document.querySelector('[role="dialog"]');
    const title = dialog.querySelector('h3');
    expect(dialog.getAttribute('aria-labelledby')).toBe(title.id);
    expect(document.activeElement.getAttribute('aria-label')).toBe('Close');

    const buttons = [...dialog.querySelectorAll('button')];
    buttons.at(-1).focus();
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    });
    expect(document.activeElement).toBe(buttons[0]);

    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    act(() => root.render(<Modal isOpen={false} onClose={onClose} title="Prompt Details">Body</Modal>));
    expect(document.activeElement).toBe(opener);
  });

  it('keeps closing modal mounted without exposing an active dialog', () => {
    render(<Modal isOpen onClose={() => {}} title="Fading">Body</Modal>);

    expect(document.querySelector('[role="dialog"]')).not.toBeNull();

    act(() => {
      root.render(<Modal isOpen={false} onClose={() => {}} title="Fading">Body</Modal>);
    });

    expect(document.querySelector('.modal.is-closing')).not.toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('keeps a required non-dismissible gate open', () => {
    const onClose = vi.fn();
    render(
      <Modal
        isOpen
        onClose={onClose}
        dismissible={false}
        title="Application access required"
      >
        Gate
      </Modal>,
    );

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelector('[aria-label="Close"]')).toBeNull();
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      dialog.querySelector('.modal-backdrop').click();
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});
