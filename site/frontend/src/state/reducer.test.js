/* @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest";

// The reducer's AGENT_UPDATE auto-advance reads getDerivedRegistry(). Mock it so
// the test controls the next-agent map without loading /api/agents.
vi.mock("../hooks/useAgentRegistry.js", () => ({
  getDerivedRegistry: () => ({
    nextAgentByKey: { setup: "normalizer", normalizer: "extracter" },
    registry: {},
  }),
}));

import {
  INITIAL_STATE,
  MAX_LOGS,
  _emptyAgentState,
  firstDefined,
  makeLog,
  queueAgents,
  reducer,
  resetAgents,
} from "./reducer.js";

/** Minimal two-agent registry state used across action tests. */
function baseAgents() {
  return {
    setup: { ...(_emptyAgentState()), name: "Setup", kind: "runtime" },
    normalizer: { ...(_emptyAgentState()), name: "Normalizer" },
    extracter: { ...(_emptyAgentState()), name: "Extracter" },
  };
}

function stateWith(overrides = {}) {
  return { ...INITIAL_STATE, logs: [], agents: baseAgents(), ...overrides };
}

describe("helpers", () => {
  it("firstDefined skips undefined/null/empty and returns first meaningful value", () => {
    expect(firstDefined(undefined, null, "", "x", "y")).toBe("x");
    expect(firstDefined(undefined, null, "")).toBeUndefined();
    expect(firstDefined(0)).toBe(0);
  });

  it("makeLog stamps id/time/message/level", () => {
    const log = makeLog("hi", "info");
    expect(log).toMatchObject({ message: "hi", level: "info" });
    expect(typeof log.id).toBe("string");
    expect(typeof log.time).toBe("string");
  });

  it("_emptyAgentState is inactive with cleared timing", () => {
    expect(_emptyAgentState()).toMatchObject({ status: "inactive", startTime: null, endTime: null });
  });

  it("resetAgents clears status/timing/text on every agent", () => {
    const reset = resetAgents({
      a: { name: "A", status: "completed", startTime: 1, endTime: 2, executionTime: 3, inputText: "i", outputText: "o" },
    });
    expect(reset.a).toMatchObject({
      name: "A",
      status: "inactive",
      startTime: null,
      endTime: null,
      executionTime: null,
      inputText: "",
      outputText: "",
    });
  });

  it("queueAgents queues everything except excluded keys", () => {
    const queued = queueAgents({ a: { status: "x" }, b: { status: "y" } }, ["a"]);
    expect(queued.a.status).toBe("x");
    expect(queued.b.status).toBe("queued");
  });
});

describe("INITIAL_STATE", () => {
  it("seeds a connection-established info log and empty collections", () => {
    expect(INITIAL_STATE.files).toEqual([]);
    expect(INITIAL_STATE.isRunning).toBe(false);
    expect(INITIAL_STATE.logs).toHaveLength(1);
    expect(INITIAL_STATE.logs[0]).toMatchObject({ level: "info" });
  });

  it("unknown action returns state unchanged", () => {
    const s = stateWith();
    expect(reducer(s, { type: "NOPE" })).toBe(s);
  });
});

describe("file actions", () => {
  it("ADD_FILES appends", () => {
    const s = stateWith({ files: [{ id: "1" }] });
    const next = reducer(s, { type: "ADD_FILES", files: [{ id: "2" }, { id: "3" }] });
    expect(next.files.map((f) => f.id)).toEqual(["1", "2", "3"]);
  });

  it("REMOVE_FILE removes by id", () => {
    const s = stateWith({ files: [{ id: "1" }, { id: "2" }] });
    expect(reducer(s, { type: "REMOVE_FILE", fileId: "1" }).files).toEqual([{ id: "2" }]);
  });

  it("CLEAR_FILES empties the list", () => {
    const s = stateWith({ files: [{ id: "1" }] });
    expect(reducer(s, { type: "CLEAR_FILES" }).files).toEqual([]);
  });

  it("UPLOAD_WARNING is a no-op", () => {
    const s = stateWith();
    expect(reducer(s, { type: "UPLOAD_WARNING" })).toBe(s);
  });
});

describe("modal actions", () => {
  it("OPEN_MODAL sets modal + optional agent key", () => {
    const next = reducer(stateWith(), { type: "OPEN_MODAL", modal: "agent", agentKey: "normalizer" });
    expect(next.activeModal).toBe("agent");
    expect(next.activeAgentKey).toBe("normalizer");
  });

  it("OPEN_MODAL defaults agentKey to null", () => {
    const next = reducer(stateWith(), { type: "OPEN_MODAL", modal: "help" });
    expect(next.activeAgentKey).toBeNull();
  });

  it("CLOSE_MODAL clears both", () => {
    const s = stateWith({ activeModal: "help", activeAgentKey: "x" });
    const next = reducer(s, { type: "CLOSE_MODAL" });
    expect(next.activeModal).toBeNull();
    expect(next.activeAgentKey).toBeNull();
  });
});

describe("AGENTS_LOADED", () => {
  it("merges registry config into per-agent UI state with reasoning fallback", () => {
    const s = stateWith({ agents: {} });
    const next = reducer(s, {
      type: "AGENTS_LOADED",
      config: {
        normalizer: { name: "Normalizer", model: "gpt-5.5", reasoning: "medium", description: "d", kind: "agent" },
        setup: { name: "Setup", kind: "runtime" },
      },
    });
    expect(next.agents.normalizer).toMatchObject({
      name: "Normalizer",
      model: "gpt-5.5",
      reasoningLevel: "medium",
      description: "d",
      kind: "agent",
    });
    // For a fresh agent, `_emptyAgentState().reasoningLevel` ("Unavailable")
    // is a defined non-empty value, so firstDefined() keeps it (the `|| "none"`
    // fallback only applies when every candidate is empty/nullish).
    expect(next.agents.setup.reasoningLevel).toBe("Unavailable");
    expect(next.agents.setup.kind).toBe("runtime");
  });

  it("falls back reasoningLevel to 'none' only when existing is empty too", () => {
    const s = stateWith({ agents: { normalizer: { ...(_emptyAgentState()), reasoningLevel: "" } } });
    const next = reducer(s, { type: "AGENTS_LOADED", config: { normalizer: { name: "N" } } });
    expect(next.agents.normalizer.reasoningLevel).toBe("none");
  });
});

describe("WORKFLOW_START", () => {
  it("resets, marks setup thinking, queues the rest, captures runInputs, clears files", () => {
    const s = stateWith({ files: [{ id: "1" }], result: { ok: true }, error: "boom" });
    const next = reducer(s, {
      type: "WORKFLOW_START",
      prompt: "make it harder",
      fileNames: ["a.pdf"],
      startAgent: "normalizer",
    });
    expect(next.isRunning).toBe(true);
    expect(next.workflowId).toBeNull();
    expect(next.result).toBeNull();
    expect(next.error).toBeNull();
    expect(next.files).toEqual([]);
    expect(next.agents.setup.status).toBe("thinking");
    expect(next.agents.setup.startTime).toBeInstanceOf(Date);
    expect(next.agents.normalizer.status).toBe("queued");
    expect(next.agents.extracter.status).toBe("queued");
    expect(next.runInputs).toEqual({ prompt: "make it harder", files: ["a.pdf"], startAgent: "normalizer" });
    // Logs: "Process starting..." + "Setup started".
    expect(next.logs.map((l) => l.message)).toEqual(["Process starting...", "Setup started"]);
  });
});

describe("WORKFLOW_REGISTERED", () => {
  it("stores the workflow id", () => {
    const next = reducer(stateWith(), { type: "WORKFLOW_REGISTERED", workflowId: "wf-1" });
    expect(next.workflowId).toBe("wf-1");
  });
});

describe("SETUP_COMPLETE", () => {
  it("completes the thinking setup node and logs success", () => {
    const s = stateWith({
      agents: { ...baseAgents(), setup: { ...(_emptyAgentState()), name: "Setup", status: "thinking", startTime: new Date(Date.now() - 1000) } },
    });
    const next = reducer(s, { type: "SETUP_COMPLETE" });
    expect(next.agents.setup.status).toBe("completed");
    expect(next.agents.setup.endTime).toBeInstanceOf(Date);
    expect(typeof next.agents.setup.executionTime).toBe("number");
    expect(next.logs.at(-1).level).toBe("success");
  });

  it("is idempotent when setup is not thinking", () => {
    const s = stateWith({ agents: { ...baseAgents(), setup: { ...(_emptyAgentState()), status: "completed" } } });
    expect(reducer(s, { type: "SETUP_COMPLETE" })).toBe(s);
  });
});

describe("AGENT_UPDATE", () => {
  it("applies status + data patch, dropping undefined fields", () => {
    const s = stateWith();
    const next = reducer(s, {
      type: "AGENT_UPDATE",
      agentKey: "normalizer",
      status: "thinking",
      data: { inputText: "in", outputText: undefined },
    });
    expect(next.agents.normalizer.status).toBe("thinking");
    expect(next.agents.normalizer.inputText).toBe("in");
    // undefined is filtered out, so it stays the prior empty string, not undefined.
    expect(next.agents.normalizer.outputText).toBe("");
  });

  it("ignores updates for unknown agent keys", () => {
    const s = stateWith();
    expect(reducer(s, { type: "AGENT_UPDATE", agentKey: "ghost", status: "thinking" })).toBe(s);
  });

  it("does not drag an advanced agent backwards to inactive/queued", () => {
    const s = stateWith({ agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), status: "completed" } } });
    const next = reducer(s, { type: "AGENT_UPDATE", agentKey: "normalizer", status: "inactive" });
    expect(next.agents.normalizer.status).toBe("completed");
    const next2 = reducer(s, { type: "AGENT_UPDATE", agentKey: "normalizer", status: "queued" });
    expect(next2.agents.normalizer.status).toBe("completed");
  });

  it("auto-advances the next agent to thinking when one completes", () => {
    const s = stateWith({ agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), status: "thinking" }, extracter: { ...(_emptyAgentState()), status: "queued" } } });
    const next = reducer(s, { type: "AGENT_UPDATE", agentKey: "normalizer", status: "completed" });
    expect(next.agents.normalizer.status).toBe("completed");
    expect(next.agents.extracter.status).toBe("thinking");
    expect(next.agents.extracter.startTime).toBeInstanceOf(Date);
    expect(next.agents.extracter.endTime).toBeNull();
  });

  it("does not auto-advance a next agent that already advanced", () => {
    const s = stateWith({ agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), status: "thinking" }, extracter: { ...(_emptyAgentState()), status: "completed" } } });
    const next = reducer(s, { type: "AGENT_UPDATE", agentKey: "normalizer", status: "completed" });
    expect(next.agents.extracter.status).toBe("completed");
  });
});

describe("WORKFLOW_COMPLETE", () => {
  it("stops running, completes lingering thinking agents, sets result + success log", () => {
    const s = stateWith({ agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), status: "thinking" } }, isRunning: true });
    const next = reducer(s, { type: "WORKFLOW_COMPLETE", result: { done: true }, elapsedTimeSeconds: 12.3 });
    expect(next.isRunning).toBe(false);
    expect(next.result).toEqual({ done: true });
    expect(next.agents.normalizer.status).toBe("completed");
    expect(next.agents.normalizer.endTime).toBeInstanceOf(Date);
    expect(next.logs.at(-1).message).toContain("12.30s");
    expect(next.logs.at(-1).level).toBe("success");
  });
});

describe("WORKFLOW_ERROR", () => {
  it("marks thinking agents errored, sets error + folder, logs errors", () => {
    const s = stateWith({
      agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), name: "Normalizer", status: "thinking", startTime: new Date(Date.now() - 500) } },
      isRunning: true,
    });
    const next = reducer(s, { type: "WORKFLOW_ERROR", message: "kaboom", outputFolder: "outputs/x" });
    expect(next.isRunning).toBe(false);
    expect(next.result).toBeNull();
    expect(next.errorFolder).toBe("outputs/x");
    expect(next.error).toContain("kaboom");
    expect(next.agents.normalizer.status).toBe("errored");
    // A per-agent errored log + a workflow error log.
    const errorMsgs = next.logs.filter((l) => l.level === "error").map((l) => l.message);
    expect(errorMsgs.some((m) => m.includes("Normalizer errored"))).toBe(true);
    expect(errorMsgs.some((m) => m.includes("Workflow error: kaboom"))).toBe(true);
  });
});

describe("WORKFLOW_BLOCKED", () => {
  it("marks the tripped guardrail node blocked, stops running, records the block, logs a warning", () => {
    const s = stateWith({
      agents: {
        ...baseAgents(),
        normalizer: { ...(_emptyAgentState()), name: "Normalizer", status: "completed" },
        extracter: { ...(_emptyAgentState()), name: "Extracter", status: "thinking" },
      },
      isRunning: true,
    });
    const next = reducer(s, {
      type: "WORKFLOW_BLOCKED",
      blocked: { category: "policy", reason: "Off-topic request.", stage: "extracter" },
    });
    expect(next.isRunning).toBe(false);
    expect(next.result).toBeNull();
    expect(next.agents.extracter.status).toBe("blocked");
    expect(next.agents.extracter.endTime).toBeInstanceOf(Date);
    expect(next.blocked).toEqual({ category: "policy", reason: "Off-topic request.", stage: "extracter" });
    const last = next.logs.at(-1);
    expect(last.level).toBe("warning");
    expect(last.message).toContain("Off-topic request.");
    expect(last.message).toContain("policy");
  });

  it("cancels other thinking nodes when a specific stage tripped", () => {
    const s = stateWith({
      agents: {
        ...baseAgents(),
        normalizer: { ...(_emptyAgentState()), status: "thinking" },
        extracter: { ...(_emptyAgentState()), status: "thinking" },
      },
      isRunning: true,
    });
    const next = reducer(s, {
      type: "WORKFLOW_BLOCKED",
      blocked: { reason: "blocked", stage: "extracter" },
    });
    expect(next.agents.extracter.status).toBe("blocked");
    expect(next.agents.normalizer.status).toBe("cancelled");
  });

  it("blocks the current thinking node when no stage is provided", () => {
    const s = stateWith({
      agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), status: "thinking" } },
      isRunning: true,
    });
    const next = reducer(s, { type: "WORKFLOW_BLOCKED", blocked: { reason: "denied" } });
    expect(next.agents.normalizer.status).toBe("blocked");
    expect(next.blocked.reason).toBe("denied");
    expect(next.blocked.stage).toBe("");
  });

  it("WORKFLOW_START and CLEAR_AGENTS reset a prior block", () => {
    const s = stateWith({ blocked: { category: "policy", reason: "x", stage: "extracter" } });
    expect(reducer(s, { type: "WORKFLOW_START", prompt: "", fileNames: [] }).blocked).toBeNull();
    expect(reducer(s, { type: "CLEAR_AGENTS" }).blocked).toBeNull();
    expect(reducer(s, { type: "CLEAR_INPUTS" }).blocked).toBeNull();
  });
});

describe("WORKFLOW_ABORTED", () => {
  it("just clears the running flag", () => {
    const s = stateWith({ isRunning: true });
    expect(reducer(s, { type: "WORKFLOW_ABORTED" }).isRunning).toBe(false);
  });
});

describe("clear + log actions", () => {
  it("CLEAR_INPUTS clears files/result/error", () => {
    const s = stateWith({ files: [{ id: "1" }], result: { a: 1 }, error: "e" });
    const next = reducer(s, { type: "CLEAR_INPUTS" });
    expect(next.files).toEqual([]);
    expect(next.result).toBeNull();
    expect(next.error).toBeNull();
  });

  it("CLEAR_AGENTS resets agents and clears result/error", () => {
    const s = stateWith({ agents: { ...baseAgents(), normalizer: { ...(_emptyAgentState()), status: "completed" } }, result: { a: 1 }, error: "e" });
    const next = reducer(s, { type: "CLEAR_AGENTS" });
    expect(next.agents.normalizer.status).toBe("inactive");
    expect(next.result).toBeNull();
    expect(next.error).toBeNull();
  });

  it("CLEAR_LOGS empties logs", () => {
    const s = stateWith({ logs: [makeLog("x", "info")] });
    expect(reducer(s, { type: "CLEAR_LOGS" }).logs).toEqual([]);
  });

  it("LOG appends a stamped entry", () => {
    const s = stateWith({ logs: [] });
    const next = reducer(s, { type: "LOG", message: "hello", level: "warning" });
    expect(next.logs.at(-1)).toMatchObject({ message: "hello", level: "warning" });
  });

  it("LOG caps retained entries at MAX_LOGS", () => {
    const logs = Array.from({ length: MAX_LOGS }, (_, i) => makeLog(`l${i}`, "info"));
    const s = stateWith({ logs });
    const next = reducer(s, { type: "LOG", message: "overflow", level: "info" });
    expect(next.logs).toHaveLength(MAX_LOGS);
    expect(next.logs.at(-1).message).toBe("overflow");
  });
});
