import { useReducer, useEffect, useState } from "react";
import "./styles/app.css";

import ChatConversation from "./sections/ChatConversation.jsx";

import AgentDetailModal from "./components/modals/AgentDetailModal.jsx";
import SetupModal from "./components/modals/SetupModal.jsx";
import OutputModal from "./components/modals/OutputModal.jsx";
import HelpModal from "./components/modals/HelpModal.jsx";
import AdminDashboard from "./sections/AdminDashboard.jsx";

import { AppShell, Button, Modal } from "./components/ui/index.js";
import { SiteBanner, SiteHeader, SiteFooter } from "./sections/SiteChrome.jsx";

import { useWorkflow } from "./hooks/useWorkflow.js";
import { useAgents } from "./hooks/useAgents.js";
import { getDerivedRegistry } from "./hooks/useAgentRegistry.js";
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
  const [serviceStatus, setServiceStatus] = useState({ maintenance: false, code: "", message: "", detectedAt: "" });

  const {
    run: runWorkflow,
    abort: abortWorkflow,
  } = useWorkflow(dispatch);
  useAgents(dispatch);

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
    let isDisposed = false;

    const checkServiceStatus = async () => {
      try {
        const res = await fetch(`/api/service-status?t=${Date.now()}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (isDisposed) return;
        setServiceStatus({ maintenance: Boolean(data?.maintenance), code: typeof data?.code === "string" ? data.code : "", message: typeof data?.message === "string" ? data.message : "", detectedAt: typeof data?.detectedAt === "string" ? data.detectedAt : "" });
      } catch {
        if (isDisposed) return;
        setServiceStatus((prev) => ({ ...prev, maintenance: false }));
      }
    };

    checkServiceStatus();
    const intervalId = setInterval(checkServiceStatus, 60_000);

    return () => {
      isDisposed = true;
      clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    let visitorId = "";
    try {
      visitorId = localStorage.getItem("anonymousVisitorId") || crypto.randomUUID();
      localStorage.setItem("anonymousVisitorId", visitorId);
    } catch {
      visitorId = crypto.randomUUID?.() || `visitor-${Date.now()}`;
    }
    const heartbeat = () => fetch("/api/visitor/heartbeat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ visitorId }) }).catch(() => {});
    heartbeat();
    const intervalId = setInterval(heartbeat, 60_000);
    return () => clearInterval(intervalId);
  }, []);

  const serviceInactive = serviceStatus.maintenance;

  const startRun = (promptText, files, theme = "") => {
    if (serviceInactive) {
      dispatch({
        type: "LOG",
        message: `${serviceStatus.code || "Service maintenance"}; workflow start blocked.`,
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

  return (
    <>
      <AppShell
        banner={
          <SiteBanner show={serviceInactive} detail={serviceStatus.code || undefined}>
            {serviceStatus.message || "This site is currently inactive for maintenance."}
          </SiteBanner>
        }
        header={<SiteHeader mode={mode} onModeChange={setMode} />}
        footer={<SiteFooter version={footerVersion} />}
      >
        <div className="workspace is-single">
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
              mode={mode}
            />
          </div>
        </div>
      </AppShell>

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
      <Modal isOpen={serviceInactive} onClose={() => {}} dismissible={false} title="Site maintenance">
        <div className="maintenance-modal-content">
          <p>{serviceStatus.message || "This site is temporarily unavailable while administrators resolve an operational issue."}</p>
          <strong>{serviceStatus.code || "SCHEDULED MAINTENANCE"}</strong>
          <span>Detected: {serviceStatus.detectedAt ? new Date(serviceStatus.detectedAt).toLocaleString() : "Unknown"}</span>
          <Button variant="secondary" onClick={() => window.location.reload()}>Check again</Button>
        </div>
      </Modal>
    </>
  );
}

export default function App() {
  return window.location.pathname.startsWith("/admin") ? <AdminDashboard /> : <PublicApp />;
}
