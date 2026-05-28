import { useReducer, useEffect, useState } from "react";
import "./styles/app.css";

import PromptInput from "./components/PromptInput.jsx";
import DeveloperToolsPanel from "./components/DeveloperToolsPanel.jsx";
import AgentPipeline from "./components/AgentPipeline.jsx";
import LogPanel from "./components/LogPanel.jsx";
import ResultsSection from "./components/ResultsSection.jsx";
import FailureSection from "./components/FailureSection.jsx";

import FilesModal from "./components/modals/FilesModal.jsx";
import TestFilesModal from "./components/modals/TestFilesModal.jsx";
import CreateDataModal from "./components/modals/CreateDataModal.jsx";
import AgentDetailModal from "./components/modals/AgentDetailModal.jsx";
import SetupModal from "./components/modals/SetupModal.jsx";
import OutputModal from "./components/modals/OutputModal.jsx";
import HelpModal from "./components/modals/HelpModal.jsx";
import InputToAgentModal from "./components/modals/InputToAgentModal.jsx";

import { useWorkflow } from "./hooks/useWorkflow.js";
import { useAgents } from "./hooks/useAgents.js";
import { useCreateData } from "./hooks/useCreateData.js";

// =============================================================================
// Initial State
// =============================================================================

/**
 * Baseline agent/runtime metadata rendered before the backend loads config.
 */
const INITIAL_AGENTS = {
  setup: {
    name: "Starter",
    description: "Prepares the shared workflow runtime",
    model: "OpenAI Runtime",
    reasoningLevel: "Unavailable",
    kind: "runtime",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "User",
    outputTo: "Normalizer",
    inputText: "",
    outputText: "",
  },
  normalizer: {
    name: "Unavailable",
    description: "Unavailable",
    model: "Unavailable",
    reasoningLevel: "Unavailable",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "User",
    outputTo: "Extracter",
    inputText: "",
    outputText: "",
  },
  extracter: {
    name: "Unavailable",
    description: "Unavailable",
    model: "Unavailable",
    reasoningLevel: "Unavailable",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "Normalizer",
    outputTo: "Verifier",
    inputText: "",
    outputText: "",
  },
  verifier: {
    name: "Unavailable",
    description: "Unavailable",
    model: "Unavailable",
    reasoningLevel: "Unavailable",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "Extracter",
    outputTo: "Generator",
    inputText: "",
    outputText: "",
  },
  generator: {
    name: "Unavailable",
    description: "Unavailable",
    model: "Unavailable",
    reasoningLevel: "Unavailable",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "Verifier",
    outputTo: "Latexifier",
    inputText: "",
    outputText: "",
  },
  latexifier: {
    name: "Unavailable",
    description: "Unavailable",
    model: "Unavailable",
    reasoningLevel: "Unavailable",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "Generator",
    outputTo: "Evaluator",
    inputText: "",
    outputText: "",
  },
  evaluation: {
    name: "Evaluator",
    description: "Scores generated outputs and writes evaluation artifacts",
    model: "Unavailable",
    reasoningLevel: "low",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "Latexifier",
    outputTo: "Output",
    inputText: "",
    outputText: "",
  },
  output: {
    name: "Output",
    description: "Finalizes workflow files and result metadata",
    model: "Workflow Runtime",
    reasoningLevel: "Unavailable",
    kind: "runtime",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "Evaluator",
    outputTo: "User",
    inputText: "",
    outputText: "",
  },
};

/** Maximum number of log entries retained in memory and rendered. */
const MAX_LOGS = 250;
const NEXT_AGENT_BY_KEY = {
  setup: "normalizer",
  normalizer: "extracter",
  extracter: "verifier",
  verifier: "generator",
  generator: "latexifier",
  latexifier: "evaluation",
  evaluation: "output",
};

/**
 * Returns the first value in the list that is neither empty nor nullish.
 *
 * @param {...unknown} values
 * @returns {unknown}
 */
function firstDefined(...values) {
  return values.find(
    (value) => value !== undefined && value !== null && value !== "",
  );
}

/**
 * Builds a UI log entry with a timestamp and stable client-side id.
 *
 * @param {string} message
 * @param {string} level
 * @returns {{id: string, time: string, message: string, level: string}}
 */
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
  logs: [makeLog("User connection established.", "info")],
  result: null,
  error: null,
  errorFolder: "",
  activeModal: null,
  activeAgentKey: null,
};

// =============================================================================
// Reducer
// =============================================================================

/**
 * Resets every agent to the inactive UI state.
 *
 * @param {object} agents
 * @returns {object}
 */
function resetAgents(agents) {
  return Object.fromEntries(
    Object.entries(agents).map(([key, agent]) => [
      key,
      {
        ...agent,
        status: "inactive",
        startTime: null,
        endTime: null,
        executionTime: null,
        inputText: "",
        outputText: "",
      },
    ]),
  );
}

/**
 * Marks all agents as queued except the keys explicitly excluded.
 *
 * @param {object} agents
 * @param {string[]} excludeKeys
 * @returns {object}
 */
function queueAgents(agents, excludeKeys = []) {
  const exclude = new Set(excludeKeys);
  return Object.fromEntries(
    Object.entries(agents).map(([key, agent]) => [
      key,
      exclude.has(key) ? agent : { ...agent, status: "queued" },
    ]),
  );
}

/**
 * Central UI reducer for workflow, file, log, and modal state.
 *
 * @param {object} state
 * @param {{type: string}} action
 * @returns {object}
 */
function reducer(state, action) {
  switch (action.type) {
    case "ADD_FILES":
      return { ...state, files: [...state.files, ...action.files] };
    case "REMOVE_FILE":
      return {
        ...state,
        files: state.files.filter((f) => f.id !== action.fileId),
      };
    case "CLEAR_FILES":
      return { ...state, files: [] };
    case "UPLOAD_WARNING":
      return state;

    case "OPEN_MODAL":
      return {
        ...state,
        activeModal: action.modal,
        activeAgentKey: action.agentKey || null,
      };
    case "CLOSE_MODAL":
      return { ...state, activeModal: null, activeAgentKey: null };

    case "AGENTS_LOADED": {
      const updatedAgents = { ...state.agents };
      for (const [key, config] of Object.entries(action.config)) {
        if (updatedAgents[key]) {
          const reasoningLevel = firstDefined(
            config.reasoning,
            config.reasoningLevel,
            config.reasoning_level,
            updatedAgents[key].reasoningLevel,
          );
          updatedAgents[key] = {
            ...updatedAgents[key],
            name: firstDefined(config.name, updatedAgents[key].name),
            model: firstDefined(config.model, updatedAgents[key].model),
            reasoningLevel,
            description: firstDefined(
              config.description,
              updatedAgents[key].description,
            ),
            inputFrom: firstDefined(config.inputFrom, updatedAgents[key].inputFrom),
            outputTo: firstDefined(config.outputTo, updatedAgents[key].outputTo),
            kind: firstDefined(
              config.kind,
              config.type,
              config.nodeKind,
              updatedAgents[key].kind,
            ),
          };
        }
      }
      return { ...state, agents: updatedAgents };
    }

    case "WORKFLOW_START": {
      const agents = resetAgents(state.agents);
      const withSetup = {
        ...agents,
        setup: { ...agents.setup, status: "thinking", startTime: new Date() },
      };
      const queued = queueAgents(withSetup, ["setup"]);
      return {
        ...state,
        agents: queued,
        isRunning: true,
        workflowId: null,
        result: null,
        error: null,
        errorFolder: "",
        files: [],
        logs: [
          ...state.logs,
          makeLog("------------------------------", "info"),
          makeLog("Process starting...", "info"),
          makeLog("Starter started", "info"),
        ].slice(-MAX_LOGS),
      };
    }

    case "SETUP_COMPLETE": {
      const setup = state.agents.setup;
      const setupStart = setup.startTime
        ? new Date(setup.startTime).getTime()
        : null;
      const setupEnd = Date.now();
      const setupSeconds =
        setupStart !== null ? (setupEnd - setupStart) / 1000 : null;
      const setupMessage =
        setupSeconds !== null
          ? `Starter completed in ${setupSeconds.toFixed(2)}s`
          : "Starter completed";
      const normalizer = state.agents.normalizer;
      const shouldStartNormalizerNow =
        normalizer &&
        (normalizer.status === "queued" || normalizer.status === "inactive");
      return {
        ...state,
        workflowId: action.workflowId,
        agents: {
          ...state.agents,
          setup: {
            ...setup,
            status: "completed",
            endTime: new Date(setupEnd),
            executionTime: setupSeconds,
          },
          ...(shouldStartNormalizerNow
            ? {
                normalizer: {
                  ...normalizer,
                  status: "thinking",
                  startTime: normalizer.startTime || new Date(setupEnd),
                  endTime: null,
                },
              }
            : {}),
        },
        logs: [...state.logs, makeLog(setupMessage, "success")].slice(
          -MAX_LOGS,
        ),
      };
    }

    case "AGENT_UPDATE": {
      const agent = state.agents[action.agentKey];
      if (!agent) return state;
      const patch = Object.fromEntries(
        Object.entries(action.data || {}).filter(
          ([, value]) => value !== undefined,
        ),
      );
      const nextAgents = {
        ...state.agents,
        [action.agentKey]: { ...agent, ...patch, status: action.status },
      };
      if (action.status === "completed") {
        const nextKey = NEXT_AGENT_BY_KEY[action.agentKey];
        const nextAgent = nextKey ? nextAgents[nextKey] : null;
        if (
          nextAgent &&
          (nextAgent.status === "queued" || nextAgent.status === "inactive")
        ) {
          nextAgents[nextKey] = {
            ...nextAgent,
            status: "thinking",
            startTime: nextAgent.startTime || new Date(),
            endTime: null,
          };
        }
      }
      return {
        ...state,
        agents: nextAgents,
      };
    }

    case "WORKFLOW_COMPLETE": {
      const elapsed =
        typeof action.elapsedTimeSeconds === "number"
          ? ` in ${action.elapsedTimeSeconds.toFixed(2)}s`
          : "";
      return {
        ...state,
        isRunning: false,
        result: action.result,
        error: null,
        logs: [
          ...state.logs,
          makeLog(`Workflow completed${elapsed}`, "success"),
        ].slice(-MAX_LOGS),
      };
    }

    case "WORKFLOW_ERROR": {
      const now = Date.now();
      const errorLogs = [];
      const agents = Object.fromEntries(
        Object.entries(state.agents).map(([key, agent]) => [
          key,
          agent.status === "thinking"
            ? (() => {
                const startMs = agent.startTime
                  ? new Date(agent.startTime).getTime()
                  : null;
                const elapsed =
                  startMs !== null ? (now - startMs) / 1000 : null;
                const suffix =
                  elapsed !== null ? ` after ${elapsed.toFixed(2)}s` : "";
                errorLogs.push(
                  makeLog(`${agent.name || key} errored${suffix}`, "error"),
                );
                return { ...agent, status: "errored" };
              })()
            : agent,
        ]),
      );
      return {
        ...state,
        isRunning: false,
        agents,
        error:
          `Workflow failed before completion.\n${action.message || ""}`.trim(),
        errorFolder: action.outputFolder || "",
        result: null,
        logs: [
          ...state.logs,
          ...errorLogs,
          makeLog(
            `Workflow error: ${action.message || "Unknown error"}`,
            "error",
          ),
        ].slice(-MAX_LOGS),
      };
    }

    case "WORKFLOW_ABORTED":
      return { ...state, isRunning: false };

    case "CLEAR_INPUTS":
      return { ...state, files: [], result: null, error: null };
    case "CLEAR_AGENTS":
      return {
        ...state,
        agents: resetAgents(state.agents),
        result: null,
        error: null,
      };
    case "CLEAR_LOGS":
      return { ...state, logs: [] };

    case "LOG":
      return {
        ...state,
        logs: [...state.logs, makeLog(action.message, action.level)].slice(
          -MAX_LOGS,
        ),
      };

    default:
      return state;
  }
}

// =============================================================================
// App Component
// =============================================================================

/**
 * Top-level application shell for the frontend workflow UI.
 */
export default function App() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [prompt, setPrompt] = useState("");
  const [runEvaluations, setRunEvaluations] = useState(false);
  const [footerVersion, setFooterVersion] = useState("Version -");
  const [openaiStatus, setOpenaiStatus] = useState({
    isDown: false,
    description: "",
  });

  const {
    run: runWorkflow,
    runFromAgentInput,
    abort: abortWorkflow,
  } = useWorkflow(dispatch);
  const {
    state: createDataState,
    start: startCreateData,
    stop: stopCreateData,
  } = useCreateData();
  useAgents(dispatch);

  useEffect(() => {
    fetch(`/version.json?t=${Date.now()}`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return;
        const version = data.version || "unknown";
        const publishIso = data.commit_time || data.published_at;
        if (publishIso) {
          const d = new Date(publishIso);
          const text = isNaN(d.getTime()) ? publishIso : d.toLocaleString();
          setFooterVersion(`Version ${version} | Last publish: ${text}`);
        } else {
          setFooterVersion(`Version ${version}`);
        }
      })
      .catch(() => setFooterVersion("Version dev | Last publish: local"));
  }, []);

  useEffect(() => {
    let isDisposed = false;

    const checkOpenAiStatus = async () => {
      try {
        const res = await fetch(`/api/openai/status?t=${Date.now()}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (isDisposed) return;
        setOpenaiStatus({
          isDown: Boolean(data?.openai_down),
          description:
            typeof data?.description === "string" ? data.description : "",
        });
      } catch {
        if (isDisposed) return;
        setOpenaiStatus((prev) => ({ ...prev, isDown: false }));
      }
    };

    checkOpenAiStatus();
    const intervalId = setInterval(checkOpenAiStatus, 60_000);

    return () => {
      isDisposed = true;
      clearInterval(intervalId);
    };
  }, []);

  const serviceInactive = openaiStatus.isDown;

  const handleSend = (prompt, files) => {
    if (serviceInactive) {
      dispatch({
        type: "LOG",
        message: "OpenAI servers are down; workflow start blocked.",
        level: "error",
      });
      return;
    }
    runWorkflow(prompt, files, { runEvaluations });
  };

  const handleClearInputs = () => {
    abortWorkflow();
    dispatch({ type: "CLEAR_INPUTS" });
    setPrompt("");
  };

  const handleClearAgents = () => {
    abortWorkflow();
    dispatch({ type: "CLEAR_AGENTS" });
  };

  const handleAgentCardClick = (agentKey) => {
    const modal =
      agentKey === "setup" ? "setup" : agentKey === "output" ? "output" : "agent";
    dispatch({ type: "OPEN_MODAL", modal, agentKey });
  };

  const closeModal = () => dispatch({ type: "CLOSE_MODAL" });

  const handleCreateData = () => {
    dispatch({ type: "OPEN_MODAL", modal: "create-data" });
  };

  const handleInputToAgentRun = (agentKey, inputFile, callbacks = {}) => {
    if (serviceInactive) {
      callbacks.onError?.("OpenAI servers are down; agent-input workflow start blocked.");
      dispatch({
        type: "LOG",
        message: "OpenAI servers are down; agent-input workflow start blocked.",
        level: "error",
      });
      return;
    }
    runFromAgentInput(agentKey, inputFile, prompt, callbacks);
  };

  return (
    <>
      {serviceInactive && (
        <div className="outage-banner" role="alert" aria-live="assertive">
          OpenAI servers are down. This site is currently inactive.
          {openaiStatus.description ? (
            <span className="outage-banner-detail">
              {" "}
              ({openaiStatus.description})
            </span>
          ) : null}
        </div>
      )}

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
        <div className="content-shell">
          <div className="top-panels">
            <PromptInput
              prompt={prompt}
              onPromptChange={setPrompt}
              files={state.files}
              dispatch={dispatch}
              isRunning={state.isRunning}
              serviceInactive={serviceInactive}
              onSend={handleSend}
              onClearInputs={handleClearInputs}
              onClearAgents={handleClearAgents}
              onShowFiles={() =>
                dispatch({ type: "OPEN_MODAL", modal: "files" })
              }
              onShowHelp={() => dispatch({ type: "OPEN_MODAL", modal: "help" })}
            />
            <DeveloperToolsPanel
              isRunning={state.isRunning}
              serviceInactive={serviceInactive}
              isCreateDataRunning={
                createDataState.isRunning || createDataState.isStarting
              }
              onCreateData={handleCreateData}
              onInputToAgent={() =>
                dispatch({ type: "OPEN_MODAL", modal: "input-to-agent" })
              }
              onShowTestFiles={() =>
                dispatch({ type: "OPEN_MODAL", modal: "test-files" })
              }
              runEvaluations={runEvaluations}
              onRunEvaluationsChange={setRunEvaluations}
            />
          </div>

          <section className="agents-section">
            <div className="pipeline-split">
              <div className="card pipeline-panel agents-panel workflow-panel">
                <div className="card-header">
                  <h2>Agent Pipeline</h2>
                  <p className="subtitle">
                    Click a block to inspect inputs, outputs, and execution
                    details.
                  </p>
                </div>
                <div className="card-body">
                  <AgentPipeline
                    agents={state.agents}
                    onCardClick={handleAgentCardClick}
                  />
                </div>
              </div>
              <LogPanel logs={state.logs} />
            </div>
          </section>

          {state.result && <ResultsSection result={state.result} />}
          {state.error && (
            <FailureSection
              error={state.error}
              outputFolder={state.errorFolder}
            />
          )}
        </div>
      </main>

      <footer className="footer">
        <div className="footer-content">
          <p className="footer-primary">
            UCONN Physics Research - Dr. Xian Wu (2025 - 2026)
          </p>
          <p className="footer-primary">Created by Lindim Ismaili</p>
          <p className="footer-secondary">{footerVersion}</p>
        </div>
      </footer>

      <FilesModal
        isOpen={state.activeModal === "files"}
        onClose={closeModal}
        files={state.files}
        onRemove={(id) => dispatch({ type: "REMOVE_FILE", fileId: id })}
      />
      <TestFilesModal
        isOpen={state.activeModal === "test-files"}
        onClose={closeModal}
        files={state.files}
        dispatch={dispatch}
        isRunning={state.isRunning}
      />
      <CreateDataModal
        isOpen={state.activeModal === "create-data"}
        onClose={closeModal}
        createData={createDataState}
        onRun={() => startCreateData(prompt)}
        onStop={stopCreateData}
      />
      <InputToAgentModal
        isOpen={state.activeModal === "input-to-agent"}
        onClose={closeModal}
        agents={state.agents}
        isRunning={state.isRunning}
        serviceInactive={serviceInactive}
        onRun={handleInputToAgentRun}
      />
      <AgentDetailModal
        isOpen={state.activeModal === "agent"}
        onClose={closeModal}
        agentKey={state.activeAgentKey}
        agent={state.activeAgentKey ? state.agents[state.activeAgentKey] : null}
      />
      <SetupModal isOpen={state.activeModal === "setup"} onClose={closeModal} />
      <OutputModal isOpen={state.activeModal === "output"} onClose={closeModal} />
      <HelpModal isOpen={state.activeModal === "help"} onClose={closeModal} />
    </>
  );
}
