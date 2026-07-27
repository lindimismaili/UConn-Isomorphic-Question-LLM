/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BatchEvalModal from './BatchEvalModal.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

function renderModal(batchEval) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <BatchEvalModal
        isOpen
        onClose={() => {}}
        batchEval={batchEval}
        loadRuns={() => {}}
      />,
    );
  });
}

function getButton(label) {
  const button = [...document.querySelectorAll('button')]
    .find((candidate) => candidate.textContent.includes(label));
  expect(button).toBeTruthy();
  return button;
}

function selectFiles(input, files) {
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: files,
  });
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  document.body.innerHTML = '';
  container = null;
  root = null;
});

describe('BatchEvalModal cost confirmation', () => {
  it('discloses isolated upload extraction, retry, bootstrap, and grader calls', () => {
    const start = vi.fn();
    renderModal({
      status: 'idle',
      isStarting: false,
      isRunning: false,
      entries: [],
      availableRuns: [],
      loadingRuns: false,
      policy: {
        maxEntries: 5,
        maxItems: 100,
        graderCount: 8,
        maxUploadExtractionFiles: 6,
        maxUploadExtractionCalls: 12,
        maxUploadBootstrapCalls: 6,
      },
      start,
      stop: vi.fn(),
    });

    act(() => {
      getButton('Upload PDFs').click();
    });
    expect(document.querySelector('[role="tablist"]')).toBeTruthy();
    expect(getButton('Upload PDFs').getAttribute('role')).toBe('tab');

    const fileInputs = [...document.querySelectorAll('input[type="file"]')];
    const sourceFile = new File(['source'], 'source.pdf', {
      type: 'application/pdf',
    });
    const generatedFiles = [
      new File(['generated-a'], 'generated-a.pdf', {
        type: 'application/pdf',
      }),
      new File(['generated-b'], 'generated-b.pdf', {
        type: 'application/pdf',
      }),
    ];
    selectFiles(fileInputs[0], [sourceFile]);
    selectFiles(fileInputs[1], generatedFiles);

    expect(document.body.textContent).toContain(
      '800 grader calls; 3 isolated PDFs require up to 6 extraction agent calls (one retry per PDF) and 3 document-dependency bootstrap calls.',
    );
    expect(document.body.textContent).toContain(
      'I approve up to 800 grader calls, 6 extraction agent calls, and 3 document-dependency bootstrap calls for 3 isolated PDFs',
    );
    expect(document.body.textContent).toContain(
      'Safety moderation and content-classifier checks also run for every extracted PDF; their exact call count varies.',
    );
    expect(document.body.textContent).toContain(
      'I understand that safety moderation and content-classifier checks also run for every extracted PDF',
    );

    const runButton = getButton('Run Batch Eval');
    expect(runButton.disabled).toBe(true);

    act(() => {
      document.querySelector('input[type="checkbox"]').click();
    });
    expect(runButton.disabled).toBe(false);

    act(() => {
      runButton.click();
    });
    expect(start).toHaveBeenCalledWith({
      mode: 'upload',
      sourceFile,
      generatedFiles,
      confirmGraderCalls: true,
    });
  });

  it('keeps prior-run confirmation limited to grader calls', () => {
    renderModal({
      status: 'idle',
      isStarting: false,
      isRunning: false,
      entries: [],
      availableRuns: [
        {
          folder: 'prior-run',
          label: 'Prior run',
          questionCount: 2,
          hasEval: false,
        },
      ],
      loadingRuns: false,
      policy: {
        maxEntries: 5,
        maxItems: 100,
        graderCount: 8,
        maxUploadExtractionFiles: 6,
        maxUploadExtractionCalls: 12,
        maxUploadBootstrapCalls: 6,
      },
      start: vi.fn(),
      stop: vi.fn(),
    });

    act(() => {
      document.querySelector('.batch-eval-run-row input').click();
    });

    expect(document.body.textContent).toContain(
      '2 questions × 8 rubric graders = 16 grader calls.',
    );
    expect(document.body.textContent).not.toContain('extraction agent calls');
    expect(document.body.textContent).not.toContain('bootstrap calls');
  });

  it('shows server-projected upload work while the batch is running', () => {
    renderModal({
      batchId: 'eval-upload',
      status: 'running',
      mode: 'upload',
      isStarting: false,
      isRunning: true,
      totalFiles: 2,
      doneFiles: 0,
      completedFiles: 0,
      failedFiles: 0,
      cancelledFiles: 0,
      blockedFiles: 0,
      entries: [],
      availableRuns: [],
      loadingRuns: false,
      projectedGraderCalls: 32,
      projectedExtractionFiles: 3,
      projectedExtractionCalls: 6,
      projectedBootstrapCalls: 3,
      start: vi.fn(),
      stop: vi.fn(),
    });

    expect(document.body.textContent).toContain(
      'Server projection: up to 32 grader calls; 3 isolated PDFs; up to 6 extraction agent calls; 3 document-dependency bootstrap calls.',
    );
    expect(document.body.textContent).toContain(
      'Safety moderation and content-classifier checks also run for every extracted PDF; their exact call count varies.',
    );
  });

  it('offers an explicit reset after a terminal batch', () => {
    const reset = vi.fn(() => true);
    renderModal({
      batchId: null,
      status: 'completed',
      mode: 'prior_runs',
      isStarting: false,
      isRunning: false,
      totalFiles: 1,
      doneFiles: 1,
      completedFiles: 1,
      failedFiles: 0,
      cancelledFiles: 0,
      blockedFiles: 0,
      outputFolder: '',
      entries: [],
      availableRuns: [],
      loadingRuns: false,
      start: vi.fn(),
      stop: vi.fn(),
      reset,
    });

    expect(document.body.textContent).not.toContain('Run Batch Eval');
    act(() => {
      getButton('New Batch Evaluation').click();
    });
    expect(reset).toHaveBeenCalledOnce();
  });
});
