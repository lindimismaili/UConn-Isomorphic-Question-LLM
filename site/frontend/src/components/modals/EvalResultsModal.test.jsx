/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EvalResultsModal from './EvalResultsModal.jsx';

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('EvalResultsModal', () => {
  it('renders input and output text from datasource_item JSONL records', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(JSON.stringify({
        datasource_item: {
          source_id: 'q1',
          source_question_text: 'Source question text',
          generated_question_text: 'Generated question text',
        },
        results: [{ name: 'physics_correctness', score: 95, passed: true }],
      })),
    }));

    render(
      <EvalResultsModal
        isOpen
        onClose={() => {}}
        report={{
          status: 'passed',
          passing_score_threshold: 70,
          rubric: [{ name: 'physics_correctness' }],
        }}
        itemsUrl="/eval-items.jsonl"
        itemsFileName="evaluation_outputs/evaluation_agents.jsonl"
        reportFileName="evaluation_outputs/evaluation_report.json"
        onDownload={() => {}}
      />,
    );

    await flushPromises();

    expect(document.body.textContent).toContain('Source question text');
    expect(document.body.textContent).toContain('Generated question text');
    expect(document.body.textContent).not.toContain('No input text');
    expect(document.body.textContent).not.toContain('No output text');
  });
});
