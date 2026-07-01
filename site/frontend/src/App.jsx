import { useReducer, useEffect, useState } from "react";
import "./styles/app.css";

import ChatConversation from "./sections/ChatConversation.jsx";
import DeveloperToolsPanel from "./components/DeveloperToolsPanel.jsx";

import FilesModal from "./components/modals/FilesModal.jsx";
import TestFilesModal from "./components/modals/TestFilesModal.jsx";
import CreateDataModal from "./components/modals/CreateDataModal.jsx";
import AgentDetailModal from "./components/modals/AgentDetailModal.jsx";
import SetupModal from "./components/modals/SetupModal.jsx";
import OutputModal from "./components/modals/OutputModal.jsx";
import HelpModal from "./components/modals/HelpModal.jsx";
import InputToAgentModal from "./components/modals/InputToAgentModal.jsx";
import BatchEvalModal from "./components/modals/BatchEvalModal.jsx";

import { AppShell } from "./components/ui/index.js";
import { SiteBanner, SiteHeader, SiteFooter } from "./sections/SiteChrome.jsx";

import { useWorkflow } from "./hooks/useWorkflow.js";
import { useAgents } from "./hooks/useAgents.js";
import { getDerivedRegistry } from "./hooks/useAgentRegistry.js";
import { useCreateData } from "./hooks/useCreateData.js";
import { useBatchEval } from "./hooks/useBatchEval.js";
import { reducer, INITIAL_STATE } from "./state/reducer.js";

// =============================================================================
// App Component
// =============================================================================

/**
 * Top-level application shell for the chat-first workflow UI. Holds all state
 * (via the presentation-agnostic reducer) and modal routing; the conversation
 * surface itself is composed in `sections/ChatConversation.jsx` from ui/
 * primitives.
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
  const {
    state: batchEvalState,
    start: startBatchEval,
    stop: stopBatchEval,
    loadRuns: loadBatchEvalRuns,
  } = useBatchEval();
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

  const startRun = (promptText, files, theme = "") => {
    if (serviceInactive) {
      dispatch({
        type: "LOG",
        message: "OpenAI servers are down; workflow start blocked.",
        level: "error",
      });
      return;
    }
    runWorkflow(promptText, files, { runEvaluations, theme });
  };

  // The Composer's single send/stop affordance: `{stop:true}` while a run is in
  // flight, otherwise `{prompt, files, theme}` to start a new run.
  const handleComposerSend = (payload = {}) => {
    if (payload.stop) {
      handleClearInputs();
      dispatch({ type: "CLEAR_LOGS" });
      return;
    }
    startRun(payload.prompt ?? prompt, payload.files ?? state.files, payload.theme ?? "");
  };

  const handleClearInputs = () => {
    abortWorkflow();
    dispatch({ type: "CLEAR_INPUTS" });
    dispatch({ type: "CLEAR_AGENTS" });
    setPrompt("");
  };

  const handleNodeClick = (agentKey) => {
    // Runtime nodes get their dedicated modals (keyed by agent key); guardrail
    // and LLM agents share the generic detail modal.
    const kind =
      state.agents[agentKey]?.kind ??
      getDerivedRegistry()?.registry?.[agentKey]?.kind;
    const modal = kind === "runtime" ? agentKey : "agent";
    dispatch({ type: "OPEN_MODAL", modal, agentKey });
  };

  const closeModal = () => dispatch({ type: "CLOSE_MODAL" });

  const handleCreateData = () => {
    dispatch({ type: "OPEN_MODAL", modal: "create-data" });
  };

  const handleBatchEval = () => {
    dispatch({ type: "OPEN_MODAL", modal: "batch-eval" });
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
      <AppShell
        banner={
          <SiteBanner show={serviceInactive} detail={openaiStatus.description || undefined}>
            OpenAI servers are down. This site is currently inactive.
          </SiteBanner>
        }
        header={<SiteHeader />}
        footer={<SiteFooter version={footerVersion} />}
      >
        <div className="workspace">
          <div className="workspace-main">
            <ChatConversation
              state={state}
              dispatch={dispatch}
              prompt={prompt}
              onPromptChange={setPrompt}
              isRunning={state.isRunning}
              serviceInactive={serviceInactive}
              onSend={handleComposerSend}
              onNodeClick={handleNodeClick}
              onShowHelp={() => dispatch({ type: "OPEN_MODAL", modal: "help" })}
            />
          </div>
          <aside className="workspace-side">
            <DeveloperToolsPanel
              isRunning={state.isRunning}
              serviceInactive={serviceInactive}
              isCreateDataRunning={
                createDataState.isRunning || createDataState.isStarting
              }
              onCreateData={handleCreateData}
              onBatchEval={handleBatchEval}
              isBatchEvalRunning={batchEvalState.isRunning || batchEvalState.isStarting}
              onInputToAgent={() =>
                dispatch({ type: "OPEN_MODAL", modal: "input-to-agent" })
              }
              onShowTestFiles={() =>
                dispatch({ type: "OPEN_MODAL", modal: "test-files" })
              }
              runEvaluations={runEvaluations}
              onRunEvaluationsChange={setRunEvaluations}
              logs={state.logs}
            />
          </aside>
        </div>
      </AppShell>

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
      <SetupModal
        isOpen={state.activeModal === "setup"}
        onClose={closeModal}
        workflowId={state.workflowId}
        runInputs={state.runInputs}
      />
      <OutputModal
        isOpen={state.activeModal === "output"}
        onClose={closeModal}
        result={state.result}
        runInputs={state.runInputs}
      />
      <HelpModal isOpen={state.activeModal === "help"} onClose={closeModal} />
      <BatchEvalModal
        isOpen={state.activeModal === "batch-eval"}
        onClose={closeModal}
        batchEval={{ ...batchEvalState, start: startBatchEval, stop: stopBatchEval }}
        loadRuns={loadBatchEvalRuns}
      />
    </>
  );
}
