# Frontend

React + Vite UI for the UConn Isomorphic Question Generator.

The frontend provides:

- source file upload and prompt entry
- workflow progress and agent pipeline status
- logs for workflow activity
- result panels for generated content, LaTeX, PDF, and evaluation data
- modal views for agents, setup, files, and outputs

## Run

Install dependencies from the repository root:

```bash
npm -C frontend install
```

Start the Vite dev server:

```bash
npm -C frontend run dev:ui
```

Build production assets into `dist/`:

```bash
npm -C frontend run build
```

Optional checks:

```bash
npm -C frontend run lint
npm -C frontend run preview
```

## Backend Connection

The Vite dev server proxies API requests to `http://127.0.0.1:8000` by default.
Override that target with `VITE_BACKEND_URL` if the backend is running on a different host or port.

Proxy paths include:

- `/api`
- `/version.json`
- `/data`

## Build Notes

- `frontend/vite.config.js` emits `agents.json` and `data/agents.json` during the build so the UI can load shared agent metadata.
- The production build output is written to `dist/`, which the backend serves from `/` when present.
- The UI expects the backend API contract implemented in `backend/src/server.py`.
