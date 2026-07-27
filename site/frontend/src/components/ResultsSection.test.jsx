/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { downloadApiFile } from '../utils/api.js';
import ResultsSection from './ResultsSection.jsx';

vi.mock('../utils/api.js', async (importOriginal) => ({
  ...(await importOriginal()),
  downloadApiFile: vi.fn(),
}));

vi.mock('../hooks/useAgentRegistry', () => {
  const registryData = {
    pipelineKeys: [],
    registry: {},
    agentLabels: {},
  };
  return {
    getDerivedRegistry: () => registryData,
    useAgentRegistry: () => registryData,
  };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;
let originalScrollIntoView;

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(element));
}

async function click(element) {
  await act(async () => {
    element.click();
    await Promise.resolve();
  });
}

beforeEach(() => {
  downloadApiFile.mockReset().mockResolvedValue(undefined);
  originalScrollIntoView = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  document.body.innerHTML = '';
  Element.prototype.scrollIntoView = originalScrollIntoView;
  container = null;
  root = null;
});

describe('ResultsSection protected downloads', () => {
  it('uses the shared transport and stable basenames for files and workflow ZIPs', async () => {
    render(
      <ResultsSection
        mode="dev"
        result={{
          workflowId: 'workflow-id',
          outputFolder: '07-27-2026/run-label',
          files: [{ name: 'reports/result.pdf' }],
          workflowResult: {},
        }}
      />,
    );

    const fileDownload = document.querySelector('.download-item .btn-download');
    await click(fileDownload);
    const zipDownload = [...document.querySelectorAll('button')]
      .find((button) => button.textContent.trim() === 'Download .ZIP');
    await click(zipDownload);

    expect(downloadApiFile).toHaveBeenNthCalledWith(
      1,
      '/api/download/workflow-id/reports/result.pdf',
      'result.pdf',
    );
    expect(downloadApiFile).toHaveBeenNthCalledWith(
      2,
      '/api/download-zip/workflow-id',
      'run-label.zip',
    );
  });
});
