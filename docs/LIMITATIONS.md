# Current Limitations

What the app does not yet protect against or support. Read this before sharing
access, deploying publicly, or handing the site to someone else.

- `APP_ACCESS_TOKEN` is one shared gate, not student accounts or role-based
  access control.
- The request limiter is in-process and resets on backend restart. It is not a
  per-student quota, dollar ledger, or semester budget.
- Temporary provider files have a crash-safe expiry, but a hard process or
  device failure can still leave remote evaluation definitions/runs for manual
  provider cleanup.
- User/Teacher/Dev are presentation modes, not authorization.
- The Dev panel is a workflow/developer surface, not a full host-admin
  dashboard. The proposed professor admin dashboard is not implemented:
  models, prompts, secrets, systemd, Cloudflare, backups, and budgets are not
  safely mutable from the UI.
- Uploaded document contents reach the tool-enabled Extracter before the
  post-extraction content guardrail. Restrict uploads to trusted course
  material; public anonymous uploads need a bounded local parsing/OCR
  quarantine stage that is not implemented yet.
- Hosted extraction/LaTeX containers install allowlisted runtime tooling on
  demand. Direct Python document packages are version-pinned, but the
  ephemeral provider environment does not have the Pi release's complete
  transitive hash lock.
- OpenAI Eval Batch Stop is cooperative and best-effort; an already-running
  remote evaluation call may finish after Stop is requested.
- Backups and restore are operator-run, deployment-locked procedures; there is
  no unattended backup schedule. A physical spare-SD restore drill is still
  required before handoff.
- Persistent Pi course data is permission-restricted but not encrypted at rest
  by this repository; physically secure the device/SD card or provision
  encrypted storage for sensitive or student-identifiable material.
- No software license has been selected in this repository; the owner must make
  that decision before external redistribution.
- Live workflow tests spend OpenAI funds and are not part of ordinary local
  automated tests.
