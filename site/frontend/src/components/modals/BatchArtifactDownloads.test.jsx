/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BatchEvalModal from './BatchEvalModal.jsx';
import CompareProfessorPanel from './CompareProfessorPanel.jsx';
import CreateDataModal from './CreateDataModal.jsx';
import { downloadApiFile, requestJson } from '../../utils/api.js';

vi.mock('../../utils/api.js', () => ({
  downloadApiFile: vi.fn(),
  requestJson: vi.fn(),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(element));
}

async function clickButton(label) {
  const button = [...document.querySelectorAll('button')]
    .find((candidate) => candidate.textContent.includes(label));
  expect(button).toBeTruthy();
  await act(async () => {
    button.click();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  downloadApiFile.mockReset().mockResolvedValue(undefined);
  requestJson.mockReset();
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  document.body.innerHTML = '';
  container = null;
  root = null;
});

describe('batch artifact downloads', () => {
  it('downloads a terminal Create Data batch through the protected API', async () => {
    render(
      <CreateDataModal
        isOpen
        onClose={() => {}}
        onRun={() => {}}
        onStop={() => {}}
        createData={{
          batchId: 'create-id',
          status: 'completed',
          outputFolder: '07-27-2026/create-run',
          totalFiles: 1,
          doneFiles: 1,
          completedFiles: 1,
          failedFiles: 0,
          cancelledFiles: 0,
          blockedFiles: 0,
          entries: [],
        }}
      />,
    );

    await clickButton('Download Batch ZIP');

    expect(downloadApiFile).toHaveBeenCalledWith(
      '/api/download-zip/create-id',
      'create-run.zip',
    );
  });

  it('downloads a terminal Eval Batch through the protected API', async () => {
    render(
      <BatchEvalModal
        isOpen
        onClose={() => {}}
        loadRuns={() => {}}
        batchEval={{
          batchId: 'eval-id',
          status: 'completed',
          mode: 'prior_runs',
          outputFolder: '07-27-2026/eval-run',
          totalFiles: 1,
          doneFiles: 1,
          completedFiles: 1,
          failedFiles: 0,
          cancelledFiles: 0,
          blockedFiles: 0,
          entries: [],
          availableRuns: [],
          policy: { maxEntries: 5, maxItems: 100, graderCount: 8 },
          start: () => {},
          stop: () => {},
        }}
      />,
    );

    await clickButton('Download Batch ZIP');

    expect(downloadApiFile).toHaveBeenCalledWith(
      '/api/download-zip/eval-id',
      'eval-run.zip',
    );
  });

  it('downloads the generated professor workbook without exposing a server path', async () => {
    requestJson.mockResolvedValue({
      outputFolder: '07-27-2026/eval-run',
      xlsxPath: 'XLSX/compiled.xlsx',
      summary: {
        mean_kappa: 0.8,
        mean_raw_agreement: 0.9,
        criteria_with_poor_kappa: [],
      },
      pdfsCompared: ['quiz'],
    });
    render(
      <CompareProfessorPanel outputFolder="07-27-2026/eval-run" />,
    );

    await clickButton('Compare to Professor');
    await clickButton('Download Workbook');

    expect(downloadApiFile).toHaveBeenCalledWith(
      '/api/output-artifact?folder=07-27-2026%2Feval-run&file=XLSX%2Fcompiled.xlsx',
      'XLSX/compiled.xlsx',
    );
  });
});
