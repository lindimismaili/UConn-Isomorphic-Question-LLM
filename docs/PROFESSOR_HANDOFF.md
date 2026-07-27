# Professor Handoff Guide

This guide is the operational starting point for running the UConn Isomorphic
Question Generator without a development-agent environment. Complete the
release checklist before giving students or public users access.

## What the application does

The application accepts physics-question source files and runs a ten-node
workflow:

`setup → input_guardrail → normalizer → extracter → content_guardrail → verifier → generator → latexifier → evaluation → output`

Ordinary generation verifies the extracted source questions before creating
new variants. Paid rubric evaluation of generated output is a separate,
optional step; it is not a guarantee that every generated question is correct.

## Handoff safety decisions

- Revoke or rotate every credential that was ever stored in the formerly
  tracked `site/.env` before sharing this repository history. The current tree
  ignores that file, but earlier commits remain part of Git history unless the
  owner performs a separate reviewed history-cleanup operation.
- Keep `OPENAI_API_KEY` on the server. Never put it in frontend source, a
  `VITE_*` variable, screenshots, logs, or chat.
- Set `APP_ACCESS_TOKEN` before allowing access beyond localhost. It is a
  shared deployment gate, not a multi-user login system.
- Start Raspberry Pi deployment on the local network. Do not enable a public
  Cloudflare route until authentication and rate-limit checks pass.
- Leave evaluation off unless its eight-grader cost is intentional.
- Back up persistent data before upgrades. The project has code rollback, not
  automatic data backup or data-schema rollback.
- Treat uploaded assessments and generated outputs as course data. Decide who
  may access them and how long they should be retained.

## Clean local setup

Use Python 3.12 and Node 22.13+ LTS. Keep Python packages in the project-local
`site/.venv`.

```bash
(
set -euo pipefail
read -r -p 'Reviewed 40-hex release commit: ' RELEASE_COMMIT
printf '%s\n' "${RELEASE_COMMIT}" | grep -Eq '^[0-9a-f]{40}$'
git clone https://github.com/lindimismaili/UConn-IsomorphicQuestionLLM.git \
  UConn-IsomorphicQuestionLLM
cd UConn-IsomorphicQuestionLLM
GIT_TERMINAL_PROMPT=0 GIT_NO_REPLACE_OBJECTS=1 \
  git -c core.hooksPath=/dev/null -c protocol.file.allow=never \
  fetch origin "${RELEASE_COMMIT}"
GIT_NO_REPLACE_OBJECTS=1 \
  git -c core.hooksPath=/dev/null checkout --detach "${RELEASE_COMMIT}"
test "$(GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD)" = "${RELEASE_COMMIT}"
cd site
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
npm -C frontend ci
)
```

`npm ci` installs exactly the lockfile state and is the reproducible handoff
command. The development Python lock includes production packages plus the
test/lint/audit tools required by the release checklist. The Pi deployment
installs the smaller production lock. Do not substitute a global Python
installation for `site/.venv`. The repository owner supplies
`RELEASE_COMMIT` in the signed-off release record after the handoff suite
passes; never substitute an unreviewed branch tip or short SHA.

## Configure credentials and optional settings

For local development, put private overrides in `site/.env.local`. The backend
loads `.env.local` before an optional ignored `.env`; existing process or
service environment variables have highest precedence. No arbitrary
`.env.<mode>.local` files are loaded.

Create the file directly on the machine with owner-only permissions and do not
paste real values into chat. Run this from the repository root:

```bash
touch site/.env.local
chmod 600 site/.env.local
```

```dotenv
OPENAI_API_KEY=<professor-owned-key>
APP_ACCESS_TOKEN=<long-random-shared-access-token>

# Optional hosted donation link; no PayPal API secret is needed.
PAYPAL_DONATE_URL=https://www.paypal.com/donate/?hosted_button_id=<id>

# Optional request throttle for cost-bearing POST routes.
APP_RATE_LIMIT_REQUESTS=12
APP_RATE_LIMIT_WINDOW_SECONDS=60
```

The optional donation URL is exposed only when it is HTTPS and belongs to
`paypal.com` or `paypal.me`. Donations do not purchase or increase application
quota.

### Provision the professor's OpenAI project and key

Create a dedicated API Platform project for this application instead of using
an organization-wide/default key. OpenAI documents
[project creation, model/rate/spend limits, and alerts](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform)
and [Restricted API-key permissions](https://help.openai.com/en/articles/8867743-assign-api-key-permissions).

For a user-owned project key, choose **Restricted** and grant only the
Responses, Files, Containers/container-files, and Moderations access needed by
this application; grant Evals access only while the optional evaluation
features are in use. Permission labels can vary as the platform changes, so
verify the current key dialog and test the narrowest workable permissions
rather than selecting **All** by habit. Service-account key dialogs may not
offer the same per-endpoint controls.

In the project's Limits page, restrict usable models, set per-model rate
limits, set spend alerts, and configure the available spend-limit enforcement
appropriate for the course. Confirm the current platform behavior: application
rate limits are not a dollar ledger, and an alert-only threshold does not stop
requests. Start with a tiny, explicitly authorized run and inspect project
usage before opening access to a class.

### Allow only the required hosted-container domains

OpenAI hosted containers have no outbound network access by default. The
[official shell-network guide](https://developers.openai.com/api/docs/guides/tools-shell#network-access)
requires both an organization administrator's allowlist and the request-level
`network_policy`. Ask the professor's organization administrator to allow
exactly the six domains currently declared by `setup.allowedDomains` in
`site/backend/data/agents.json`:

- `archive.ubuntu.com`
- `security.ubuntu.com`
- `deb.debian.org`
- `security.debian.org`
- `pypi.org`
- `files.pythonhosted.org`

The application's request policy further restricts each container to that
list; it cannot grant a domain absent from the organization list. If the
registry or `OPENAI_CONTAINER_ALLOWED_DOMAINS` changes, review both layers
together and do not add broad package mirrors or general-purpose destinations.
The exact dashboard labels may change. After configuring the key and allowlist,
run one tiny, explicitly authorized end-to-end generation and verify document
bootstrap and LaTeX compilation before granting class access.

### Configure the optional PayPal link

Create a hosted donation link through PayPal's
[Donate Button page](https://www.paypal.com/donate/buttons), then put only that
HTTPS hosted URL in `PAYPAL_DONATE_URL`. Do not add PayPal REST credentials,
client secrets, webhooks, or payment handling to this application. After
restart, open the site in a private/incognito browser window and verify that
the footer link opens the expected PayPal-owned hostname and intended
recipient; do not complete a real transaction merely as a setup check.

### Access-token behavior

When `APP_ACCESS_TOKEN` is configured, it must be 32-512 characters using
letters, numbers, and `. _ ~ + / = -`. Protected API requests must include:

```http
X-App-Access-Token: <the configured APP_ACCESS_TOKEN>
```

The browser gate collects the shared token, keeps it in that tab's
`sessionStorage`, and sends the header only to same-origin `/api` requests.
Closing the tab clears that browser session. The gate must never collect or
receive the OpenAI API key. These GET routes remain public for health/bootstrap
behavior:

- `/api/health`
- `/api/openai/status`
- `/api/public-config`
- `/version.json`

An authenticated `/api/readiness` response reports whether the request passed
the access boundary and whether an OpenAI key is configured, without returning
either secret.

The current limiter is in-process and IP-scoped for cost-bearing POST routes,
including local compatibility mode when no access token is set. It resets when
the backend restarts and is not a semester budget or per-student quota system.

Paid start routes also require a browser-generated `X-Idempotency-Key`. If a
response is lost, the browser client does not retry automatically; clicking Start
again with unchanged inputs reuses the tab-local pending key and the server
returns the original workflow or batch instead of spending twice. Changed
inputs create a new key. The server retains only the key, request hash, and
start response in its private SQLite state for at least seven days and while
the original workflow/batch record remains retained; it does not store a
second copy of the prompt or upload body in that mapping.

## Run locally

The cross-platform preview binds to localhost by default:

```bash
cd UConn-IsomorphicQuestionLLM
node deploy/preview.mjs --dev
```

Use `--lan` only on a trusted network. The launcher detects the computer's
reachable IPv4 address, prints the exact LAN URL, and adds only that origin
(plus the normal loopback origins) to the backend's temporary allowlist. LAN
mode always enables the Access Gate: it preserves an existing
`APP_ACCESS_TOKEN` without displaying it, or generates a cryptographically
strong one-session token, prints that generated token once, and tells you to
paste it into the browser Access Gate. The generated value is passed only to
the backend child process and expires when the preview stops.

If detection cannot find the intended interface, set a validated one-command
override instead of using a wildcard:

```bash
PREVIEW_LAN_HOST=192.168.1.25 node deploy/preview.mjs --dev --lan
```

The launcher fails closed if it cannot determine a non-loopback address. Do not
copy an example address literally; use the address or resolvable LAN hostname
assigned to that computer. If a persistent token is configured, enter that
known value at the Access Gate; the launcher intentionally does not echo it.

The supported non-loopback development path is `preview.mjs --dev --lan` (or
`preview.sh --dev --lan`). If you manually bind Uvicorn or Vite to `0.0.0.0` or
another non-loopback interface, configure a non-empty `APP_ACCESS_TOKEN` and an
exact `ALLOWED_ORIGINS` entry first; raw Uvicorn does not generate these
protections for you. To test the production build locally:

```bash
node deploy/preview.mjs --web
```

Check the exact URL and port printed by the launcher. If it reports
`http://127.0.0.1:<printed-port>`, verify:

```bash
curl -fsS "http://127.0.0.1:<printed-port>/api/health"
```

The actual port may change when the preferred port is occupied.

## Understand paid evaluation

Web generation and the module CLI leave evaluation disabled by default. The
CLI requires the explicit `--eval` flag:

```bash
cd site
.venv/bin/python -m backend.src.main run <source-file> --eval
```

The rubric has eight criteria implemented as eight score-model graders. If an
evaluation dataset contains `N` source/generated pairs, it creates
approximately `8 × N` grader calls, in addition to extraction, verification,
generation, LaTeX, container, and other OpenAI usage. Test with a very small
dataset first and monitor OpenAI project usage.

Upload-mode Eval Batch isolates every supplied PDF because those files are
untrusted. If the batch contains `F` PDFs (one source plus the generated
files), it projects `F` document-dependency bootstrap calls and up to `2 × F`
Extracter calls when every PDF needs its single retry, in addition to the
`8 × N` graders. The default maximum is six PDFs. Review and approve all three
projections shown in the modal. Moderation/content classifiers also run per
extracted PDF and may require additional batches; the grader count is not the
total provider cost.

The ten boxes in the pipeline display are an orchestration taxonomy, not a count of
billable calls. With evaluation disabled, a full run still uses five core
agent calls, three classifier calls across the two guardrail nodes, and two
model-backed container shell tasks (bootstrap and compile)—at least ten
model-backed calls when each guardrail fits in one batch. Moderation, tool
turns, retries, extra classifier batches, container time, and file storage are
additional usage.

Each complete input or content classifier phase is bounded to 16 calls and 300
seconds by default. The settings
`OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS` and
`OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS` can lower those ceilings; exhaustion
blocks the run before another classifier call. The workflow also validates its
required prompts, container templates/placeholders, and LaTeX template locally
before making its first paid provider request.

Every paid HTTP/CLI instruction has a fixed 20,000-character ceiling and is
rejected before provider work, idempotency hashing, or persistence. Registry
output ceilings further bound model generation: setup shell calls are capped at
4096 tokens per response and guardrail classifiers at 2048. Current normal-stage
per-response caps are Normalizer 2048, Extracter/Verifier 32768 each, and
Generator/LaTeXifier 65536 each. Tool turns and retries can create additional
responses, so these are not a whole-run token budget. Each of the eight Evals
score-model calls uses `GRADER_MAX_COMPLETION_TOKENS=4096` by default.
Provider-generated PDF/TeX downloads stream to an owner-only temporary file and
are rejected above `MAX_GENERATED_ARTIFACT_BYTES` (50 MiB by default).

On an Extracter cache miss, that work spans two sequential provider-container
sessions. The first is extraction-only; after the content guardrail accepts,
the app deletes that container and its uploaded provider files before creating
a clean Verifier/Generator/LaTeX container with no source-file attachments.
This is a security isolation cost, not another agent call. An Extracter cache
hit skips the source upload, document bootstrap, and extraction container.

The repository does not yet enforce a dollar-denominated course or semester
budget. The request limiter reduces bursts; it is not cost accounting.
The default process policy executes one full agentic workflow at a time and
rejects a new top-level start when four process-local workflow records are
already non-terminal. These are pressure controls, not financial quotas. Legacy
token-delta streaming is off by default because the current UI polls status.

The Eval Batch Stop action is cooperative and best-effort. It prevents further
scheduled work where possible, but an already-running remote OpenAI evaluation
may finish after Stop is requested and may still incur cost.

Create Data is also a paid batch operation: it can start up to
`MAX_CREATE_DATA_ENTRIES` complete generation workflows over the bundled
fixtures. Its actual cost projection is the current fixture count; a request
is rejected if that count exceeds the configured cap. The UI and API require
explicit approval for that fan-out. It does not run the eight-grader rubric
evaluation; use Batch Evaluation separately before running professor
comparison.

On-demand browser ZIP downloads accept at most 128 MiB of uncompressed source
artifacts by default and can never be configured above 512 MiB. The browser
buffers the resulting ZIP blob, so raise `DOWNLOAD_ZIP_MAX_SOURCE_BYTES` only
for a deliberate, well-resourced client.

## Raspberry Pi setup

Use 64-bit Raspberry Pi OS on a supported Pi. The installer requires an
`aarch64` kernel with `arm64` packages, Python 3.10+, at least 1,700 MiB RAM
(a 4 GB-class Pi is recommended), and at least 8 GiB free on both the release
and persistent-data filesystems. Clone the repository, then run the read-only
preflight before installation:

```bash
bash deploy/deploy.sh --preflight
bash deploy/deploy.sh
```

The installer provisions the application as immutable releases and keeps
host-specific secrets outside those releases in:

```text
/etc/uconn-isomorphic/environment
```

The directory is root-owned mode `0755` so the normal professor account can
read the non-secret `0644` deploy config; the environment file is root-owned
mode `0600`. systemd loads optional release-local non-secret defaults first and
the canonical persistent device environment second, so device values override
release defaults. On first installation the script prompts twice, without echoing, for
a missing OpenAI key and generates a strong application access token. The
access token is shown once on an interactive terminal.

Use the supported secret-management commands instead of committing credentials:

```bash
bash deploy/deploy.sh --configure-secrets
bash deploy/deploy.sh --rotate-openai-key
bash deploy/deploy.sh --rotate-access-token
bash deploy/deploy.sh --configure-donation
bash deploy/deploy.sh --configure-runtime-policy
bash deploy/deploy.sh --status
```

`--configure-secrets` fills missing settings, rotations restart the service,
`--configure-donation` validates and atomically applies or clears a hosted
PayPal URL, `--configure-runtime-policy` safely configures bounded
rate/retention/concurrency values, and `--status` reports booleans/metadata
without printing secrets. All setting changes take the deployment lock, require
zero active workflows before restart, and health-check the result.

The installed service also has a root-owned
`/run/uconn-isomorphic-work-lock/activity.lock`. Every admitted paid web or CLI
job holds a shared lease from acceptance through provider and artifact cleanup.
The installer preserves a safe live inode and installs a systemd-tmpfiles rule
that recreates the root-owned `0755` directory and single-link `0444` file after
boot. Deploy, backup, and restore publish maintenance, take the conflicting
exclusive lease, and retain it through restart, health verification, and
maintenance removal. Do not delete, replace, link, chmod, or bypass this runtime
lock; a deployed backend fails closed if its path or metadata is missing or
unsafe.

Keep the first deployment local. Verify the application, access boundary,
rate-limit response, result download authorization, and backup before choosing
any Cloudflare/public-hosting option. Public hosting additionally requires
Cloudflare **Always Use HTTPS** plus an Access allow policy for the exact
hostname with no path bypass; verify from a separate network that HTTP
redirects to that same HTTPS hostname and anonymous UI/API requests stop at
Access. Public mode keeps the Pi's nginx origin loopback-only and requires no
inbound router port-forward.

Do not call the Raspberry Pi handoff ready merely because the installer passes
syntax checks. A scratch Pi or equivalent 64-bit test device must pass
preflight, clean install, restart, health check, failed-release rollback,
secret rotation, and backup/restore rehearsal for the exact release commit.

## Back up and roll back

The Pi release mechanism retains prior code releases and can switch the
`current` symlink back when a candidate fails health checks. It persistently
links these data paths across code releases:

- `/var/lib/uconn-isomorphic/uploads/`
- `/var/lib/uconn-isomorphic/outputs/`
- `/var/lib/uconn-isomorphic/runtime/`
- `/etc/uconn-isomorphic/environment`

Persistent across releases does not mean retained indefinitely. By default, an
hourly cleanup removes output runs older than 7 days and then enforces at most
500 runs / 20 GiB; upload sessions older than 24 hours are removed and total
upload storage is bounded to 2 GiB. The upload bound is also enforced live
across concurrent requests and counts persisted, unused sessions between
cleanup passes; the API returns HTTP 507 before writing beyond it. Review
`OUTPUT_RETENTION_SECONDS`, `OUTPUT_MAX_RUNS`, `OUTPUT_MAX_BYTES`,
`UPLOAD_RETENTION_SECONDS`, `UPLOAD_MAX_BYTES`, and
`ARTIFACT_CLEANUP_INTERVAL_SECONDS` before course use. Back up required course
records before those limits can remove them.

Before an update or schema-changing release, run the installed
`uconn-isomorphic-backup-restore backup` command from
[../deploy/BACKUP_RESTORE.md](../deploy/BACKUP_RESTORE.md). It takes the shared
deployment lock, enters maintenance, requires an exact-zero active-work count,
stops remaining writers, records the exact release commit, validates the
archive, and restarts/health-checks the prior service before reopening it. The
destination must be a separately mounted Linux filesystem with a LUKS
dm-crypt ancestor that the helper can verify from sysfs; a plaintext USB
filesystem is refused. The archive also embeds a verified Git bundle for the
exact commit and its reachable history; capacity planning and offline custody
must account for both course/runtime data and repository history.

Provisioning LUKS is a destructive storage-administration task and is
intentionally outside this repository's automation. Use a blank, correctly
identified removable device; inventory and back up anything on it first; and
follow the current Raspberry Pi OS or institutional IT procedure. Do not copy
generic `cryptsetup` commands onto a device that contains data, and do not
weaken the helper merely to accept a plaintext destination. If the professor
cannot safely provision and rehearse encrypted removable storage, record the
backup gate as failed and keep public exposure disabled.

The archive contains credentials: close the LUKS device after backup, keep it
offline, and never copy the files to plaintext storage. Restore likewise
requires the archive and checksum to remain together in a root-owned directory
on verifiably LUKS-backed removable storage; it refuses a plaintext staging
copy. There is no automatic scheduled backup. Restore/recovery are
operator-triggered but phase-journaled and resumable across power loss;
completion rotates the browser token and requires inherited deployment/work
locks plus exact-commit health before exposure. Retained pre-restore roots are
removed only by the explicit, transaction-ID-bound `finalize` command after the
physical acceptance drill; do not delete them by hand.
Automatic code rollback does not restore SQLite contents, delete newly written
uploads/outputs, or reverse a destructive data migration. A migration needs its
own tested restore procedure and spare-SD rehearsal.

The encrypted removable backup does not encrypt the Pi's live SD card.
Persistent course data on the device is protected by Unix ownership/modes, not
at-rest encryption provisioned by this repository. Physically secure the Pi/SD
card or use an institution-approved encrypted root/data-volume design when the
material is sensitive or student-identifiable.

## Current dashboard and operational limitations

The application has User, Teacher, and Dev views. Those are presentation modes,
not user accounts or authorization roles. The current security boundary is one
shared deployment token.

The professor can use the existing Developer Tools for batch/evaluation
workflows, but the Dev panel is a workflow/developer surface, not a full
host-admin dashboard. The proposed admin dashboard is not implemented. In
particular, do not expect the UI to:

- edit models, prompts, environment variables, or systemd settings;
- manage individual student identities or quotas;
- show authoritative token or dollar usage;
- schedule backups (the tested backup/restore command remains host-operated);
- restart host services or control Cloudflare;
- provide an audit log of administrative mutations.

Make model/reasoning changes in `site/backend/data/agents.json`; change prompts
through the prompt-versioning workflow; change server secrets on the host.
Replay/from-agent-input tools still run the input and resolved-content
guardrails; they are not a way to bypass safety checks.

Keep document upload access limited to trusted course participants and
materials. The current workflow screens the filename and user instruction
before setup, and screens every uploaded structured agent-input string. A
tool-enabled Extracter still reads ordinary document bytes before their content
can be screened. After extraction, every string field in the complete
structured result is moderated and prompt-injection-classified; the
academic/physics/is-a-question classifier intentionally receives only the
question records. Public anonymous uploads require a bounded local parsing/OCR
quarantine stage that this release does not provide.

The Pi application's Python and npm dependencies are locked, but the hosted
extraction/LaTeX containers install allowlisted tooling at run time. Direct
Python document-package versions are pinned; their full transitive provider
environment and distro packages are not hash-locked. Treat this as an external
service/supply-chain dependency when generated artifacts need independent
verification.

## Evals retirement deadline

The OpenAI Evals API used by `site/backend/src/evaluation.py` becomes read-only
on **October 31, 2026** and shuts down on **November 30, 2026**. The professor
should not plan a semester that depends on the current evaluation path beyond
those dates. Assign and validate a replacement evaluation implementation before
the read-only deadline. Generation can remain useful with evaluation disabled,
but evaluation-specific batch tools and reports depend on that migration. Track
the dates and migration guidance on OpenAI's
[official deprecations page](https://developers.openai.com/api/docs/deprecations).

## Final release checklist

From the repository root, invoke the `handoff-readiness` skill or perform its
equivalent checks:

- clean dependency install with `site/.venv` and `npm ci`;
- dedicated professor-owned OpenAI project, least-privilege key, model/rate
  limits, spend enforcement/alerts, and key rotation recorded;
- organization hosted-container allowlist restricted to the six
  registry-requested domains, followed by a tiny authorized bootstrap/compile
  validation;
- backend tests and Ruff;
- frontend tests, ESLint, and production build;
- deploy/preview syntax checks;
- localhost preview and production-build health;
- access-token rejection/acceptance without exposing secrets;
- optional PayPal link verified in a private/incognito browser without making a
  real donation;
- small, explicitly authorized paid workflow only if live API verification is
  required;
- scratch Pi/VM installation before the professor's device;
- verified executable backup, spare-SD restore/recovery, and code rollback;
- [ ] Data-distribution review signed off by the owner/professor: they have
  permission to distribute every tracked evaluation PDF, rubric/calibration
  artifact, and professor-comparison workbook, and have confirmed that none
  contains student-identifiable data. This technical review intentionally did
  not inspect or delete the curated source contents;
- [ ] Repository owner licensing decision recorded; this repository currently has
  no `LICENSE`, so external redistribution is not approved merely by technical
  readiness;
- recorded release commit and remaining limitations.

See [../SECURITY.md](../SECURITY.md) for security boundaries and
[../deploy/DEPLOY.md](../deploy/DEPLOY.md) for installer operations. Use the
separate [../deploy/BACKUP_RESTORE.md](../deploy/BACKUP_RESTORE.md) runbook for
backup creation, archive verification, spare-device restore, and interrupted
restore recovery.
