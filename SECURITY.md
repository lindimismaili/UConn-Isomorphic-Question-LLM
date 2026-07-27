# Security Policy and Deployment Boundaries

This is an academic application that can spend OpenAI funds and store uploaded
course material. Treat security controls as required deployment configuration,
not optional UI polish.

## Report a vulnerability

Do not include API keys, access tokens, student data, uploaded assessments, or
private output artifacts in a public issue. Contact the repository owner or
course maintainer through an agreed private channel. Include affected commit,
route or component, reproduction steps with redacted values, and likely impact.

No public bounty, response-time SLA, or long-term-support policy is currently
offered.

## Secret handling

- `OPENAI_API_KEY` is server-only. Never expose it to the browser or store it in
  a `VITE_*` variable.
- `APP_ACCESS_TOKEN` is also a server secret. Browsers send it in
  `X-App-Access-Token`; it is not the OpenAI key. The current UI keeps it only
  in the browser tab's `sessionStorage` and attaches it only to same-origin
  `/api` requests.
- Local private overrides belong in `site/.env.local`. The backend loads that
  file before an optional ignored `site/.env`, while existing process/service
  environment variables win. Other `.env.*.local` mode files are not loaded.
- Raspberry Pi secrets live in root-owned
  `/etc/uconn-isomorphic/environment` with mode `0600`, inside a root-owned
  mode-`0755` configuration directory and outside Git/immutable releases. The
  directory is traversable so the normal professor account can read the
  non-secret `0644` deploy config; only root can read the environment file.
  systemd loads this after optional release defaults so device settings win.
- Never print, log, screenshot, commit, or paste real values into chat.
- If a secret may have entered Git history or logs, revoke/rotate it first,
  then handle history cleanup separately.

The repository previously tracked `site/.env`. Treat every credential that was
ever stored there as exposed: revoke or rotate it before sharing this history.
The current tree ignores both `site/.env` and `site/.env.local`; use
`site/.env.example` only as a non-secret reference.

`PAYPAL_DONATE_URL` is an optional public URL, not a PayPal credential. The
backend publishes it only when it is HTTPS on `paypal.com` or `paypal.me`.

## Access boundary

Set `APP_ACCESS_TOKEN` whenever the application is reachable beyond localhost.
Manually configured values must be 32-512 characters using the documented
secret-safe alphabet; invalid or weak values fail startup.
When configured, API clients must send the matching `X-App-Access-Token`.
Health/bootstrap GET routes remain public:

- `/api/health`
- `/api/openai/status`
- `/api/public-config`
- `/version.json`

OpenAPI/Swagger documentation is disabled while token protection is active.
Authenticated `/api/readiness` reports readiness booleans without returning
secrets.

This is a shared-token boundary, not per-user identity, SSO, or role-based
access control. User/Teacher/Dev UI modes are not authorization.

## Cost and abuse protection

Cost-bearing POST routes use an in-process, IP-scoped sliding-window limiter,
including local compatibility mode when no access token is set. Defaults are
12 requests per 60 seconds, configurable with `APP_RATE_LIMIT_REQUESTS` and
`APP_RATE_LIMIT_WINDOW_SECONDS`.

Every provider-cost-bearing start additionally requires a same-origin
client-generated `X-Idempotency-Key`. SQLite atomically binds it to the
canonical request fingerprint and original resource ID for at least seven days
and while that resource record remains retained. Concurrent duplicates and
manual retries after an uncertain response return the original start response;
different-input reuse is rejected. The dashboard persists a pending key in
tab-local `sessionStorage` and does not automatically retry paid POSTs.

Limitations:

- counters reset on backend restart;
- there is no distributed coordination across multiple processes/hosts;
- proxy/IP trust must be reviewed before relying on IP identity;
- there is no per-student quota, usage ledger, dollar budget, or semester
  kill-switch;
- a valid shared token permits all protected operations.

Evaluation is disabled by default for ordinary web/CLI generation. Each
evaluated item uses eight rubric graders, so an evaluation of `N` items creates
approximately `8 × N` grader calls. Rate limits reduce request bursts but do
not cap OpenAI spend.

## Uploads, outputs, and privacy

Uploaded files and output artifacts can contain assessments, answers, grader
rationales, and generated course material. Restrict host access, choose explicit
retention limits, and back up only to an approved private destination.

Cleanup is destructive. Defaults are 24-hour/2-GiB upload retention and
7-day/500-run/20-GiB output retention, checked hourly. Configure
`UPLOAD_RETENTION_SECONDS`, `UPLOAD_MAX_BYTES`, `OUTPUT_RETENTION_SECONDS`,
`OUTPUT_MAX_RUNS`, `OUTPUT_MAX_BYTES`, and
`ARTIFACT_CLEANUP_INTERVAL_SECONDS` deliberately; archive required records
before the cleanup policy can remove them. These values are strictly bounded
and invalid, zero, or negative settings prevent startup rather than silently
disabling a limit or deleting every artifact.

`UPLOAD_MAX_BYTES` is also enforced at request time across the process. Actual
stored sessions and in-progress reservations are accounted together, so
concurrent or abandoned valid uploads receive HTTP 507 before the upload tree
would cross the configured capacity.

Each accepted run that reaches source archival stores portable snapshots under
its output `input_files/` directory. Include those copies in retention,
authorization, backup, and deletion policy; deleting only the upload-session
copy does not remove the archived source.

Path-traversal and filename checks do not replace authorization. Do not publish
workflow IDs or artifact URLs. Treat IP addresses or future student identifiers
as personal data with a stated purpose and retention period.

## Guardrails

Input/content guardrails screen moderation, prompt-injection, and on-topic
physics-question conditions. They reduce misuse but are not an authentication
or sandbox boundary. The current registry uses `failClosed: true`: detected
violations and safety-service infrastructure failures block the workflow as a
precaution. Reassess this explicitly if the policy changes.

The complete input classifier phase and the combined content
injection/academic classifier phase each have an aggregate 300-second and
16-request default budget, controlled by
`OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS` and
`OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS`. Budget exhaustion always blocks,
even under a fail-open infrastructure policy, so a degraded classifier cannot
continue making sequential paid calls. Required prompts and runtime templates
are also validated locally before the first provider request.

Cached replays and uploaded from-agent-input payloads are screened again. Do
not weaken that behavior: replayed prompts or structured content may be
attacker-controlled even when the original run was trusted.

The ordinary document path still asks a tool-enabled Extracter to read the
uploaded file before the extracted text reaches the content guardrail. The
filename, user instruction, and direct structured agent inputs are screened
earlier, but arbitrary instructions embedded inside a PDF, document, or image
are not deterministically parsed and quarantined first. The Extracter therefore
runs in its own explicit container: after the content guardrail accepts, the
application must delete that whole container and its provider file objects
before it creates a fresh downstream container with no source-file IDs. A
cleanup failure blocks downstream execution. This contains mutable provider
state but does not make the original bytes trustworthy. Treat uploads as
trusted course material, not public anonymous content. A public/student upload
surface requires a separate bounded local parsing/OCR quarantine stage before
provider tools receive the document.

The extraction container installs document tools before source upload, but the
current Containers API has no supported update operation that can disable its
network policy afterward. Its narrow distro/PyPI allowlist therefore remains
active while the Extracter sees the source. [OpenAI warns that allowlisted
endpoints can still become prompt-injection exfiltration
paths](https://developers.openai.com/api/docs/guides/tools-shell#network-access).
Package hosts are operationally necessary here, so this remains a
trusted-upload boundary. A future provider-supported immutable prebuilt image,
or a local/offline parser/OCR stage, should remove that overlap.

OpenAI Agents SDK tracing is disabled by default and trace payloads exclude
sensitive data. Leave `OPENAI_AGENTS_DISABLE_TRACING` enabled for real course
material unless a reviewed debugging session specifically requires traces.

## Network deployment

- Preview binds to localhost by default. Use `--lan` only on a trusted network.
- Start Raspberry Pi deployment locally.
- Do not enable a public Cloudflare route until token rejection/acceptance,
  rate limiting, download protection, and backup/restore have been verified on
  that release.
- For Cloudflare public hosting, enable **Always Use HTTPS**, verify plain HTTP
  redirects to the same HTTPS hostname, and confirm the Cloudflare Access
  policy covers both the UI and `/api/` without a Bypass rule. Keep the Pi
  origin loopback-only; Cloudflare Tunnel requires no inbound port-forward.
- Keep Git deploy credentials read-only and scoped to this repository.

## Updates, backups, and rollback

Immutable releases and health-based rollback recover application code. They do
not restore persistent uploads, outputs, runtime databases, or server secrets.
The repository does not schedule automatic backups.

Before deployment or data-schema changes, use the installed root-owned
`uconn-isomorphic-backup-restore backup` command documented in
[`deploy/BACKUP_RESTORE.md`](deploy/BACKUP_RESTORE.md). It owns the deployment
lock, enters maintenance before its exact-zero active-work check, rejects
remaining writers and nested mounts, validates the selected release, and
publishes mode-`0600` archive/checksum files through the validated destination
directory descriptor. It refuses plaintext ext4/USB destinations: the mounted
Linux filesystem must have a verifiable LUKS dm-crypt ancestor. The archive
contains credentials, course/runtime data, and a verified bundle of the exact
Git commit plus reachable history; close the LUKS device after backup and keep
it offline. Restore refuses a plaintext staging copy and requires the matching
archive/checksum in one root-owned directory on equivalent verified LUKS
storage.
The co-located checksum detects corruption but is not an authenticity proof if
an attacker can rewrite both files; retain its value in a separate trusted
record when tamper provenance is required.

Restore uses root-only one-copy staging, safe member validation, same-filesystem
per-root stages, and a durable phase journal. The backend and nginx stay
fail-closed until a local-only reinstall rotates the shared browser token and
the exact backup commit passes direct loopback health/version checks. The
installer child must inherit the already-owned deployment lock and match the
root-only journal plus ephemeral authorization marker. Interrupted archive,
source-bundle, installer-build, and marker-bound stage artifacts are cleaned
only after strict identity/metadata checks; the durable transaction journal is
removed after all transaction cleanup, and only then may the parent atomically
refresh the installed helper from the verified active release. Retained
pre-restore roots require an explicit matching
transaction-ID `finalize` after acceptance. Restore and recovery remain
operator-triggered; a destructive data migration requires its own tested
compatibility/rollback plan and a physical spare-SD rehearsal.

## Dependency and platform maintenance

Use the lockfile with `npm ci`, a project-local `site/.venv`, and the documented
test/build gates. Review dependency advisories before each handoff release.

The Raspberry Pi application dependencies are hash-locked, but hosted provider
containers still install document/TeX tooling at workflow time from allowlisted
signed distro repositories and PyPI. The direct Python document-package
versions are pinned; transitive wheels and distro package versions are not
fully hash-locked in that ephemeral environment. A higher-assurance deployment
needs a prebuilt verified container or a complete provider-side hash lock.

The current OpenAI Evals API becomes read-only October 31, 2026 and shuts down
November 30, 2026. Treat replacement of the evaluation subsystem as a
time-bounded security/reliability dependency, not an optional future cleanup.
