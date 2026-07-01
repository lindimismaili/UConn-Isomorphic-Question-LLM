/**
 * Central UI reducer for the workflow app — workflow, agent, log, file, and
 * modal state. Extracted verbatim from App.jsx so the presentation layer can be
 * refactored without touching state logic. It is 100% presentation-agnostic:
 * it knows nothing about layout, DOM, or CSS.
 */

import { generateId } from "../utils/ids.js";
import { getDerivedRegistry } from "../hooks/useAgentRegistry.js";

// =============================================================================
// Initial State
// =============================================================================

/** Maximum number of log entries retained in memory and rendered. */
export const MAX_LOGS = 250;

/** Build empty workflow state for a single agent key. */
export function _emptyAgentState() {
  return {
    name: "Unavailable",
    description: "",
    model: "Unavailable",
    reasoningLevel: "Unavailable",
    kind: "agent",
    status: "inactive",
    startTime: null,
    endTime: null,
    executionTime: null,
    inputFrom: "",
    outputTo: "",
    inputText: "",
    outputText: "",
  };
}

/**
 * Returns the first value in the list that is neither empty nor nullish.
 *
 * @param {...unknown} values
 * @returns {unknown}
 */
export function firstDefined(...values) {
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
export function makeLog(message, level) {
  return {
    id: generateId(),
    time: new Date().toLocaleTimeString(),
    message,
    level,
  };
}

export const INITIAL_STATE = {
  files: [],
  agents: {},
  isRunning: false,
  workflowId: null,
  logs: [makeLog("User connection established.", "info")],
  result: null,
  error: null,
  errorFolder: "",
  activeModal: null,
  activeAgentKey: null,
  // Inputs of the most recent run (prompt + file names), captured at start so
  // the runtime modals can show them after the upload list is cleared.
  runInputs: null,
  // Guardrail-block details for the most recent run, or null. Shape mirrors the
  // status payload's `blocked` field: {category, reason, stage}.
  blocked: null,
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
export function resetAgents(agents) {
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
export function queueAgents(agents, excludeKeys = []) {
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
export function reducer(state, action) {
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
        const existing = updatedAgents[key] || _emptyAgentState();
        const reasoningLevel = firstDefined(
          config.reasoning,
          config.reasoningLevel,
          config.reasoning_level,
          existing.reasoningLevel,
        );
        updatedAgents[key] = {
          ...existing,
          name: firstDefined(config.name, existing.name) || key,
          model: firstDefined(config.model, existing.model) || "Unavailable",
          reasoningLevel: reasoningLevel || "none",
          description: firstDefined(config.description, existing.description) || "",
          inputFrom: firstDefined(config.inputFrom, existing.inputFrom) || "",
          outputTo: firstDefined(config.outputTo, existing.outputTo) || "",
          kind: firstDefined(config.kind, config.type, config.nodeKind, existing.kind) || "agent",
        };
      }
      return { ...state, agents: updatedAgents };
    }

    case "WORKFLOW_START": {
      const agents = resetAgents(state.agents);
      // The first pipeline node (runtime setup) is simulated locally while the
      // backend bootstraps; agent state preserves agents.json sequence order.
      const [setupKey] = Object.keys(agents);
      const withSetup = setupKey
        ? {
            ...agents,
            [setupKey]: { ...agents[setupKey], status: "thinking", startTime: new Date() },
          }
        : agents;
      const queued = queueAgents(withSetup, setupKey ? [setupKey] : []);
      const startLogs = [makeLog("Process starting...", "info")];
      if (setupKey) {
        startLogs.push(makeLog(`${agents[setupKey].name || setupKey} started`, "info"));
      }
      return {
        ...state,
        agents: queued,
        isRunning: true,
        workflowId: null,
        result: null,
        error: null,
        errorFolder: "",
        files: [],
        blocked: null,
        runInputs: {
          prompt: action.prompt || "",
          files: action.fileNames || [],
          startAgent: action.startAgent || "",
        },
        logs: [...state.logs, ...startLogs].slice(-MAX_LOGS),
      };
    }

    case "WORKFLOW_REGISTERED":
      // The backend accepted the run; the Starter node stays "thinking" while
      // the shared runtime (OpenAI container) is created and bootstrapped.
      return { ...state, workflowId: action.workflowId };

    case "SETUP_COMPLETE": {
      // Fired once the backend reports the first real agent has started (or the
      // run finished). Idempotent so repeated polls don't re-log or re-complete.
      const [setupKey] = Object.keys(state.agents);
      const setup = setupKey ? state.agents[setupKey] : null;
      if (!setup || setup.status !== "thinking") return state;
      const setupLabel = setup.name || setupKey;
      const setupStart = setup.startTime
        ? new Date(setup.startTime).getTime()
        : null;
      const setupEnd = Date.now();
      const setupSeconds =
        setupStart !== null ? (setupEnd - setupStart) / 1000 : null;
      const setupMessage =
        setupSeconds !== null
          ? `${setupLabel} completed in ${setupSeconds.toFixed(2)}s`
          : `${setupLabel} completed`;
      return {
        ...state,
        agents: {
          ...state.agents,
          [setupKey]: {
            ...setup,
            status: "completed",
            endTime: new Date(setupEnd),
            executionTime: setupSeconds,
          },
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
      // While the backend is still bootstrapping, polls report not-yet-started
      // agents as "inactive". Don't let that drag an agent the UI has already
      // advanced (thinking/completed/errored/cancelled) back to a hidden state.
      const isBackwards =
        (action.status === "inactive" || action.status === "queued") &&
        ["thinking", "completed", "errored", "cancelled"].includes(agent.status);
      const nextStatus = isBackwards ? agent.status : action.status;
      const nextAgents = {
        ...state.agents,
        [action.agentKey]: { ...agent, ...patch, status: nextStatus },
      };
      if (nextStatus === "completed") {
        const nextKey = (getDerivedRegistry()?.nextAgentByKey ?? {})[action.agentKey];
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
      const now = new Date();
      const agents = Object.fromEntries(
        Object.entries(state.agents).map(([key, agent]) => [
          key,
          agent.status === "thinking"
            ? { ...agent, status: "completed", endTime: agent.endTime ?? now }
            : agent,
        ]),
      );
      return {
        ...state,
        isRunning: false,
        agents,
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

    case "WORKFLOW_BLOCKED": {
      // A guardrail tripped: the run ends in the terminal `blocked` status. Mark
      // the tripped guardrail node `blocked` (falling back to any thinking node),
      // stop running, and record the block so the transcript can explain it.
      const blocked = action.blocked || {};
      const stageKey = blocked.stage || action.stage || null;
      const now = new Date();
      const agents = Object.fromEntries(
        Object.entries(state.agents).map(([key, agent]) => {
          if (key === stageKey) {
            return [key, { ...agent, status: "blocked", endTime: agent.endTime ?? now }];
          }
          if (agent.status === "thinking") {
            return [
              key,
              stageKey
                ? { ...agent, status: "cancelled", endTime: agent.endTime ?? now }
                : { ...agent, status: "blocked", endTime: agent.endTime ?? now },
            ];
          }
          return [key, agent];
        }),
      );
      const reason = blocked.reason || action.message || "Request blocked by a guardrail.";
      const category = blocked.category ? ` (${blocked.category})` : "";
      return {
        ...state,
        isRunning: false,
        agents,
        result: null,
        error: null,
        blocked: {
          category: blocked.category || "",
          reason,
          stage: stageKey || "",
        },
        logs: [
          ...state.logs,
          makeLog(`Guardrail blocked the run${category}: ${reason}`, "warning"),
        ].slice(-MAX_LOGS),
      };
    }

    case "WORKFLOW_ABORTED":
      return { ...state, isRunning: false };

    case "CLEAR_INPUTS":
      return { ...state, files: [], result: null, error: null, blocked: null };
    case "CLEAR_AGENTS":
      return {
        ...state,
        agents: resetAgents(state.agents),
        result: null,
        error: null,
        blocked: null,
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
