/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RubricViewerModal from './RubricViewerModal.jsx';

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

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
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
  vi.unstubAllGlobals();
});

describe('RubricViewerModal', () => {
  it('renders rubric JSON in a structured view', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        model: 'gpt-test',
        criteria: [
          {
            name: 'physics_correctness',
            category: 'Quality control',
            question: 'Is the generated question correct?',
            guidance: ['Check equations and units.'],
            scoring_guide: ['90-100: sound physics.'],
          },
        ],
      }),
    }));

    render(<RubricViewerModal isOpen onClose={() => {}} />);
    await flushPromises();

    expect(document.body.textContent).toContain('gpt-test');
    expect(document.body.textContent).toContain('Physics Correctness');
    expect(document.body.textContent).toContain('Quality control');
    expect(document.body.textContent).toContain('Check equations and units.');
    expect(document.body.textContent).toContain('90-100: sound physics.');
  });
});
