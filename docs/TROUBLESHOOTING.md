# Troubleshooting

Fixes for common setup and runtime problems.

- `OPENAI_API_KEY` missing: the backend API can start, but workflow execution
  will fail until the key is set. See
  [Setting An API Key](../README.md#setting-an-api-key).
- Workflows fail with an authentication error, or say the key is missing, even
  though you set one: make sure your key is in `site/.env.local` (or your
  shell/service environment), not in `site/.env.example`. The app never reads
  the example file. Keys committed in earlier versions of `site/.env` are
  disabled.
- `site/dist/index.html` missing: run `npm -C site/frontend run build`, then restart the backend.
- `crypto.hash is not a function` or Vite rejects the Node version: use Node
  20.19+ within Node 20.x, Node 22.13+, or Node 24+. On Raspberry Pi, run
  `bash deploy/deploy.sh`; the deployer provisions Node 22 when needed.
- `Python 3.10-3.13 was not found` from the preview launcher: install Python
  3.12, then run the launcher again.
- Upload rejected: check the file extension and `MAX_UPLOAD_BYTES`.
- Workflow appears stuck: check `/api/health`, `/api/openai/status`, and the latest folder in `site/backend/outputs/`.
- Frontend cannot reach the backend in dev mode: run
  `node deploy/preview.mjs --dev` and open the URL it prints (normally
  `http://localhost:5173`).
