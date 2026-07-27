import { useReducer, useEffect, useState } from "react";
import "./styles/app.css";

import ChatConversation from "./sections/ChatConversation.jsx";

import FilesModal from "./components/modals/FilesModal.jsx";
import AgentDetailModal from "./components/modals/AgentDetailModal.jsx";
import SetupModal from "./components/modals/SetupModal.jsx";
import OutputModal from "./components/modals/OutputModal.jsx";
import HelpModal from "./components/modals/HelpModal.jsx";
import AccessGateModal from "./components/modals/AccessGateModal.jsx";
import AdminDashboard from "./sections/AdminDashboard.jsx";

import {
  AppShell,
  Button,
  Card,
  CardBody,
  Modal,
} from "./components/ui/index.js";
import { SiteBanner, SiteHeader, SiteFooter } from "./sections/SiteChrome.jsx";

import { useWorkflow } from "./hooks/useWorkflow.js";
import { useAgents } from "./hooks/useAgents.js";
import { getDerivedRegistry } from "./hooks/useAgentRegistry.js";
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
function PublicApp() {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [prompt, setPrompt] = useState("");
  // Public mode only changes audience-facing guidance. Developer controls live
  // under the protected Admin Dashboard instead of this consumer page.
  const [mode, setMode] = useState(() => {
    try {
      const stored = localStorage.getItem("uiMode");
      return stored === "teacher" ? stored : "user";
    } catch {
      return "user";
    }
  });
  const [footerVersion, setFooterVersion] = useState("Version -");
  const [serviceStatus, setServiceStatus] = useState({
    maintenance: false,
    code: "",
    message: "",
    detectedAt: "",
  });
  const [openaiStatus, setOpenaiStatus] = useState({
    isDown: false,
    description: "",
  });
  const access = useAppAccess();

  const {
    run: runWorkflow,
    stop: stopWorkflow,
    isStopping,
  } = useWorkflow(dispatch, { enabled: access.accessGranted });
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

  // Operational maintenance is owned by the admin dashboard; provider outage is
  // reported separately. The consumer page polls both and blocks on either.
  useEffect(() => {
    if (!access.accessGranted) return undefined;
    let isDisposed = false;

    const checkServiceStatus = async () => {
      try {
        const res = await fetchWithAbort(`/api/service-status?t=${Date.now()}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (isDisposed) return;
        setServiceStatus({
          maintenance: Boolean(data?.maintenance),
          code: typeof data?.code === "string" ? data.code : "",
          message: typeof data?.message === "string" ? data.message : "",
          detectedAt: typeof data?.detectedAt === "string" ? data.detectedAt : "",
        });
      } catch {
        if (isDisposed) return;
        setServiceStatus((prev) => ({ ...prev, maintenance: false }));
      }
    };

    const checkOpenaiStatus = async () => {
      try {
        const res = await fetchWithAbort(`/api/openai/status?t=${Date.now()}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (isDisposed) return;
        setOpenaiStatus({
          isDown: Boolean(data?.openai_down),
          description: typeof data?.description === "string" ? data.description : "",
        });
      } catch {
        if (isDisposed) return;
        setOpenaiStatus((prev) => ({ ...prev, isDown: false }));
      }
    };

    const poll = () => {
      checkServiceStatus();
      checkOpenaiStatus();
    };

    poll();
    const intervalId = setInterval(poll, 60_000);

    return () => {
      isDisposed = true;
      clearInterval(intervalId);
    };
  }, [access.accessGranted]);

  useEffect(() => {
    let visitorId = "";
    try {
      visitorId = localStorage.getItem("anonymousVisitorId") || crypto.randomUUID();
      localStorage.setItem("anonymousVisitorId", visitorId);
    } catch {
      visitorId = crypto.randomUUID?.() || `visitor-${Date.now()}`;
    }
    const heartbeat = () =>
      fetch("/api/visitor/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visitorId }),
      }).catch(() => {});
    heartbeat();
    const intervalId = setInterval(heartbeat, 60_000);
    return () => clearInterval(intervalId);
  }, []);

  const readinessMessage = access.readinessError
    ? `Server readiness could not be verified: ${access.readinessError}`
    : access.accessGranted && !access.readiness?.openaiApiKeyConfigured
      ? "OPENAI_API_KEY is not configured on the server. Configure it in the server environment and restart the application; never enter it into this browser."
      : "";
  const serviceInactive =
    serviceStatus.maintenance || openaiStatus.isDown || !access.workflowReady;
  const serviceMessage = serviceStatus.maintenance
    ? serviceStatus.message || "This site is currently inactive for maintenance."
    : openaiStatus.isDown
      ? "OpenAI servers are currently unavailable."
      : readinessMessage || "The application server is not ready to start workflows.";
  const bannerDetail = serviceStatus.maintenance
    ? serviceStatus.code || undefined
    : openaiStatus.isDown
      ? openaiStatus.description || undefined
      : undefined;

  const startRun = (promptText, files, theme = "") => {
    if (serviceInactive) {
      dispatch({
        type: "LOG",
        message: `${serviceMessage} Workflow start blocked.`,
        level: "error",
      });
      return;
    }
    runWorkflow(promptText, files, { runEvaluations: false, theme });
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
    // Runtime nodes get their dedicated modals (keyed by agent key); guardrail
    // and LLM agents share the generic detail modal.
    const kind =
      state.agents[agentKey]?.kind ??
      getDerivedRegistry()?.registry?.[agentKey]?.kind;
    const modal = kind === "runtime" ? agentKey : "agent";
    dispatch({ type: "OPEN_MODAL", modal, agentKey });
  };

  const closeModal = () => dispatch({ type: "CLOSE_MODAL" });

  return (
    <>
      <AppShell
        banner={
          <SiteBanner
            show={access.accessGranted && serviceInactive}
            detail={bannerDetail}
          >
            {serviceMessage}
          </SiteBanner>
        }
        header={<SiteHeader mode={mode} onModeChange={setMode} showDonation />}
        footer={
          <SiteFooter version={footerVersion} donationUrl={access.donationUrl} />
        }
      >
        {access.accessGranted ? (
          <div className="workspace is-single">
            <div className="workspace-main">
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
          <Modal
            isOpen={serviceStatus.maintenance}
            onClose={() => {}}
            dismissible={false}
            title="Site maintenance"
          >
            <div className="maintenance-modal-content">
              <p>
                {serviceStatus.message ||
                  "This site is temporarily unavailable while administrators resolve an operational issue."}
              </p>
              <strong>{serviceStatus.code || "SCHEDULED MAINTENANCE"}</strong>
              <span>
                Detected:{" "}
                {serviceStatus.detectedAt
                  ? new Date(serviceStatus.detectedAt).toLocaleString()
                  : "Unknown"}
              </span>
              <Button variant="secondary" onClick={() => window.location.reload()}>
                Check again
              </Button>
            </div>
          </Modal>
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

export default function App() {
  return window.location.pathname.startsWith("/admin") ? <AdminDashboard /> : <PublicApp />;
}
