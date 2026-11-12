# UConn Isomorphic Question Generator

> **No longer maintained:** This repository is no longer updated or
> maintained. It has been handed off to Xian Wu at the University of
> Connecticut, who is continuing the project and research. See
> [deploy/HANDOFF.md](deploy/HANDOFF.md) for the handoff details.

Generate original, isomorphic physics multiple-choice questions from source
PDFs, Word documents, images, or text files. A multi-agent workflow built on the
OpenAI Agents SDK (FastAPI backend, React frontend) extracts and verifies the
source questions, writes new questions that keep the same reasoning with
different surface details, and typesets them as LaTeX and PDF. Guardrails
screen every request and uploaded document, and paid rubric evaluation is
optional and off by default.

> **API key disclaimer:** Earlier commits in this repository intentionally
> included OpenAI API keys while the repository was private. Those API keys
> have since been disabled and no longer work, and `site/.env`, the file that
> held them, is no longer tracked. To run the app, use your own key as
> described in [Setting An API Key](#setting-an-api-key).

## Current Version

**1.0.0** (from [site/pyproject.toml](site/pyproject.toml)). A deployed site
also reports the exact commit it is running at `/version.json`.

## Requirements

- [Git](https://git-scm.com/)
- [Python](https://www.python.org/downloads/) 3.12 (3.10-3.13 also work)
- [Node.js](https://nodejs.org/) 22 LTS with npm ([.nvmrc](.nvmrc) pins the
  exact version; Node 20.19+ and 24+ also work)
- An OpenAI API key with access to the Agents SDK and hosted containers.
  Container network access also needs an allowlist set by your OpenAI
  organization admin; see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#prerequisites).

You do not need a local LaTeX install; PDFs are compiled in an OpenAI container.

## Setup

Clone the repository:

```bash
git clone https://github.com/lindimismaili/UConn-Isomorphic-Question-LLM.git
cd UConn-Isomorphic-Question-LLM
```

The preview launcher in [Running The Local Preview](#running-the-local-preview)
creates the Python environment and installs all locked dependencies on its
first run, so you can skip to [Setting An API Key](#setting-an-api-key). To
install everything manually instead, including the test and lint tools:

macOS / Linux:

```bash
cd site
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
npm -C frontend ci
```

Windows (PowerShell):

```powershell
cd site
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r backend\requirements-dev.txt
npm -C frontend ci
```

Packages go into the project's own `site/.venv` and `site/frontend/node_modules`;
never install them into a global or system Python.

## Setting An API Key

Two environment files matter, both in `site/`:

| File | In git? | What it's for |
| --- | --- | --- |
| `site/.env.example` | Yes | Template listing every setting with blank values. Never put real values here; the app doesn't read it. |
| `site/.env.local` | No (ignored) | Your private settings, including your API key. It stays on your computer. |

Create your local file by copying the template. From the repository root:

macOS / Linux (`chmod` limits the file to your user):

```bash
cp site/.env.example site/.env.local
chmod 600 site/.env.local
```

Windows (PowerShell):

```powershell
Copy-Item site\.env.example site\.env.local
```

Then open `site/.env.local` and fill in the blank key line:

```env
OPENAI_API_KEY=sk-your-key-here
```

A key set in your shell or service environment overrides `site/.env.local`.
The site starts without a key, but question generation fails until one is set.
Every other setting is optional for local use; see
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Running The Local Preview

From the repository root:

```bash
node deploy/preview.mjs --dev
```

This starts the backend and the hot-reloading frontend. Open the URL it prints
(normally <http://localhost:5173>); adding `/api/health` to that URL checks that
the backend is up.

To preview the production build instead, served by the backend at
<http://localhost:8000>:

```bash
node deploy/preview.mjs --web
```

In VS Code you can also run either one from **Terminal → Run Task** as
**Run Dev Mode** or **Run Web Preview**.

The preview is reachable only from your own computer. Before exposing the site
anywhere else, complete the access-token, rate-limit, and backup steps in
[deploy/HANDOFF.md](deploy/HANDOFF.md).

## Documentation

| Document | Read it when you need to… |
| --- | --- |
| [deploy/HANDOFF.md](deploy/HANDOFF.md) | Take over the project: handoff status, open items, setup, access and cost controls, and the release checklist |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Understand how the pipeline, guardrails, and containers fit together, or find where code lives |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Change an environment variable, limit, timeout, cost cap, or the donation recipient |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Do a full development install, run the backend and frontend separately, run tests and lint, or version prompt changes |
| [docs/CLI.md](docs/CLI.md) | Generate questions or compare grades from the command line instead of the web UI |
| [docs/OUTPUTS_AND_EVALUATION.md](docs/OUTPUTS_AND_EVALUATION.md) | Find a run's output files, interpret evaluation results, or estimate API cost |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deploy to the Raspberry Pi or use the `/admin` operations dashboard |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Fix a common setup or runtime problem |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Know what the app does not protect against before sharing or deploying it |
| [AGENTS.md](AGENTS.md) | Work on the repository with a coding agent; conventions and source-of-truth paths ([CLAUDE.md](CLAUDE.md) points here) |
| [deploy/DEPLOY.md](deploy/DEPLOY.md) | Run the deployment and preview scripts |
| [deploy/BACKUP_RESTORE.md](deploy/BACKUP_RESTORE.md) | Back up or restore the Raspberry Pi |
| [site/SECURITY.md](site/SECURITY.md) | Review the security boundary, secret handling, and privacy, or report a vulnerability |
| [site/backend/BACKEND.md](site/backend/BACKEND.md) | Look up a backend file, function, API route, or test |
| [site/frontend/DESIGN.md](site/frontend/DESIGN.md) | Make any UI change (read it first) |
| [site/backend/data/calibration/README.md](site/backend/data/calibration/README.md) | Maintain evaluation calibration data |
| [archive/ARCHIVE.md](archive/ARCHIVE.md) | Find historical GPT Builder versions, question sources, and reports |

## Authors

Created by Lindim Ismaili and Xian Wu.
