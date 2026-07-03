#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoDir = resolve(scriptDir, "..");
const siteDir = join(repoDir, "site");
const frontendDir = join(siteDir, "frontend");
const isWindows = process.platform === "win32";
const npmCommand = isWindows ? (process.env.ComSpec ?? "cmd.exe") : "npm";
const npmArgs = isWindows ? ["/d", "/s", "/c", "npm"] : [];
const mode = process.argv[2] ?? "--dev";
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

function numberFromEnv(name, fallback) {
  const value = Number.parseInt(process.env[name] ?? `${fallback}`, 10);
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be a port number from 1 to 65535.`);
  }
  return value;
}

function commandWorks(command, args = ["--version"]) {
  return spawnSync(command, args, { stdio: "ignore", shell: false }).status === 0;
}

function pythonExecutable(venvDir) {
  return join(venvDir, isWindows ? "Scripts" : "bin", isWindows ? "python.exe" : "python");
}

function pythonCandidates() {
  const candidates = [];
  if (process.env.VIRTUAL_ENV) {
    candidates.push({ command: pythonExecutable(process.env.VIRTUAL_ENV), args: [], managed: true });
  }

  candidates.push({ command: pythonExecutable(join(siteDir, ".venv")), args: [], managed: true });
  candidates.push({ command: pythonExecutable(join(repoDir, ".venv")), args: [], managed: true });

  const envsDir = join(homedir(), "envs");
  if (existsSync(envsDir)) {
    const sharedEnvironments = readdirSync(envsDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name.startsWith("main_py"))
      .map((entry) => entry.name)
      .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }));
    for (const environmentName of sharedEnvironments) {
      candidates.push({ command: pythonExecutable(join(envsDir, environmentName)), args: [], managed: true });
    }
  }

  if (isWindows) {
    candidates.push({ command: "py", args: ["-3.12"], managed: false });
    candidates.push({ command: "py", args: ["-3"], managed: false });
  }
  candidates.push({ command: "python3.12", args: [], managed: false });
  candidates.push({ command: "python3", args: [], managed: false });
  candidates.push({ command: "python", args: [], managed: false });
  return candidates;
}

function findPython() {
  for (const candidate of pythonCandidates()) {
    if ((candidate.command.includes("/") || candidate.command.includes("\\")) && !existsSync(candidate.command)) {
      continue;
    }
    if (commandWorks(candidate.command, [...candidate.args, "--version"])) {
      return candidate;
    }
  }
  throw new Error("Python 3 was not found. Install Python 3.12, then run the preview task again.");
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
  const result = spawnSync(
    python.command,
    [...python.args, "-c", "import fastapi, uvicorn; from agents.exceptions import ModelRefusalError"],
    { cwd: siteDir, env: process.env, stdio: "ignore", shell: false },
  );
  return result.status === 0;
}

function ensureDependencies() {
  if (!existsSync(join(frontendDir, "node_modules"))) {
    const installCommand = existsSync(join(frontendDir, "package-lock.json")) ? "ci" : "install";
    info("Installing frontend dependencies.");
    runSync(npmCommand, [...npmArgs, "-C", frontendDir, installCommand, "--no-audit", "--no-fund"]);
  }

  let python = findPython();
  if (!pythonHasBackendDependencies(python)) {
    if (!python.managed) {
      const venvDir = join(siteDir, ".venv");
      info(`Creating project virtual environment at ${venvDir}.`);
      pythonRunSync(python, ["-m", "venv", venvDir]);
      python = { command: pythonExecutable(venvDir), args: [], managed: true };
    }
    info("Installing backend dependencies into the selected virtual environment.");
    pythonRunSync(python, ["-m", "pip", "install", "-r", join(siteDir, "backend", "requirements.txt")]);
  }

  const envPath = join(siteDir, ".env");
  if (!existsSync(envPath) || !/^OPENAI_API_KEY=.+$/m.test(readFileSync(envPath, "utf8"))) {
    warn("site/.env is missing OPENAI_API_KEY. The site can start, but workflows will fail until it is set.");
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

function allowedOrigins(devPort, webPort) {
  const previewOrigins = [`http://${localDomain}:${devPort}`, `http://${localDomain}:${webPort}`];
  return [process.env.ALLOWED_ORIGINS, ...previewOrigins].filter(Boolean).join(",");
}

async function runDevPreview() {
  const python = ensureDependencies();
  const backendPort = await selectPort(preferredBackendPort, "backend");
  const devPort = await selectPort(preferredDevPort, "dev preview");
  const webPort = preferredWebPort;
  const env = { ...process.env, ALLOWED_ORIGINS: allowedOrigins(devPort, webPort) };

  info(`Starting backend at http://${localDomain}:${backendPort}.`);
  const backend = spawnChild(
    python.command,
    [...python.args, "-m", "uvicorn", "backend.src.server:app", "--host", backendHost, "--port", `${backendPort}`, "--reload"],
    { cwd: siteDir, env },
  );
  await waitForBackend(backendPort);

  info(`Starting hot reload dev preview at http://${localDomain}:${devPort}.`);
  const frontend = spawnChild(
    npmCommand,
    [...npmArgs, "-C", frontendDir, "run", "dev:ui", "--", "--host", "0.0.0.0", "--port", `${devPort}`, "--strictPort", "--force"],
    { env: { ...env, VITE_BACKEND_URL: `http://${backendHost}:${backendPort}` } },
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
  const env = {
    ...process.env,
    HOST: backendHost,
    PORT: `${webPort}`,
    ALLOWED_ORIGINS: allowedOrigins(preferredDevPort, webPort),
  };

  info(`Starting built web preview at http://${localDomain}:${webPort}.`);
  const backend = spawnChild(python.command, [...python.args, "-m", "backend.src.server"], { cwd: siteDir, env });
  await waitForExit(backend);
}

function printHelp() {
  console.log("Usage: node deploy/preview.mjs [--dev|--web]");
  console.log(`  --dev  Run backend plus Vite hot reload at http://${localDomain}:${preferredDevPort}`);
  console.log(`  --web  Build frontend and serve it through FastAPI at http://${localDomain}:${preferredWebPort}`);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    stopChildren();
    process.exit(0);
  });
}
process.on("exit", stopChildren);

try {
  if (mode === "--dev" || mode === "dev") await runDevPreview();
  else if (mode === "--web" || mode === "web") await runWebPreview();
  else if (mode === "--help" || mode === "-h") printHelp();
  else throw new Error(`Unknown preview mode: ${mode}`);
} catch (error) {
  stopChildren();
  fail(error instanceof Error ? error.message : String(error));
}
