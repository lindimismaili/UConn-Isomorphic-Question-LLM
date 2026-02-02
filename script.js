/**
 * Isomorphic Question Generator - Frontend JavaScript
 * Handles UI interactions, state management, and API communication
 */

// =============================================================================
// State Management
// =============================================================================

const state = {
  files: [],
  agents: {
    normalizer: {
      name: "Normalizer",
      description: "Parses user request into configuration",
      model: "gpt-4o",
      reasoningLevel: "none",
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
      name: "Extracter",
      description: "Reads files and extracts questions",
      model: "gpt-4o",
      reasoningLevel: "none",
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
      name: "Verifier",
      description: "Validates answers and adds explanations",
      model: "gpt-4o",
      reasoningLevel: "none",
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
      name: "Generator",
      description: "Creates isomorphic questions",
      model: "gpt-4o",
      reasoningLevel: "none",
      status: "inactive",
      startTime: null,
      endTime: null,
      executionTime: null,
      inputFrom: "Verifier",
      outputTo: "LaTeX Formatter",
      inputText: "",
      outputText: "",
    },
    latex: {
      name: "LaTeX Formatter",
      description: "Converts questions to LaTeX format",
      model: "gpt-4o",
      reasoningLevel: "none",
      status: "inactive",
      startTime: null,
      endTime: null,
      executionTime: null,
      inputFrom: "Generator",
      outputTo: "User",
      inputText: "",
      outputText: "",
    },
  },
  isProcessing: false,
  outputFiles: [],
  outputText: "",
  outputFolder: "",
  workflowId: null,
  sessionId: null,
  abortController: null, // For cancelling API requests
  sliders: {
    difficulty: 0,
    theme: 0,
    numericVariation: 0,
  },
  sliderConfig: null,
  agentsConfig: null, // Loaded from agents.json
};

const MAX_UPLOAD_FILES = 5;

// =============================================================================
// DOM Elements
// =============================================================================

const elements = {
  promptInput: document.getElementById("promptInput"),
  uploadBtn: document.getElementById("uploadBtn"),
  fileInput: document.getElementById("fileInput"),
  currentFilesBtn: document.getElementById("currentFilesBtn"),
  fileCount: document.getElementById("fileCount"),
  clearInputsBtn: document.getElementById("clearInputsBtn"),
  clearAgentsBtn: document.getElementById("clearAgentsBtn"),
  sendBtn: document.getElementById("sendBtn"),
  filesModal: document.getElementById("filesModal"),
  filesList: document.getElementById("filesList"),
  agentModal: document.getElementById("agentModal"),
  agentModalTitle: document.getElementById("agentModalTitle"),
  completedSection: document.getElementById("completedSection"),
  outputText: document.getElementById("outputText"),
  downloadsList: document.getElementById("downloadsList"),
  downloadFileCount: document.getElementById("downloadFileCount"),
  failureSection: document.getElementById("failureSection"),
  failureOutputText: document.getElementById("failureOutputText"),
  failureFolderPath: document.getElementById("failureFolderPath"),
  difficultySlider: document.getElementById("difficultySlider"),
  themeSlider: document.getElementById("themeSlider"),
  numericVariationSlider: document.getElementById("numericVariationSlider"),
};

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Load agents configuration from the server
 */
async function loadAgentsConfig() {
  try {
    state.agentsConfig = await fetchAgentsConfigData();
    // Update agent state with config data
    applyAgentsConfig();
  } catch (error) {
    console.warn("Failed to load agents config:", error);
  }
}

/**
 * Fetch agents config from API or local fallback
 */
async function fetchAgentsConfigData() {
  // Try API first
  try {
    const response = await fetch("/api/agents");
    if (response.ok) {
      return await response.json();
    }
  } catch (error) {
    console.log("API fetch failed, trying local fallback:", error.message);
  }

  // Fallback to local file
  try {
    const response = await fetch("prompts/agents.json");
    if (response.ok) {
      return await response.json();
    }
  } catch (error) {
    console.log("Local fallback failed:", error.message);
  }

  throw new Error("Could not load agents config");
}

/**
 * Apply loaded agents config to state
 */
function applyAgentsConfig() {
  if (!state.agentsConfig) return;

  for (const [key, config] of Object.entries(state.agentsConfig)) {
    if (state.agents[key]) {
      state.agents[key].name = config.name || state.agents[key].name;
      state.agents[key].model = config.model || state.agents[key].model;
      state.agents[key].reasoningLevel =
        config.reasoning || state.agents[key].reasoningLevel;
      state.agents[key].description = config.description || "";
      state.agents[key].inputFrom =
        config.inputFrom || state.agents[key].inputFrom;
      state.agents[key].outputTo =
        config.outputTo || state.agents[key].outputTo;
    }
  }
}

/**
 * Load slider configuration from the server
 */
async function loadSliderConfig() {
  try {
    state.sliderConfig = await fetchSlidersConfigData();
  } catch (error) {
    console.warn("Failed to load slider config, using defaults:", error);
    state.sliderConfig = getDefaultSliderConfig();
  }
}

/**
 * Get default slider configuration (fallback)
 */
function getDefaultSliderConfig() {
  return {
    difficulty: {
      levels: [
        "Set the difficulty of these questions to be similar to the original.",
        "Set the difficulty of these questions to be a little harder than the original.",
        "Set the difficulty of these questions to be decently harder than the original.",
        "Set the difficulty of these questions to be much harder than the original.",
      ],
    },
    theme: {
      levels: [
        "Keep the theme and context of these questions very similar to the original.",
        "Adjust the theme and context of these questions slightly from the original.",
        "Change the theme and context of these questions to be noticeably different from the original.",
        "Create a completely new theme and context for these questions, distinct from the original.",
      ],
    },
    numericVariation: {
      levels: [
        "Keep the numeric values very close to the original values.",
        "Vary the numeric values slightly from the original values.",
        "Vary the numeric values moderately from the original values.",
        "Vary the numeric values significantly, using a wide range of different values.",
      ],
    },
  };
}

/**
 * Build the final prompt by appending slider instructions
 */
function buildPromptWithSliders(userPrompt) {
  if (!state.sliderConfig) {
    return userPrompt;
  }

  const sliderTexts = [];

  // Get the text for each slider based on its current value
  if (state.sliderConfig.difficulty && state.sliders.difficulty >= 0) {
    const text = state.sliderConfig.difficulty.levels[state.sliders.difficulty];
    if (text) sliderTexts.push(text);
  }

  if (state.sliderConfig.theme && state.sliders.theme >= 0) {
    const text = state.sliderConfig.theme.levels[state.sliders.theme];
    if (text) sliderTexts.push(text);
  }

  if (
    state.sliderConfig.numericVariation &&
    state.sliders.numericVariation >= 0
  ) {
    const text =
      state.sliderConfig.numericVariation.levels[
        state.sliders.numericVariation
      ];
    if (text) sliderTexts.push(text);
  }

  // Append slider instructions to user prompt
  if (sliderTexts.length > 0) {
    const sliderInstructions = sliderTexts.join(" ");
    return userPrompt
      ? `${userPrompt}\n\n${sliderInstructions}`
      : sliderInstructions;
  }

  return userPrompt;
}

/**
 * Load and display an agent prompt or sliders config
 */
async function loadAndShowPrompt(promptName) {
  const titleEl = document.getElementById("promptViewerTitle");
  const contentEl = document.getElementById("promptViewerContent");

  // Map prompt names to display titles
  const titles = {
    normalizer: "Normalizer",
    extracter: "Extracter",
    verifier: "Verifier",
    generator: "Generator",
    latex: "LaTeX",
    python: "Python",
    sliders: "Sliders",
  };

  titleEl.textContent = titles[promptName] || "Agent Prompt";
  contentEl.textContent = "Loading...";

  // Open prompt viewer as overlay (don't close agents modal)
  openModal("promptViewerModal");

  try {
    const content =
      promptName === "sliders"
        ? formatSlidersForDisplay(await fetchSlidersConfigData())
        : await fetchPromptContent(promptName);

    contentEl.textContent = content;
  } catch (error) {
    contentEl.textContent = `Error loading prompt: ${error.message}`;
  }
}

/**
 * Fetch prompt text with API-first strategy and fallback to local files.
 */
async function fetchPromptContent(promptName) {
  try {
    const response = await fetch(`/api/prompts/${promptName}`);
    if (!response.ok) {
      throw new Error(`Server responded with ${response.status}`);
    }
    return await response.text();
  } catch (apiError) {
    console.warn(
      `Falling back to local prompt copy for ${promptName}:`,
      apiError,
    );
    try {
      return await fetchLocalPrompt(promptName);
    } catch (localError) {
      throw new Error(
        `API error: ${apiError.message}; Local fallback: ${localError.message}`,
      );
    }
  }
}

/**
 * Fetch the sliders config JSON using API-first strategy with local fallback.
 */
async function fetchSlidersConfigData() {
  try {
    const response = await fetch("/api/sliders");
    if (!response.ok) {
      throw new Error(`Server responded with ${response.status}`);
    }
    return await response.json();
  } catch (apiError) {
    console.warn("Falling back to local sliders config:", apiError);
    try {
      return await fetchLocalJson("prompts/sliders.json");
    } catch (localError) {
      throw new Error(
        `API error: ${apiError.message}; Local fallback: ${localError.message}`,
      );
    }
  }
}

/**
 * Fetch a local prompt file from the prompts directory.
 */
async function fetchLocalPrompt(promptName) {
  const response = await fetchLocalResource(`prompts/${promptName}.txt`);
  return await response.text();
}

/**
 * Fetch JSON from a local path relative to the current page.
 */
async function fetchLocalJson(relativePath) {
  const response = await fetchLocalResource(relativePath);
  return await response.json();
}

/**
 * Load a resource that lives next to the Site content.
 */
async function fetchLocalResource(relativePath) {
  const resourceUrl = new URL(relativePath, window.location.href).href;
  const response = await fetch(resourceUrl);
  if (!response.ok) {
    throw new Error(`Local resource unavailable (${response.status})`);
  }
  return response;
}

/**
 * Format sliders config for readable display
 */
function formatSlidersForDisplay(data) {
  let output = "";

  for (const [key, config] of Object.entries(data)) {
    output += `═══════════════════════════════════════════\n`;
    output += `${config.label || key.toUpperCase()}\n`;
    output += `Range: ${config.leftLabel} → ${config.rightLabel}\n`;
    output += `═══════════════════════════════════════════\n\n`;

    config.levels.forEach((level, index) => {
      output += `Level ${index + 1}:\n`;
      output += `  "${level}"\n\n`;
    });

    output += "\n";
  }

  return output.trim();
}

/**
 * Format text with proper newlines and tabs for display
 * If the text is valid JSON, format it with proper indentation
 */
function formatText(text) {
  if (!text) return "";

  // First, handle escaped newlines and tabs
  let processed = text
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "    ")
    .replace(/\\r/g, "");

  // Try to parse as JSON and pretty-print it
  try {
    const parsed = JSON.parse(processed);
    return JSON.stringify(parsed, null, 2);
  } catch (e) {
    // Not valid JSON, return as-is
    return processed;
  }
}

/**
 * Format a date to a readable string
 */
function formatDateTime(date) {
  if (!date) return "-";
  return new Date(date).toLocaleString();
}

/**
 * Format execution time in seconds
 */
function formatExecutionTime(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  return `${seconds.toFixed(2)}s`;
}

/**
 * Normalize the output folder path (always start with Outputs/)
 */
function formatOutputFolderPath(folder) {
  if (!folder) return "Outputs/";
  return folder.startsWith("Outputs/") ? folder : `Outputs/${folder}`;
}

/**
 * Generate a unique ID
 */
function generateId() {
  return Math.random().toString(36).substring(2, 11);
}

// =============================================================================
// File Management
// =============================================================================

/**
 * Handle file selection
 */
function handleFileSelect(event) {
  const selectedFiles = Array.from(event.target.files);
  const availableSlots = MAX_UPLOAD_FILES - state.files.length;

  if (availableSlots <= 0) {
    triggerUploadButtonWarning();
    event.target.value = "";
    return;
  }

  const allowedFiles = selectedFiles.slice(0, availableSlots);
  allowedFiles.forEach((file) => {
    const exists = state.files.some(
      (f) => f.name === file.name && f.size === file.size,
    );
    if (!exists) {
      state.files.push({
        id: generateId(),
        name: file.name,
        size: file.size,
        type: file.type,
        file: file,
      });
    }
  });

  if (selectedFiles.length > allowedFiles.length) {
    triggerUploadButtonWarning();
  }

  updateFilesUI();
  event.target.value = ""; // Reset input
}

/**
 * Remove a file from the list
 */
function removeFile(fileId) {
  state.files = state.files.filter((f) => f.id !== fileId);
  updateFilesUI();
  renderFilesList();

  // Close modal if no files left
  if (state.files.length === 0) {
    closeModal("filesModal");
  }
}

/**
 * Update files-related UI elements
 */
function updateFilesUI() {
  const hasFiles = state.files.length > 0;

  elements.currentFilesBtn.classList.toggle("hidden", !hasFiles);
  elements.fileCount.textContent = state.files.length;

  updateUploadButtonState();
}

function updateUploadButtonState() {
  if (!elements.uploadBtn) return;
  const limitReached = state.files.length >= MAX_UPLOAD_FILES;
  elements.uploadBtn.disabled = limitReached;
}

/**
 * Render the files list in the modal
 */
function renderFilesList() {
  elements.filesList.innerHTML = state.files
    .map(
      (file) => `
        <div class="file-item" data-file-id="${file.id}">
            <div class="file-item-info">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span class="file-item-name">${file.name}</span>
            </div>
            <button class="btn btn-remove btn-sm" onclick="removeFile('${file.id}')">
                Remove
            </button>
        </div>
    `,
    )
    .join("");

  if (state.files.length === 0) {
    elements.filesList.innerHTML =
      '<p style="color: var(--color-text-muted); text-align: center;">No files uploaded</p>';
  }
}

// =============================================================================
// Modal Management
// =============================================================================

/**
 * Resolve a modal reference from its id or element
 */
function getModalElement(modalIdOrElement) {
  if (!modalIdOrElement) return null;
  if (typeof modalIdOrElement === "string") {
    return document.getElementById(modalIdOrElement);
  }
  if (modalIdOrElement instanceof Element) {
    return modalIdOrElement;
  }
  return null;
}

/**
 * Open a modal
 */
function openModal(modalIdOrElement) {
  const modal = getModalElement(modalIdOrElement);
  if (modal) {
    modal.classList.add("is-visible");
    document.body.classList.add("modal-open");
    document.body.style.overflow = "hidden";
  }
}

/**
 * Close a modal
 */
function closeModal(modalIdOrElement) {
  const modal = getModalElement(modalIdOrElement);
  if (modal) {
    modal.classList.remove("is-visible");
    if (!document.querySelector(".modal.is-visible")) {
      document.body.classList.remove("modal-open");
      document.body.style.overflow = "";
    }
  }
}

/**
 * Open the agent details modal
 */
function openAgentModal(agentKey) {
  const agent = state.agents[agentKey];
  if (!agent) return;

  // Helper to show "Unavailable" for missing values
  const getValue = (val, defaultVal = "Unavailable") => {
    if (val === null || val === undefined || val === "" || val === "-")
      return defaultVal;
    return val;
  };

  // Populate modal with agent data
  elements.agentModalTitle.textContent = `${agent.name} Details`;
  document.getElementById("detailAgentName").textContent = getValue(agent.name);
  document.getElementById("detailDescription").textContent = getValue(
    agent.description,
  );
  document.getElementById("detailModel").textContent = getValue(agent.model);
  document.getElementById("detailReasoning").textContent = getValue(
    agent.reasoningLevel,
    "none",
  );
  document.getElementById("detailStatus").textContent = getValue(
    capitalizeFirst(agent.status),
  );
  document.getElementById("detailStartTime").textContent = agent.startTime
    ? formatDateTime(agent.startTime)
    : "Unavailable";
  document.getElementById("detailEndTime").textContent = agent.endTime
    ? formatDateTime(agent.endTime)
    : "Unavailable";
  document.getElementById("detailExecutionTime").textContent =
    agent.executionTime !== null
      ? formatExecutionTime(agent.executionTime)
      : "Unavailable";
  document.getElementById("detailInputFrom").textContent = getValue(
    agent.inputFrom,
  );
  document.getElementById("detailOutputTo").textContent = getValue(
    agent.outputTo,
  );
  document.getElementById("detailInputText").textContent =
    formatText(agent.inputText) || "No input data";
  document.getElementById("detailOutputText").textContent =
    formatText(agent.outputText) || "No output data";

  openModal("agentModal");
}

function capitalizeFirst(str) {
  if (!str) return "";
  return str.charAt(0).toUpperCase() + str.slice(1);
}

// =============================================================================
// Agent Status Management
// =============================================================================

/**
 * Update agent status
 */
function updateAgentStatus(agentKey, status, data = {}) {
  const agent = state.agents[agentKey];
  if (!agent) return;

  agent.status = status;

  if (data.startTime !== undefined) agent.startTime = data.startTime;
  if (data.endTime !== undefined) agent.endTime = data.endTime;
  if (data.executionTime !== undefined)
    agent.executionTime = data.executionTime;
  if (data.inputText !== undefined) agent.inputText = data.inputText;
  if (data.outputText !== undefined) agent.outputText = data.outputText;
  if (data.model !== undefined) agent.model = data.model;
  if (data.reasoningLevel !== undefined)
    agent.reasoningLevel = data.reasoningLevel;

  // Update UI
  const card = document.querySelector(`.agent-card[data-agent="${agentKey}"]`);
  if (card) {
    card.setAttribute("data-status", status);

    const indicator = card.querySelector(".status-indicator");
    if (indicator) {
      indicator.className = `status-indicator ${status}`;
    }

    const statusText = card.querySelector(".agent-status-text");
    if (statusText) {
      statusText.textContent = capitalizeFirst(status);
      statusText.style.color = getStatusColor(status);
    }
  }
}

/**
 * Get color for status
 */
function getStatusColor(status) {
  const colors = {
    inactive: "var(--color-text-muted)",
    queued: "#ffffff",
    thinking: "var(--color-warning)",
    completed: "var(--color-success)",
    errored: "var(--color-error)",
  };
  return colors[status] || colors.inactive;
}

/**
 * Reset all agents to inactive
 */
function resetAllAgents() {
  Object.keys(state.agents).forEach((key) => {
    const agent = state.agents[key];
    agent.status = "inactive";
    agent.startTime = null;
    agent.endTime = null;
    agent.executionTime = null;
    agent.inputText = "";
    agent.outputText = "";

    updateAgentStatus(key, "inactive");
  });
}

/**
 * Set all agents to queued status (white glow)
 */
function queueAllAgents() {
  Object.keys(state.agents).forEach((key) => {
    updateAgentStatus(key, "queued");
  });
}

// =============================================================================
// Workflow Management
// =============================================================================

/**
 * Abort any ongoing API requests and reset workflow state
 */
function abortWorkflow() {
  if (state.abortController) {
    state.abortController.abort();
    state.abortController = null;
  }
  state.isProcessing = false;
  state.workflowId = null;
  elements.sendBtn.disabled = false;
}

/**
 * Clear the prompt and uploaded files while leaving agents untouched.
 */
function clearInputs({ showFeedback = true } = {}) {
  // Abort any ongoing requests
  abortWorkflow();

  if (showFeedback) {
    triggerClearButtonFeedback(elements.clearInputsBtn);
  }
  elements.promptInput.value = "";
  state.files = [];
  updateFilesUI();
  renderFilesList();
  updateUploadButtonState();
  hideCompletedSection();
}

/**
 * Reset every agent back to the inactive state and abort any ongoing workflow.
 */
function clearAgents() {
  // Abort any ongoing requests
  abortWorkflow();

  triggerClearButtonFeedback(elements.clearAgentsBtn);
  resetAllAgents();
  hideCompletedSection();
}

/**
 * Show temporary feedback on a button (text change + glow)
 */
function triggerButtonFeedback(
  button,
  { text, className, duration = 1400, onReset } = {},
) {
  if (!button || !text) return;

  const label =
    button.querySelector(".btn-label") || button.querySelector("span");
  const target = label || button;
  const originalText =
    target.dataset.originalLabel ?? target.textContent.trim() ?? "";
  if (!target.dataset.originalLabel) {
    target.dataset.originalLabel = originalText;
  }

  const widthBefore = button.getBoundingClientRect().width;
  const widthAfter = measureButtonWidth(button, text);
  button.dataset.originalWidth = widthBefore;

  button.style.width = `${widthBefore}px`;
  button.offsetHeight;

  requestAnimationFrame(() => {
    target.textContent = text;
    if (className) {
      button.classList.add(className);
    }
    button.disabled = true;
    button.style.width = `${widthAfter}px`;
  });

  if (button._feedbackTimeoutId) {
    clearTimeout(button._feedbackTimeoutId);
  }

  button._feedbackTimeoutId = setTimeout(() => {
    const resetWidth =
      Number(button.dataset.originalWidth) ||
      button.getBoundingClientRect().width;
    button.style.width = `${resetWidth}px`;
    target.textContent = target.dataset.originalLabel || originalText;
    if (className) {
      button.classList.remove(className);
    }
    button.disabled = false;
    button._feedbackTimeoutId = null;
    setTimeout(() => {
      button.style.width = "";
    }, 300);
    if (typeof onReset === "function") {
      onReset();
    }
  }, duration);
}

function triggerClearButtonFeedback(button) {
  triggerButtonFeedback(button, {
    text: "Cleared",
    className: "btn-feedback",
    duration: 1400,
  });
}

function triggerUploadButtonWarning() {
  triggerButtonFeedback(elements.uploadBtn, {
    text: "Too many files!",
    className: "btn-error-feedback",
    duration: 1600,
    onReset: updateUploadButtonState,
  });
}

function measureButtonWidth(button, text) {
  const clone = button.cloneNode(true);
  clone.style.position = "absolute";
  clone.style.visibility = "hidden";
  clone.style.top = "-9999px";
  clone.style.left = "-9999px";
  clone.style.width = "auto";
  const cloneLabel = clone.querySelector(".btn-label");
  if (cloneLabel) {
    cloneLabel.textContent = text;
  } else {
    clone.textContent = text;
  }
  document.body.appendChild(clone);
  const width = clone.getBoundingClientRect().width;
  clone.remove();
  return width;
}

/**
 * Send the workflow request
 */
async function sendWorkflow() {
  if (state.isProcessing) return;

  const userPrompt = elements.promptInput.value.trim();

  if (!userPrompt && state.files.length === 0) {
    alert("Please enter a prompt or upload files.");
    return;
  }

  // Build prompt with slider instructions appended
  const prompt = buildPromptWithSliders(userPrompt);

  const filesSnapshot = [...state.files];

  state.isProcessing = true;
  elements.sendBtn.disabled = true;
  resetAllAgents();
  queueAllAgents();  // Show white glow on all agents immediately
  hideCompletedSection();
  hideFailureSection();
  clearInputs({ showFeedback: false });

  try {
    // Run the real workflow
    await executeWorkflow(prompt, filesSnapshot);
  } catch (error) {
    console.error("Workflow error:", error);
    showFailureSection({
      outputText:
        `Workflow failed before completion.\n${error.message || ""}`.trim(),
    });
    alert("An error occurred during processing: " + error.message);
  } finally {
    state.isProcessing = false;
    elements.sendBtn.disabled = false;
  }
}

/**
 * Execute the real workflow via API
 */
async function executeWorkflow(prompt, files = []) {
  // Create an AbortController for this workflow
  state.abortController = new AbortController();
  const signal = state.abortController.signal;

  try {
    // Step 1: Upload files if any
    if (files.length > 0) {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f.file));

      console.log(
        "Uploading files:",
        files.map((f) => f.name),
      );

      let uploadResponse;
      try {
        uploadResponse = await fetch("/api/upload", {
          method: "POST",
          body: formData,
          signal,
        });
      } catch (networkError) {
        if (networkError.name === "AbortError") {
          console.log("Upload aborted by user");
          return;
        }
        console.error("Network error during upload:", networkError);
        throw new Error(
          `Upload network error: ${networkError.message}. Check if the server is running and accessible.`,
        );
      }

      if (!uploadResponse.ok) {
        let errorDetail = `Status: ${uploadResponse.status}`;
        try {
          const errorJson = await uploadResponse.json();
          errorDetail =
            errorJson.detail || errorJson.message || JSON.stringify(errorJson);
        } catch {
          try {
            errorDetail = await uploadResponse.text();
          } catch {
            // Keep status code as error detail
          }
        }
        console.error("Upload failed:", errorDetail);
        throw new Error(`Failed to upload files: ${errorDetail}`);
      }

      const uploadResult = await uploadResponse.json();
      console.log("Upload successful:", uploadResult);
      state.sessionId = uploadResult.sessionId;
    }

    // Step 2: Start the workflow
    const formData = new FormData();
    formData.append("prompt", prompt);
    formData.append("sessionId", state.sessionId || "");

    let startResponse;
    try {
      startResponse = await fetch("/api/workflow/start", {
        method: "POST",
        body: formData,
        signal,
      });
    } catch (networkError) {
      if (networkError.name === "AbortError") {
        console.log("Workflow start aborted by user");
        return;
      }
      console.error("Network error starting workflow:", networkError);
      throw new Error(`Workflow start network error: ${networkError.message}`);
    }

    if (!startResponse.ok) {
      let errorDetail = `Status: ${startResponse.status}`;
      try {
        const errorJson = await startResponse.json();
        errorDetail =
          errorJson.detail || errorJson.message || JSON.stringify(errorJson);
      } catch {
        try {
          errorDetail = await startResponse.text();
        } catch {
          // Keep status code
        }
      }
      console.error("Workflow start failed:", errorDetail);
      throw new Error(`Failed to start workflow: ${errorDetail}`);
    }

    const startResult = await startResponse.json();
    state.workflowId = startResult.workflowId;

    // Step 3: Poll for status updates
    await pollWorkflowStatus(state.workflowId);
  } catch (error) {
    console.error("Workflow error:", error);
    // Mark any thinking agents as errored
    Object.keys(state.agents).forEach((key) => {
      if (state.agents[key].status === "thinking") {
        updateAgentStatus(key, "errored");
      }
    });
    throw error;
  }
}

/**
 * Poll for workflow status updates
 */
async function pollWorkflowStatus(workflowId) {
  const agents = ["normalizer", "extracter", "verifier", "generator", "latex"];
  const signal = state.abortController?.signal;

  while (true) {
    // Check if aborted
    if (signal?.aborted) {
      console.log("Polling aborted by user");
      return;
    }

    let response;
    try {
      response = await fetch(`/api/workflow/${workflowId}/status`, { signal });
    } catch (error) {
      if (error.name === "AbortError") {
        console.log("Status poll aborted by user");
        return;
      }
      throw error;
    }

    if (!response.ok) {
      throw new Error("Failed to get workflow status");
    }

    const status = await response.json();

    // Update agent statuses
    for (const agentKey of agents) {
      const agentStatus = status.agents[agentKey];
      if (agentStatus) {
        const data = agentStatus.data || {};
        updateAgentStatus(agentKey, agentStatus.status, {
          startTime: data.startTime ? new Date(data.startTime) : null,
          endTime: data.endTime ? new Date(data.endTime) : null,
          executionTime: data.executionTime || null,
          outputText: data.output || "",
        });
      }
    }

    // Check if completed or errored
    if (status.status === "completed") {
      // Get final results
      let resultResponse;
      try {
        resultResponse = await fetch(`/api/workflow/${workflowId}/result`, {
          signal,
        });
      } catch (error) {
        if (error.name === "AbortError") {
          console.log("Result fetch aborted by user");
          return;
        }
        throw error;
      }

      if (resultResponse.ok) {
        const result = await resultResponse.json();
        showCompletedSection({
          outputText: `Workflow completed successfully!\n\nExtracted: ${result.result?.source_questions?.length || 0} questions\nVerified: ${result.result?.verified_questions?.length || 0} questions\nGenerated: ${result.result?.generated_questions?.length || 0} isomorphic questions\nLaTeX file: ${result.result?.latex_file || "isomorphic_questions.tex"}`,
          outputFolder: result.outputFolder,
          files: result.files || [],
        });
      }
      break;
    } else if (status.status === "errored") {
      throw new Error("Workflow failed");
    }

    // Wait before next poll (with abort check)
    try {
      await sleep(1000, signal);
    } catch (error) {
      if (error.name === "AbortError") {
        console.log("Sleep aborted by user");
        return;
      }
      throw error;
    }
  }
}

/**
 * Sleep utility with abort support
 */
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener("abort", () => {
        clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      });
    }
  });
}

// =============================================================================
// Completed Section
// =============================================================================

/**
 * Show the completed section with results
 */
function showCompletedSection(data) {
  hideFailureSection();
  elements.completedSection.classList.remove("hidden");

  // Set output text
  elements.outputText.textContent = formatText(data.outputText || "");

  // Set download files
  state.outputFiles = data.files || [];
  elements.downloadFileCount.textContent = `${state.outputFiles.length} file${state.outputFiles.length !== 1 ? "s" : ""}`;

  // Set output folder path
  const resolvedFolder = formatOutputFolderPath(data.outputFolder);
  state.outputFolder = resolvedFolder;
  const outputFolderPath = document.getElementById("outputFolderPath");
  if (outputFolderPath) {
    outputFolderPath.textContent = resolvedFolder;
  }

  renderDownloadsList();

  // Scroll to completed section
  elements.completedSection.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

/**
 * Hide the completed section
 */
function hideCompletedSection() {
  elements.completedSection.classList.add("hidden");
  state.outputFiles = [];
  elements.outputText.textContent = "";
  elements.downloadsList.innerHTML = "";
  elements.downloadFileCount.textContent = "0 files";
  state.outputFolder = "";
}

/**
 * Show the failure card when workflow stops early
 */
function showFailureSection(data = {}) {
  if (!elements.failureSection) return;
  hideCompletedSection();
  elements.failureSection.classList.remove("hidden");

  const message =
    data.outputText ||
    "The workflow stopped before the full pipeline completed. Review the status and try again.";
  elements.failureOutputText.textContent = formatText(message);

  const folderValue = formatOutputFolderPath(
    data.outputFolder || state.outputFolder,
  );
  state.outputFolder = folderValue;
  if (elements.failureFolderPath) {
    elements.failureFolderPath.textContent = folderValue;
  }

  elements.failureSection.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

/**
 * Hide the failure section
 */
function hideFailureSection() {
  if (!elements.failureSection) return;
  elements.failureSection.classList.add("hidden");
  if (elements.failureOutputText) {
    elements.failureOutputText.textContent = "";
  }
}

/**
 * Render the downloads list
 */
function renderDownloadsList() {
  elements.downloadsList.innerHTML = state.outputFiles
    .map(
      (file) => `
        <div class="download-item">
            <div class="download-item-info">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span class="download-item-name">${file.name}</span>
            </div>
            <button class="btn btn-primary btn-download" onclick="downloadFile('${file.name}')">
                <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Download
            </button>
        </div>
    `,
    )
    .join("");
}

/**
 * Download a file from the server
 */
function downloadFile(fileName) {
  if (!state.workflowId) {
    alert("No workflow results available");
    return;
  }

  // Get the output folder from the workflow
  const outputFolder =
    document
      .getElementById("outputFolderPath")
      ?.textContent?.replace("Outputs/", "") || "";

  // Create download link
  const downloadUrl = `/api/download/${state.workflowId}/${fileName}`;

  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// =============================================================================
// API Functions (Placeholders)
// =============================================================================

/**
 * Upload files to the server
 */
async function uploadFiles(files) {
  // TODO: Implement file upload to server
  console.log("Uploading files:", files);
  return { success: true, fileIds: files.map(() => generateId()) };
}

/**
 * Start the workflow on the server
 */
async function startWorkflow(prompt, fileIds) {
  // TODO: Implement workflow start API call
  console.log("Starting workflow:", { prompt, fileIds });
  return { success: true, workflowId: generateId() };
}

/**
 * Poll for workflow status
 */
async function getWorkflowStatus(workflowId) {
  // TODO: Implement status polling
  console.log("Getting workflow status:", workflowId);
  return { status: "completed", agents: state.agents };
}

/**
 * Get workflow results
 */
async function getWorkflowResults(workflowId) {
  // TODO: Implement results retrieval
  console.log("Getting workflow results:", workflowId);
  return { outputText: "", files: [] };
}

// =============================================================================
// Event Listeners
// =============================================================================

document.addEventListener("DOMContentLoaded", () => {
  // Load configurations
  loadSliderConfig();
  loadAgentsConfig();

  // File upload
  elements.uploadBtn.addEventListener("click", () =>
    elements.fileInput.click(),
  );
  elements.fileInput.addEventListener("change", handleFileSelect);

  // Current files button
  elements.currentFilesBtn.addEventListener("click", () => {
    renderFilesList();
    openModal("filesModal");
  });

  // Clear inputs button
  if (elements.clearInputsBtn) {
    elements.clearInputsBtn.addEventListener("click", clearInputs);
  }

  if (elements.clearAgentsBtn) {
    elements.clearAgentsBtn.addEventListener("click", clearAgents);
  }

  // Send button
  elements.sendBtn.addEventListener("click", sendWorkflow);

  // Slider event listeners
  if (elements.difficultySlider) {
    elements.difficultySlider.addEventListener("input", (e) => {
      state.sliders.difficulty = parseInt(e.target.value, 10);
    });
  }

  if (elements.themeSlider) {
    elements.themeSlider.addEventListener("input", (e) => {
      state.sliders.theme = parseInt(e.target.value, 10);
    });
  }

  if (elements.numericVariationSlider) {
    elements.numericVariationSlider.addEventListener("input", (e) => {
      state.sliders.numericVariation = parseInt(e.target.value, 10);
    });
  }

  // Help button
  const helpBtn = document.getElementById("helpBtn");
  if (helpBtn) {
    helpBtn.addEventListener("click", () => openModal("helpModal"));
  }

  // Agents button
  const agentsBtn = document.getElementById("agentsBtn");
  if (agentsBtn) {
    agentsBtn.addEventListener("click", () => openModal("agentsModal"));
  }

  // Agent list items - load prompt when clicked
  document.querySelectorAll(".agent-list-item").forEach((item) => {
    item.addEventListener("click", () => {
      const promptName = item.getAttribute("data-prompt");
      loadAndShowPrompt(promptName);
    });
  });

  // Back to agents list button (just close overlay, agents modal is still open)
  const backToAgentsBtn = document.getElementById("backToAgentsBtn");
  if (backToAgentsBtn) {
    backToAgentsBtn.addEventListener("click", () => {
      closeModal("promptViewerModal");
    });
  }

  // Clickable agent cards
  document.querySelectorAll(".agent-card.clickable").forEach((card) => {
    card.addEventListener("click", () => {
      const agentKey = card.getAttribute("data-agent");
      openAgentModal(agentKey);
    });
  });

  // Modal close buttons
  document.querySelectorAll(".close-modal").forEach((btn) => {
    btn.addEventListener("click", () => {
      const modalId = btn.getAttribute("data-modal");
      closeModal(modalId);
    });
  });

  // Close modals on backdrop click
  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", () => {
      const modal = backdrop.closest(".modal");
      if (modal) {
        closeModal(modal);
      }
    });
  });

  // Close modals on Escape key
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal.is-visible").forEach((modal) => {
        closeModal(modal);
      });
    }
  });

  // Keyboard shortcut for send (Ctrl+Enter)
  elements.promptInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      sendWorkflow();
    }
  });
});

// Make functions available globally for onclick handlers
window.removeFile = removeFile;
window.downloadFile = downloadFile;
window.openAgentModal = openAgentModal;
window.closeModal = closeModal;
