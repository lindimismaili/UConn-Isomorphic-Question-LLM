import { useReducer, useEffect, useState } from 'react';
import './styles/app.css';

import PromptInput from './components/PromptInput.jsx';
import AgentPipeline from './components/AgentPipeline.jsx';
import LogPanel from './components/LogPanel.jsx';
import ResultsSection from './components/ResultsSection.jsx';
import FailureSection from './components/FailureSection.jsx';

import FilesModal from './components/modals/FilesModal.jsx';
import AgentDetailModal from './components/modals/AgentDetailModal.jsx';
import SetupModal from './components/modals/SetupModal.jsx';
import HelpModal from './components/modals/HelpModal.jsx';
import SkillsModal from './components/modals/SkillsModal.jsx';

import { useWorkflow } from './hooks/useWorkflow.js';
import { useAgents } from './hooks/useAgents.js';

// =============================================================================
// Initial State
// =============================================================================

const INITIAL_AGENTS = {
  setup: {
    name: 'Setup',
    description: 'Setting up environment',
    model: 'Unavailable',
    reasoningLevel: 'Unavailable',
    status: 'inactive',
    startTime: null, endTime: null, executionTime: null,
    inputFrom: 'User', outputTo: 'Normalizer',
    inputText: '', outputText: '',
  },
  normalizer: {
    name: 'Unavailable',
    description: 'Unavailable',
    model: 'Unavailable',
    reasoningLevel: 'Unavailable',
    status: 'inactive',
    startTime: null, endTime: null, executionTime: null,
    inputFrom: 'User', outputTo: 'Extracter',
    inputText: '', outputText: '',
  },
  extracter: {
    name: 'Unavailable',
    description: 'Unavailable',
    model: 'Unavailable',
    reasoningLevel: 'Unavailable',
    status: 'inactive',
    startTime: null, endTime: null, executionTime: null,
    inputFrom: 'Normalizer', outputTo: 'Verifier',
    inputText: '', outputText: '',
  },
  verifier: {
    name: 'Unavailable',
    description: 'Unavailable',
    model: 'Unavailable',
    reasoningLevel: 'Unavailable',
    status: 'inactive',
    startTime: null, endTime: null, executionTime: null,
    inputFrom: 'Extracter', outputTo: 'Generator',
    inputText: '', outputText: '',
  },
  generator: {
    name: 'Unavailable',
    description: 'Unavailable',
    model: 'Unavailable',
    reasoningLevel: 'Unavailable',
    status: 'inactive',
    startTime: null, endTime: null, executionTime: null,
    inputFrom: 'Verifier', outputTo: 'LaTeX Formatter',
    inputText: '', outputText: '',
  },
  latex: {
    name: 'Unavailable',
    description: 'Unavailable',
    model: 'Unavailable',
    reasoningLevel: 'Unavailable',
    status: 'inactive',
    startTime: null, endTime: null, executionTime: null,
    inputFrom: 'Generator', outputTo: 'User',
    inputText: '', outputText: '',
  },
};

const MAX_LOGS = 250;

function makeLog(message, level) {
  return {
    id: Math.random().toString(36).slice(2),
    time: new Date().toLocaleTimeString(),
    message,
    level,
  };
}

const INITIAL_STATE = {
  files: [],
  agents: { ...INITIAL_AGENTS },
  isRunning: false,
  workflowId: null,
  logs: [],
  result: null,
  error: null,
  errorFolder: '',
  activeModal: null,
  activeAgentKey: null,
  pipelineVisible: false,
};

// =============================================================================
// Reducer
// =============================================================================

function resetAgents(agents) {
  return Object.fromEntries(
    Object.entries(agents).map(([key, agent]) => [
      key,
      { ...agent, status: 'inactive', startTime: null, endTime: null, executionTime: null, inputText: '', outputText: '' },
    ])
  );
}

function queueAgents(agents, excludeKeys = []) {
  const exclude = new Set(excludeKeys);
  return Object.fromEntries(
    Object.entries(agents).map(([key, agent]) => [
      key,
      exclude.has(key) ? agent : { ...agent, status: 'queued' },
    ])
  );
}

function reducer(state, action) {
  switch (action.type) {
    case 'ADD_FILES':
      return { ...state, files: [...state.files, ...action.files] };
    case 'REMOVE_FILE':
      return { ...state, files: state.files.filter((f) => f.id !== action.fileId) };
    case 'CLEAR_FILES':
      return { ...state, files: [] };
    case 'UPLOAD_WARNING':
      return state;

    case 'OPEN_MODAL':
      return { ...state, activeModal: action.modal, activeAgentKey: action.agentKey || null };
    case 'CLOSE_MODAL':
      return { ...state, activeModal: null, activeAgentKey: null };

    case 'AGENTS_LOADED': {
      const updatedAgents = { ...state.agents };
      for (const [key, config] of Object.entries(action.config)) {
        if (updatedAgents[key]) {
          updatedAgents[key] = {
            ...updatedAgents[key],
            name: config.name || updatedAgents[key].name,
            model: config.model || updatedAgents[key].model,
            reasoningLevel: config.reasoning || updatedAgents[key].reasoningLevel,
            description: config.description || '',
            inputFrom: config.inputFrom || updatedAgents[key].inputFrom,
            outputTo: config.outputTo || updatedAgents[key].outputTo,
          };
        }
      }
      return { ...state, agents: updatedAgents };
    }

    case 'WORKFLOW_START': {
      const agents = resetAgents(state.agents);
      const withSetup = {
        ...agents,
        setup: { ...agents.setup, status: 'thinking', startTime: new Date() },
      };
      const queued = queueAgents(withSetup, ['setup']);
      return {
        ...state,
        agents: queued,
        isRunning: true,
        workflowId: null,
        result: null,
        error: null,
        errorFolder: '',
        files: [],
        pipelineVisible: true,
        logs: [...state.logs, makeLog('Process starting...', 'info'), makeLog('Setup started', 'info')].slice(-MAX_LOGS),
      };
    }

    case 'SETUP_COMPLETE': {
      const setup = state.agents.setup;
      const setupStart = setup.startTime ? new Date(setup.startTime).getTime() : null;
      const setupEnd = Date.now();
      const setupSeconds = setupStart !== null ? (setupEnd - setupStart) / 1000 : null;
      const setupMessage = setupSeconds !== null
        ? `Setup completed in ${setupSeconds.toFixed(2)}s`
        : 'Setup completed';
      return {
        ...state,
        workflowId: action.workflowId,
        agents: {
          ...state.agents,
          setup: {
            ...setup,
            status: 'completed',
            endTime: new Date(setupEnd),
            executionTime: setupSeconds,
          },
        },
        logs: [...state.logs, makeLog(setupMessage, 'success')].slice(-MAX_LOGS),
      };
    }

    case 'AGENT_UPDATE': {
      const agent = state.agents[action.agentKey];
      if (!agent) return state;
      return {
        ...state,
        agents: {
          ...state.agents,
          [action.agentKey]: { ...agent, status: action.status, ...action.data },
        },
      };
    }

    case 'WORKFLOW_COMPLETE':
      {
      const elapsed = typeof action.elapsedTimeSeconds === 'number'
        ? ` in ${action.elapsedTimeSeconds.toFixed(2)}s`
        : '';
      return {
        ...state,
        isRunning: false,
        result: action.result,
        error: null,
        logs: [...state.logs, makeLog(`Workflow completed${elapsed}`, 'success')].slice(-MAX_LOGS),
      };
      }

    case 'WORKFLOW_ERROR': {
      const now = Date.now();
      const errorLogs = [];
      const agents = Object.fromEntries(
        Object.entries(state.agents).map(([key, agent]) => [
          key,
          agent.status === 'thinking'
            ? (() => {
                const startMs = agent.startTime ? new Date(agent.startTime).getTime() : null;
                const elapsed = startMs !== null ? (now - startMs) / 1000 : null;
                const suffix = elapsed !== null ? ` after ${elapsed.toFixed(2)}s` : '';
                errorLogs.push(makeLog(`${agent.name || key} errored${suffix}`, 'error'));
                return { ...agent, status: 'errored' };
              })()
            : agent,
        ])
      );
      return {
        ...state,
        isRunning: false,
        agents,
        error: `Workflow failed before completion.\n${action.message || ''}`.trim(),
        result: null,
        logs: [
          ...state.logs,
          ...errorLogs,
          makeLog(`Workflow error: ${action.message || 'Unknown error'}`, 'error'),
        ].slice(-MAX_LOGS),
      };
    }

    case 'WORKFLOW_ABORTED':
      return { ...state, isRunning: false };

    case 'CLEAR_INPUTS':
      return { ...state, files: [], result: null, error: null };
    case 'CLEAR_AGENTS':
      return { ...state, agents: resetAgents(state.agents), result: null, error: null };

    case 'LOG':
      return {
        ...state,
        logs: [...state.logs, makeLog(action.message, action.level)].slice(-MAX_LOGS),
      };

    default:
      return state;
  }
}

// =============================================================================
// App Component
// =============================================================================

export default function App() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [footerVersion, setFooterVersion] = useState('Version -');

  const { run: runWorkflow, abort: abortWorkflow } = useWorkflow(dispatch);
  useAgents(dispatch);

  useEffect(() => {
    fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return;
        const version = data.version || 'unknown';
        const publishIso = data.commit_time || data.published_at;
        if (publishIso) {
          const d = new Date(publishIso);
          const text = isNaN(d.getTime()) ? publishIso : d.toLocaleString();
          setFooterVersion(`Version ${version} | Last publish: ${text}`);
        } else {
          setFooterVersion(`Version ${version}`);
        }
      })
      .catch(() => setFooterVersion('Version dev | Last publish: local'));
  }, []);

  const handleSend = (prompt, files) => runWorkflow(prompt, files);

  const handleClearInputs = () => {
    abortWorkflow();
    dispatch({ type: 'CLEAR_INPUTS' });
  };

  const handleClearAgents = () => {
    abortWorkflow();
    dispatch({ type: 'CLEAR_AGENTS' });
  };

  const handleAgentCardClick = (agentKey) => {
    const modal = agentKey === 'setup' ? 'setup' : 'agent';
    dispatch({ type: 'OPEN_MODAL', modal, agentKey });
  };

  const closeModal = () => dispatch({ type: 'CLOSE_MODAL' });

  return (
    <>
      <header className="header">
        <div className="header-content">
          <div className="logo">
            <span className="logo-icon logo-icon-ai" aria-hidden="true" />
            <h1>Isomorphic Question Generator</h1>
          </div>
          <div className="uconn-mark" aria-label="UCONN brand">
            <div className="uconn-text">UCONN</div>
            <div className="uconn-subtext">Physics Research</div>
          </div>
        </div>
      </header>

      <main className="main-content">
        <PromptInput
          files={state.files}
          dispatch={dispatch}
          isRunning={state.isRunning}
          onSend={handleSend}
          onClearInputs={handleClearInputs}
          onClearAgents={handleClearAgents}
          onShowFiles={() => dispatch({ type: 'OPEN_MODAL', modal: 'files' })}
        />

        {state.pipelineVisible && (
          <section className="agents-section">
            <div className="pipeline-split">
              <div className="pipeline-panel agents-panel">
                <h3 className="section-title">Agent Pipeline</h3>
                <p className="section-subtitle">Click a block to view more information</p>
                <AgentPipeline agents={state.agents} onCardClick={handleAgentCardClick} />
              </div>
              <LogPanel logs={state.logs} />
            </div>
          </section>
        )}

        {state.result && <ResultsSection result={state.result} />}
        {state.error && <FailureSection error={state.error} outputFolder={state.errorFolder} />}
      </main>

      <footer className="footer">
        <div className="footer-content">
          <p className="footer-primary">UCONN Physics Research - Dr. Xian Wu (2025 - 2026)</p>
          <p className="footer-primary">Created by Lindim Ismaili</p>
          <p className="footer-secondary">{footerVersion}</p>
        </div>
      </footer>

      <FilesModal
        isOpen={state.activeModal === 'files'}
        onClose={closeModal}
        files={state.files}
        onRemove={(id) => dispatch({ type: 'REMOVE_FILE', fileId: id })}
      />
      <AgentDetailModal
        isOpen={state.activeModal === 'agent'}
        onClose={closeModal}
        agentKey={state.activeAgentKey}
        agent={state.activeAgentKey ? state.agents[state.activeAgentKey] : null}
      />
      <SetupModal isOpen={state.activeModal === 'setup'} onClose={closeModal} />
      <HelpModal isOpen={state.activeModal === 'help'} onClose={closeModal} />
      <SkillsModal isOpen={state.activeModal === 'skills'} onClose={closeModal} />
    </>
  );
}
