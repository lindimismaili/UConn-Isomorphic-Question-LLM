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
import AccessGateModal from "./components/modals/AccessGateModal.jsx";

import {
  AppShell,
  Button,
  Card,
  CardBody,
} from "./components/ui/index.js";
import { SiteBanner, SiteHeader, SiteFooter } from "./sections/SiteChrome.jsx";

import { useWorkflow } from "./hooks/useWorkflow.js";
import { useAgents } from "./hooks/useAgents.js";
import { getDerivedRegistry } from "./hooks/useAgentRegistry.js";
import { useCreateData } from "./hooks/useCreateData.js";
import { useBatchEval } from "./hooks/useBatchEval.js";
import { useAppAccess } from "./hooks/useAppAccess.js";
import { reducer, INITIAL_STATE } from "./state/reducer.js";
import { fetchWithAbort } from "./utils/api.js";

// =============================================================================
// App Component
// =============================================================================

export function ActiveBatchNotice({ label, state, onStop, onRecover }) {
  const isActive = state.isStarting || state.isRunning;
  const needsRecovery = state.status === "start_uncertain";
  const isRecoveringResult = Boolean(state.isLoadingResult);
  if (!isActive && !needsRecovery && !isRecoveringResult) return null;

  const progress = isRecoveringResult
    ? "Loading final result…"
    : state.totalFiles > 0
      ? `${state.doneFiles}/${state.totalFiles} complete`
      : isActive
        ? "Preparing…"
        : "Start outcome needs recovery";

  return (
    <Card className="active-work-card" role="status" aria-live="polite">
      <CardBody className="active-work-card-body">
        <div>
          <strong>{label}</strong>
          <span className="active-work-card-progress">{progress}</span>
        </div>
        <div className="active-work-card-actions">
          {isActive ? (
            <Button
              variant="danger"
              size="sm"
              onClick={onStop}
              disabled={state.status === "stopping"}
            >
              {state.status === "stopping" ? "Stopping…" : "Stop"}
            </Button>
          ) : null}
          {needsRecovery ? (
            <Button variant="secondary" size="sm" onClick={onRecover}>
              Open Recovery
            </Button>
          ) : null}
        </div>
      </CardBody>
    </Card>
  );
}

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
  // Site mode: 'user' | 'teacher' | 'dev'. Dev shows the developer-tools side
  // panel; user/teacher hide it and tailor the empty-conversation hint.
  const [mode, setMode] = useState(() => {
    try {
      const stored = localStorage.getItem("uiMode");
      return stored === "teacher" || stored === "dev" ? stored : "user";
    } catch {
      return "user";
    }
  });
  const [footerVersion, setFooterVersion] = useState("Version -");
  const [openaiStatus, setOpenaiStatus] = useState({
    isDown: false,
    description: "",
  });
  const access = useAppAccess();

  const {
    run: runWorkflow,
    runFromAgentInput,
    stop: stopWorkflow,
    isStopping,
  } = useWorkflow(dispatch, { enabled: access.accessGranted });
  const {
    state: createDataState,
    start: startCreateData,
    stop: stopCreateData,
  } = useCreateData();
  const {
    state: batchEvalState,
    start: startBatchEval,
    stop: stopBatchEval,
    reset: resetBatchEval,
    loadRuns: loadBatchEvalRuns,
  } = useBatchEval();
  useAgents(dispatch, access.accessGranted);

  useEffect(() => {
    try {
      localStorage.setItem("uiMode", mode);
    } catch {
      /* storage unavailable (private mode) — mode stays session-only */
    }
  }, [mode]);

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
    if (!access.accessGranted) return undefined;
    let isDisposed = false;

    const checkOpenAiStatus = async () => {
      try {
        const res = await fetchWithAbort(`/api/openai/status?t=${Date.now()}`, {
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
  }, [access.accessGranted]);

  const readinessMessage = access.readinessError
    ? `Server readiness could not be verified: ${access.readinessError}`
    : access.accessGranted && !access.readiness?.openaiApiKeyConfigured
      ? "OPENAI_API_KEY is not configured on the server. Configure it in the server environment and restart the application; never enter it into this browser."
      : "";
  const serviceInactive = openaiStatus.isDown || !access.workflowReady;
  const serviceMessage = openaiStatus.isDown
    ? "OpenAI servers are currently unavailable."
    : readinessMessage || "The application server is not ready to start workflows.";

  const startRun = (promptText, files, theme = "") => {
    if (serviceInactive) {
      dispatch({
        type: "LOG",
        message: `${serviceMessage} Workflow start blocked.`,
        level: "error",
      });
      return;
    }
    const approveEvaluations = mode === "dev" && runEvaluations;
    setRunEvaluations(false);
    runWorkflow(promptText, files, { runEvaluations: approveEvaluations, theme });
  };

  // The Composer's single send/stop affordance: `{stop:true}` while a run is in
  // flight, otherwise `{prompt, files, theme}` to start a new run.
  const handleComposerSend = (payload = {}) => {
    if (payload.stop) {
      stopWorkflow();
      return;
    }
    startRun(payload.prompt ?? prompt, payload.files ?? state.files, payload.theme ?? "");
  };

  const handleNodeClick = (agentKey) => {
    if (mode !== "dev") return;
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
      callbacks.onError?.(`${serviceMessage} Agent-input workflow start blocked.`);
      dispatch({
        type: "LOG",
        message: `${serviceMessage} Agent-input workflow start blocked.`,
        level: "error",
      });
      return;
    }
    const approveEvaluations = mode === "dev" && runEvaluations;
    setRunEvaluations(false);
    runFromAgentInput(agentKey, inputFile, prompt, {
      ...callbacks,
      runEvaluations: approveEvaluations,
    });
  };

  return (
    <>
      <AppShell
        banner={
          <SiteBanner
            show={access.accessGranted && serviceInactive}
            detail={openaiStatus.isDown ? openaiStatus.description || undefined : undefined}
          >
            {serviceMessage}
          </SiteBanner>
        }
        header={
          <SiteHeader
            mode={mode}
            onModeChange={(nextMode) => {
              setMode(nextMode);
              if (nextMode !== "dev") {
                setRunEvaluations(false);
                dispatch({ type: "CLOSE_MODAL" });
              }
            }}
          />
        }
        footer={
          <SiteFooter
            version={footerVersion}
            donationUrl={access.donationUrl}
          />
        }
      >
        {access.accessGranted ? (
          <div className={`workspace${mode === "dev" ? "" : " is-single"}`}>
          <div className="workspace-main">
            {mode !== "dev" ? (
              <>
                <ActiveBatchNotice
                  label="Create Data batch"
                  state={createDataState}
                  onStop={stopCreateData}
                  onRecover={() => {
                    setMode("dev");
                    dispatch({ type: "OPEN_MODAL", modal: "create-data" });
                  }}
                />
                <ActiveBatchNotice
                  label="Batch Evaluation"
                  state={batchEvalState}
                  onStop={stopBatchEval}
                  onRecover={() => {
                    setMode("dev");
                    dispatch({ type: "OPEN_MODAL", modal: "batch-eval" });
                  }}
                />
              </>
            ) : null}
            <ChatConversation
              state={state}
              dispatch={dispatch}
              prompt={prompt}
              onPromptChange={setPrompt}
              maxInstructionChars={access.config?.maxInstructionChars || 20000}
              isRunning={state.isRunning}
              serviceInactive={serviceInactive}
              serviceMessage={serviceMessage}
              isStopping={isStopping}
              onSend={handleComposerSend}
              onNodeClick={handleNodeClick}
              onReviewFiles={() => dispatch({ type: "OPEN_MODAL", modal: "files" })}
              onShowHelp={() => dispatch({ type: "OPEN_MODAL", modal: "help" })}
              mode={mode}
            />
          </div>
          {mode === "dev" ? (
            <aside className="workspace-side">
              <DeveloperToolsPanel
                isRunning={state.isRunning}
                serviceInactive={serviceInactive}
                isCreateDataRunning={
                  createDataState.isRunning
                  || createDataState.isStarting
                  || createDataState.isLoadingResult
                }
                onCreateData={handleCreateData}
                onBatchEval={handleBatchEval}
                isBatchEvalRunning={
                  batchEvalState.isRunning
                  || batchEvalState.isStarting
                  || batchEvalState.isLoadingResult
                }
                onInputToAgent={() =>
                  dispatch({ type: "OPEN_MODAL", modal: "input-to-agent" })
                }
                onShowTestFiles={() =>
                  dispatch({ type: "OPEN_MODAL", modal: "test-files" })
                }
                runEvaluations={runEvaluations}
                onRunEvaluationsChange={setRunEvaluations}
                evaluationPolicy={access.config?.evaluationPolicy}
                logs={state.logs}
              />
            </aside>
          ) : null}
          </div>
        ) : null}
      </AppShell>

      {access.accessGranted ? (
        <>
      <FilesModal
        isOpen={state.activeModal === "files"}
        onClose={closeModal}
        files={state.files}
        onRemove={(id) => dispatch({ type: "REMOVE_FILE", fileId: id })}
      />
      <TestFilesModal
        isOpen={mode === "dev" && state.activeModal === "test-files"}
        onClose={closeModal}
        files={state.files}
        dispatch={dispatch}
        isRunning={state.isRunning}
      />
      <CreateDataModal
        isOpen={mode === "dev" && state.activeModal === "create-data"}
        onClose={closeModal}
        createData={createDataState}
        onRun={({ confirmWorkflowCosts } = {}) => {
          if (!serviceInactive) {
            startCreateData(prompt, { confirmWorkflowCosts });
          }
        }}
        onStop={stopCreateData}
        serviceInactive={serviceInactive}
        maxWorkflows={access.config?.createDataPolicy?.maxEntries || 5}
      />
      <InputToAgentModal
        isOpen={mode === "dev" && state.activeModal === "input-to-agent"}
        onClose={closeModal}
        agents={state.agents}
        isRunning={state.isRunning}
        serviceInactive={serviceInactive}
        onRun={handleInputToAgentRun}
      />
      <AgentDetailModal
        isOpen={mode === "dev" && state.activeModal === "agent"}
        onClose={closeModal}
        agentKey={state.activeAgentKey}
        agent={state.activeAgentKey ? state.agents[state.activeAgentKey] : null}
      />
      <SetupModal
        isOpen={mode === "dev" && state.activeModal === "setup"}
        onClose={closeModal}
        workflowId={state.workflowId}
        runInputs={state.runInputs}
      />
      <OutputModal
        isOpen={mode === "dev" && state.activeModal === "output"}
        onClose={closeModal}
        result={state.result}
        runInputs={state.runInputs}
      />
      <HelpModal isOpen={state.activeModal === "help"} onClose={closeModal} />
      <BatchEvalModal
        isOpen={mode === "dev" && state.activeModal === "batch-eval"}
        onClose={closeModal}
        batchEval={{
          ...batchEvalState,
          start: startBatchEval,
          stop: stopBatchEval,
          reset: resetBatchEval,
        }}
        loadRuns={loadBatchEvalRuns}
        serviceInactive={serviceInactive}
      />
        </>
      ) : null}

      <AccessGateModal
        isOpen={!access.accessGranted}
        loading={access.loading}
        verifying={access.verifying}
        configAvailable={Boolean(access.config)}
        hasSavedToken={access.hasSavedAccessToken}
        error={access.accessError}
        onVerify={access.verify}
        onRetry={access.retry}
      />
    </>
  );
}
