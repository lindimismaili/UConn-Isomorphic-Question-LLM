/* @vitest-environment jsdom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useFileContent from '../../hooks/useFileContent.js';
import {
  getDerivedRegistry,
  useAgentRegistry,
} from '../../hooks/useAgentRegistry';
import FinalResultModal from './FinalResultModal.jsx';
import HelpModal from './HelpModal.jsx';
import WorkflowAgentsModal from './WorkflowAgentsModal.jsx';

vi.mock('../../hooks/useFileContent.js', () => ({
  default: vi.fn(),
}));

vi.mock('../../hooks/useAgentRegistry', () => ({
  getDerivedRegistry: vi.fn(),
  useAgentRegistry: vi.fn(),
}));

vi.mock('./PromptViewerModal.jsx', () => ({
  default: () => null,
}));

vi.mock('./RubricViewerModal.jsx', () => ({
  default: () => null,
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

function clickButton(label) {
  const button = [...document.querySelectorAll('button')]
    .find((candidate) => candidate.textContent.trim() === label);
  expect(button).toBeTruthy();
  act(() => button.click());
}

beforeEach(() => {
  useFileContent.mockReset();
  useAgentRegistry.mockReset();
  getDerivedRegistry.mockReset();
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  document.body.innerHTML = '';
  container = null;
  root = null;
});

describe('FinalResultModal', () => {
  it('renders the report overall score on its native 0-100 scale', () => {
    useFileContent.mockReturnValue({
      text: JSON.stringify({
        config: {},
        source_questions: [],
        generated_questions: [],
        evaluation: {
          overall_score: 95,
          criterion_scores: {},
        },
      }),
      loading: false,
      error: null,
    });

    render(
      <FinalResultModal
        isOpen
        onClose={() => {}}
        fileName="final_result.json"
        url="/api/result"
      />,
    );
    clickButton('Evaluation');

    expect(document.querySelector('.viewer-eval-score').textContent).toBe('95%');
  });

  it('shows an actionable error when the results artifact is invalid JSON', () => {
    useFileContent.mockReturnValue({
      text: '{not-json',
      loading: false,
      error: null,
    });

    render(
      <FinalResultModal
        isOpen
        onClose={() => {}}
        fileName="final_result.json"
        url="/api/result"
        onDownload={() => {}}
      />,
    );

    const error = document.querySelector('.modal-empty-state-error');
    expect(error.textContent).toContain('not valid JSON');
    expect(error.textContent).toContain('Download the file');
    expect([...document.querySelectorAll('button')].some((button) => (
      button.textContent.trim() === 'Download'
    ))).toBe(true);
  });
});

describe('WorkflowAgentsModal', () => {
  it('shows an actionable error when agent metadata is invalid JSON', () => {
    useFileContent.mockImplementation((url) => ({
      text: url === '/api/metadata' ? '{not-json' : '{}',
      loading: false,
      error: null,
    }));
    useAgentRegistry.mockReturnValue({ pipelineKeys: [] });
    getDerivedRegistry.mockReturnValue({ pipelineKeys: [], registry: {}, agentLabels: {} });

    render(
      <WorkflowAgentsModal
        isOpen
        onClose={() => {}}
        fileName="agent_metadata_output.json"
        url="/api/metadata"
        finalResultUrl="/api/result"
      />,
    );

    const error = document.querySelector('.modal-empty-state-error');
    expect(error.textContent).toContain('agent metadata file is not valid JSON');
    expect(error.textContent).toContain('Download the file');
  });

  it('derives all question-count summaries from registry metadata', () => {
    const registryData = {
      pipelineKeys: ['reader', 'checker', 'writer'],
      agentLabels: {
        reader: 'Reader',
        checker: 'Checker',
        writer: 'Writer',
      },
      registry: {
        reader: {
          name: 'Reader',
          countLabel: 'Extracted',
          resultField: 'source_questions',
        },
        checker: {
          name: 'Checker',
          countLabel: 'Verified',
          resultField: 'verified_questions',
        },
        writer: {
          name: 'Writer',
          countLabel: 'Generated',
          resultField: 'generated_questions',
        },
      },
    };
    useAgentRegistry.mockReturnValue(registryData);
    getDerivedRegistry.mockReturnValue(registryData);
    useFileContent.mockImplementation((url) => ({
      text: JSON.stringify(
        url === '/api/metadata'
          ? {
              workflow_id: 'workflow-id',
              agents: [
                {
                  agent_key: 'reader',
                  agent_name: 'Reader',
                  status: 'completed',
                  questions_count: 2,
                },
              ],
            }
          : {
              source_questions: [{}],
              verified_questions: [{}, {}, {}, {}],
              generated_questions: [{}, {}, {}],
            },
      ),
      loading: false,
      error: null,
    }));

    render(
      <WorkflowAgentsModal
        isOpen
        onClose={() => {}}
        fileName="agent_metadata_output.json"
        url="/api/metadata"
        finalResultUrl="/api/result"
        onViewFile={() => {}}
      />,
    );

    const summaries = [...document.querySelectorAll('.workflow-agents-summary-item')]
      .map((item) => ({
        label: item.querySelector('label')?.textContent,
        value: item.querySelector('span')?.textContent,
      }));
    expect(summaries).toEqual(expect.arrayContaining([
      { label: 'Questions Extracted', value: '2' },
      { label: 'Questions Verified', value: '4' },
      { label: 'Questions Generated', value: '3' },
    ]));
  });
});

describe('HelpModal', () => {
  it('describes multiple-choice output and the canonical LaTeX template', () => {
    render(<HelpModal isOpen onClose={() => {}} />);

    const text = document.querySelector('[role="dialog"]').textContent;
    expect(text).toContain('multiple-choice physics questions');
    expect(text).not.toContain('open-ended');
    expect(text).not.toContain('multi-part');
    expect(text).toContain('backend/data/latex_template.tex');
  });
});
