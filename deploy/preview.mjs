#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoDir = resolve(scriptDir, "..");
const siteDir = join(repoDir, "site");
const frontendDir = join(siteDir, "frontend");
const previewOriginsScript = join(scriptDir, "preview_origins.py");
const isWindows = process.platform === "win32";
const npmCommand = isWindows ? (process.env.ComSpec ?? "cmd.exe") : "npm";
const npmArgs = isWindows ? ["/d", "/s", "/c", "npm"] : [];
let mode = "--dev";
let frontendHost = "127.0.0.1";
let lanMode = false;
let showHelp = false;
const localDomain = process.env.LOCAL_DOMAIN ?? "localhost";
const backendHost = process.env.BACKEND_HOST ?? "127.0.0.1";
const preferredBackendPort = numberFromEnv("BACKEND_PORT", 8000);
const preferredDevPort = numberFromEnv("PORT", 5173);
const preferredWebPort = numberFromEnv("WEB_PORT", 8000);
const children = new Set();

function info(message) {
  console.log(`[preview] ${message}`);
}

function warn(message) {
  console.warn(`[preview] ${message}`);
}

function fail(message) {
  console.error(`[preview] ${message}`);
  process.exitCode = 1;
}

function parseArguments(args) {
  let mode = "--dev";
  let modeWasSet = false;
  let lanMode = false;
  let showHelp = false;

  for (const argument of args) {
    if (argument === "--dev" || argument === "dev") {
      if (modeWasSet && mode !== "--dev") throw new Error("Choose only one preview mode.");
      mode = "--dev";
      modeWasSet = true;
    } else if (argument === "--web" || argument === "web") {
      if (modeWasSet && mode !== "--web") throw new Error("Choose only one preview mode.");
      mode = "--web";
      modeWasSet = true;
    } else if (argument === "--lan") {
      lanMode = true;
    } else if (argument === "--help" || argument === "-h") {
      showHelp = true;
    } else {
      throw new Error(`Unknown preview option: ${argument}`);
    }
  }

  if (mode === "--web" && lanMode) throw new Error("--lan is only supported with --dev.");
  return {
    mode,
    frontendHost: lanMode ? "0.0.0.0" : "127.0.0.1",
    lanMode,
    showHelp,
  };
}

function numberFromEnv(name, fallback) {
  const value = Number.parseInt(process.env[name] ?? `${fallback}`, 10);
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be a port number from 1 to 65535.`);
  }
  return value;
}

function requireLoopbackBackendHost() {
  const normalizedHost = backendHost.trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (!["127.0.0.1", "::1", "localhost"].includes(normalizedHost)) {
    throw new Error(
      "BACKEND_HOST must remain loopback-only for preview. "
      + "Use --lan to expose the Vite proxy with an exact origin and Access Gate token.",
    );
  }
}

function commandWorks(command, args = ["--version"]) {
  return spawnSync(command, args, { stdio: "ignore", shell: false }).status === 0;
}

function pythonExecutable(venvDir) {
  return join(venvDir, isWindows ? "Scripts" : "bin", isWindows ? "python.exe" : "python");
}

function pythonCandidates() {
  const candidates = [];
  if (isWindows) {
    candidates.push({ command: "py", args: ["-3.12"] });
    candidates.push({ command: "py", args: ["-3"] });
  }
  candidates.push({ command: "python3.12", args: [] });
  candidates.push({ command: "python3", args: [] });
  candidates.push({ command: "python", args: [] });
  return candidates;
}

function findBootstrapPython() {
  const versionProbe = [
    "-c",
    "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)",
  ];
  for (const candidate of pythonCandidates()) {
    if (commandWorks(candidate.command, [...candidate.args, ...versionProbe])) {
      return candidate;
    }
  }
  throw new Error(
    "Python 3.10-3.13 was not found. Install Python 3.12, then run the preview task again.",
  );
}

function projectPython() {
  const venvDir = join(siteDir, ".venv");
  const python = { command: pythonExecutable(venvDir), args: [] };
  if (existsSync(python.command) && commandWorks(python.command, ["--version"])) return python;

  info(`Creating project virtual environment at ${venvDir}.`);
  if (commandWorks("uv")) {
    runSync("uv", ["venv", "--python", "3.12", venvDir]);
  } else {
    pythonRunSync(findBootstrapPython(), ["-m", "venv", venvDir]);
  }
  if (!existsSync(python.command) || !commandWorks(python.command, ["--version"])) {
    throw new Error(`Project virtual environment creation failed at ${venvDir}.`);
  }
  return python;
}

function runSync(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: repoDir,
    env: process.env,
    stdio: "inherit",
    shell: false,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with code ${result.status}.`);
}

function pythonRunSync(python, args, options = {}) {
  return runSync(python.command, [...python.args, ...args], options);
}

function pythonHasBackendDependencies(python) {
  const importResult = spawnSync(
    python.command,
    [...python.args, "-c", "import fastapi, uvicorn; from agents.exceptions import ModelRefusalError"],
    { cwd: siteDir, env: process.env, stdio: "ignore", shell: false },
  );
  const checkResult = spawnSync(
    python.command,
    [...python.args, "-m", "pip", "check"],
    { cwd: siteDir, env: process.env, stdio: "ignore", shell: false },
  );
  return importResult.status === 0 && checkResult.status === 0;
}

function fileDigest(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function pythonHasOpenAIKey(python) {
  const probe = [
    "import os",
    "from backend.src.bootstrap import load_dotenv",
    "from backend.src.paths import ENV_PATH",
    "load_dotenv(ENV_PATH)",
    "raise SystemExit(0 if os.getenv('OPENAI_API_KEY') else 1)",
  ].join("; ");
  const result = spawnSync(
    python.command,
    [...python.args, "-c", probe],
    { cwd: siteDir, env: process.env, stdio: "ignore", shell: false },
  );
  return result.status === 0;
}

function ensureDependencies() {
  const frontendLock = join(frontendDir, "package-lock.json");
  const frontendMarker = join(frontendDir, "node_modules", ".package-lock.sha256");
  const frontendDigest = fileDigest(frontendLock);
  const frontendCurrent = existsSync(frontendMarker)
    && readFileSync(frontendMarker, "utf8").trim() === frontendDigest;
  if (!frontendCurrent) {
    info("Installing locked frontend dependencies.");
    runSync(npmCommand, [...npmArgs, "-C", frontendDir, "ci", "--no-audit", "--no-fund"]);
    writeFileSync(frontendMarker, `${frontendDigest}\n`, { encoding: "utf8", mode: 0o600 });
  }

  const python = projectPython();
  const backendLock = join(siteDir, "backend", "requirements.txt");
  const backendMarker = join(siteDir, ".venv", ".requirements.sha256");
  const backendDigest = fileDigest(backendLock);
  const backendCurrent = existsSync(backendMarker)
    && readFileSync(backendMarker, "utf8").trim() === backendDigest
    && pythonHasBackendDependencies(python);
  if (!backendCurrent) {
    info("Installing locked backend dependencies into site/.venv.");
    pythonRunSync(
      python,
      ["-m", "pip", "install", "--require-hashes", "--requirement", backendLock],
    );
    if (!pythonHasBackendDependencies(python)) {
      throw new Error("The project virtual environment failed its dependency consistency check.");
    }
    writeFileSync(backendMarker, `${backendDigest}\n`, { encoding: "utf8", mode: 0o600 });
  }

  if (!pythonHasOpenAIKey(python)) {
    warn(
      "OPENAI_API_KEY is not configured in the process environment, site/.env.local, or site/.env. " +
      "The site can start, but workflows will fail until it is set.",
    );
  }
  return python;
}

function portIsFree(port) {
  return new Promise((resolvePort) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolvePort(false));
    server.listen({ host: backendHost, port }, () => server.close(() => resolvePort(true)));
  });
}

async function selectPort(preferred, label) {
  for (let port = preferred; port < preferred + 100 && port <= 65535; port += 1) {
    if (await portIsFree(port)) {
      if (port !== preferred) warn(`Using ${label} port ${port}; ${preferred} was unavailable.`);
      return port;
    }
    warn(`${label} port ${port} is in use; trying ${port + 1}.`);
  }
  throw new Error(`No available ${label} port found after ${preferred}.`);
}

function spawnChild(command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: repoDir,
    env: process.env,
    stdio: "inherit",
    shell: false,
    ...options,
  });
  children.add(child);
  child.once("exit", () => children.delete(child));
  return child;
}

function waitForExit(child) {
  return new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal || code === 0) resolveExit();
      else reject(new Error(`Preview process exited with code ${code}.`));
    });
  });
}

function stopChild(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (isWindows) {
    spawnSync("taskkill.exe", ["/pid", `${child.pid}`, "/t", "/f"], { stdio: "ignore", shell: false });
  } else {
    child.kill("SIGTERM");
  }
}

function stopChildren() {
  for (const child of children) stopChild(child);
}

async function waitForBackend(port) {
  const url = `http://${backendHost}:${port}/api/health`;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The backend is still starting.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 500));
  }
  throw new Error(`Backend did not become healthy at ${url}.`);
}

function previewOriginConfig(python, devPort, webPort, includeLanOrigins) {
  const args = [
    ...python.args,
    previewOriginsScript,
    "--site-dir",
    siteDir,
    "--local-domain",
    localDomain,
    "--dev-port",
    `${devPort}`,
    "--web-port",
    `${webPort}`,
  ];
  if (includeLanOrigins) args.push("--lan");
  const result = spawnSync(python.command, args, {
    cwd: repoDir,
    env: process.env,
    encoding: "utf8",
    shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      result.stderr.trim() || `Preview origin validation exited with code ${result.status}.`,
    );
  }
  let config;
  try {
    config = JSON.parse(result.stdout);
  } catch {
    throw new Error("Preview origin validation returned invalid JSON.");
  }
  if (
    typeof config?.allowedOriginsValue !== "string"
    || !Array.isArray(config?.lanOrigins)
    || config.lanOrigins.some((origin) => typeof origin !== "string")
    || typeof config?.accessToken !== "string"
    || typeof config?.accessTokenGenerated !== "boolean"
  ) {
    throw new Error("Preview origin validation returned an invalid configuration.");
  }
  return config;
}

async function runDevPreview() {
  const python = ensureDependencies();
  const backendPort = await selectPort(preferredBackendPort, "backend");
  const devPort = await selectPort(preferredDevPort, "dev preview");
  const webPort = preferredWebPort;
  const originConfig = previewOriginConfig(python, devPort, webPort, lanMode);
  const backendEnv = {
    ...process.env,
    ALLOWED_ORIGINS: originConfig.allowedOriginsValue,
  };
  if (lanMode) backendEnv.APP_ACCESS_TOKEN = originConfig.accessToken;

  info(`Starting backend at http://${localDomain}:${backendPort}.`);
  const backend = spawnChild(
    python.command,
    [...python.args, "-m", "uvicorn", "backend.src.server:app", "--host", backendHost, "--port", `${backendPort}`, "--reload"],
    { cwd: siteDir, env: backendEnv },
  );
  await waitForBackend(backendPort);

  if (lanMode) {
    warn("LAN mode enabled: Vite will listen on all interfaces. Use only on a trusted network.");
    for (const origin of originConfig.lanOrigins) {
      info(`LAN preview URL: ${origin}`);
    }
    if (originConfig.accessTokenGenerated) {
      warn("No APP_ACCESS_TOKEN was configured; generated a one-session LAN access token.");
      info(`One-time Access Gate token: ${originConfig.accessToken}`);
      info("Paste that token into the browser Access Gate. It expires when this preview stops.");
    } else {
      info("Access Gate: use the configured APP_ACCESS_TOKEN (its value is not displayed).");
    }
  }
  info(`Starting hot reload dev preview at http://${localDomain}:${devPort}.`);
  const frontendEnv = {
    ...process.env,
    VITE_BACKEND_URL: `http://${backendHost}:${backendPort}`,
  };
  delete frontendEnv.APP_ACCESS_TOKEN;
  const frontend = spawnChild(
    npmCommand,
    [...npmArgs, "-C", frontendDir, "run", "dev:ui", "--", "--host", frontendHost, "--port", `${devPort}`, "--strictPort", "--force"],
    { env: frontendEnv },
  );
  try {
    await waitForExit(frontend);
  } finally {
    stopChild(backend);
  }
}

async function runWebPreview() {
  const python = ensureDependencies();
  info(`Building frontend into ${join(siteDir, "dist")}.`);
  runSync(npmCommand, [...npmArgs, "-C", frontendDir, "run", "build"]);
  const webPort = await selectPort(preferredWebPort, "built web preview");
  const originConfig = previewOriginConfig(
    python,
    preferredDevPort,
    webPort,
    false,
  );
  const env = {
    ...process.env,
    HOST: backendHost,
    PORT: `${webPort}`,
    ALLOWED_ORIGINS: originConfig.allowedOriginsValue,
  };

  info(`Starting built web preview at http://${localDomain}:${webPort}.`);
  const backend = spawnChild(python.command, [...python.args, "-m", "backend.src.server"], { cwd: siteDir, env });
  await waitForExit(backend);
}

function printHelp() {
  console.log("Usage: node deploy/preview.mjs [--dev|--web] [--lan]");
  console.log(`  --dev  Run backend plus Vite hot reload at http://${localDomain}:${preferredDevPort}`);
  console.log(`  --web  Build frontend and serve it through FastAPI at http://${localDomain}:${preferredWebPort}`);
  console.log("  --lan  Bind the Vite dev server to all interfaces (dev only; trusted networks only)");
  console.log("         The launcher prints and authorizes the exact detected LAN URL and enforces an Access Gate token.");
  console.log("         Set PREVIEW_LAN_HOST to a reachable LAN hostname or IPv4 address if detection fails.");
  console.log("         Without --lan, preview servers listen on 127.0.0.1 only.");
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    stopChildren();
    process.exit(0);
  });
}
process.on("exit", stopChildren);

try {
  ({ mode, frontendHost, lanMode, showHelp } = parseArguments(process.argv.slice(2)));
  if (showHelp) printHelp();
  else {
    requireLoopbackBackendHost();
    if (mode === "--dev") await runDevPreview();
    else if (mode === "--web") await runWebPreview();
  }
} catch (error) {
  stopChildren();
  fail(error instanceof Error ? error.message : String(error));
}
