---
name: handoff-readiness
description: Audit a release candidate for professor handoff across setup, secrets, access control, cost defaults, tests, build, documentation, Raspberry Pi operations, backups, and remaining limitations.
---

```toml
id = "03982c5a74dd47a43c206abe6258f8e2"
```

# Verify professor handoff readiness

Use this skill for a release-candidate audit, not ordinary code-change testing.
It complements `run-tests` by checking operational and documentation contracts.
Do not print secret values or run a paid OpenAI workflow unless explicitly
authorized.

## Establish scope

Inspect `git status`, the release commit/tag, applicable instructions, and
`deploy/HANDOFF.md`. Preserve unrelated work. Report whether validation
is against a clean clone, the current checkout, or a Raspberry Pi/VM.

## Verify a clean setup

Confirm:

- for Raspberry Pi: an `aarch64`/`arm64` OS, Python 3.10+, at least 1,700 MiB
  RAM (4 GB-class recommended), and 8 GiB free on both required filesystems;
- for a fresh development clone: Python 3.12 with project-local `site/.venv`;
- a hash-verified install of `site/backend/requirements-dev.txt` so tests,
  Ruff, and audit tooling are present;
- Node 22.13+ LTS and lockfile installation with `npm -C site/frontend ci`;
- frontend production build and local-only preview startup;
- `/api/health`, `/version.json`, public configuration, authenticated
  readiness, and sanitized deployment-status responses.

Use `run-tests` for the full automated suite and record its results.

## Verify configuration without exposing values

Confirm presence, never contents, of:

- server-only `OPENAI_API_KEY`;
- `APP_ACCESS_TOKEN` when the site is reachable beyond localhost;
- optional `PAYPAL_DONATE_URL`, which must be an HTTPS PayPal URL;
- rate-limit and retention settings selected for the class.

Confirm the professor owns a dedicated OpenAI project and has chosen the
narrowest key/service-account permissions that still cover Responses, Files,
Containers/container files, Moderations, and Evals only while the legacy
evaluation path remains in use. Verify the project's current model/rate/spend
limits and alerts in the OpenAI dashboard; do not assume an alert alone stops
spend. Confirm an organization administrator has allowlisted exactly the six
current `setup.allowedDomains` values for hosted containers; the request-level
network policy cannot expand the organization list. Recheck the current
dashboard procedure against OpenAI's
[official shell-network guide](https://developers.openai.com/api/docs/guides/tools-shell#network-access),
then use one tiny, explicitly authorized generation to validate bootstrap and
compilation.
Validate the configured PayPal Donate URL in a private browser window, without
completing a real transaction.

Fail the handoff if Git tracks `site/.env`, `.env.local`, `.env.*.local`, or
another credential-bearing local configuration file. Confirm those paths are
ignored without opening them, and that the committed `site/.env.example`
template exists and contains only blank or commented values. Run a redaction-safe tracked-file secret scan
that reports only category and file/line, never matched text. If any credential
ever entered Git history, require revocation/rotation before sharing and record
history cleanup as a separate owner-approved operation.

Verify protected API calls reject missing/incorrect
`X-App-Access-Token` and accept the configured token. Confirm the built
frontend does not contain the OpenAI key or server access token.

## Verify cost and safety defaults

Confirm ordinary web and CLI generation leave evaluation disabled unless the
operator opts in. Record that each evaluated item uses eight rubric graders.
Confirm Create Data requires explicit approval for its bounded full-workflow
fan-out and does not misleadingly offer professor comparison without evaluation
artifacts.
Exercise both paid-route and upload/download resource rate limiting without
starting paid work. Confirm item/batch/ZIP caps, the disabled second generator
review, the 20,000-character fixed instruction boundary, generated-artifact and
per-grader token caps, provider-file expiry, and the configured fail-closed
guardrail policy are documented. Confirm ZIP documentation distinguishes the
128 MiB default source-tree cap from its 512 MiB hard maximum and warns that the
browser buffers the generated archive. Run automated tests with
`OPENAI_API_KEY= APP_ACCESS_TOKEN=` so local environment files cannot trigger
provider calls or accidentally exercise an authenticated boundary.

## Verify operations

On a scratch Pi/VM before the professor's device:

- prove preflight, clean install, restart, health check, immutable-release
  activation, failed-release handling, and code rollback;
- inventory persistent uploads, outputs, runtime databases, configuration, and
  secret files;
- create and verify a backup before changes;
- rehearse restore while services are stopped;
- state explicitly that code rollback is not a database/data rollback;
- keep public Cloudflare exposure disabled until a backup/restore drill,
  Cloudflare Access denial/login response, **Always Use HTTPS**, and an
  exact-host HTTP-to-HTTPS redirect are verified;
- require HTTPS for untrusted networks, validate the live certificate, and
  verify the reverse proxy does not buffer unauthenticated request bodies;
- treat the SD card as unencrypted at rest unless the operator separately
  provisions encrypted storage; physically secure the device and use encrypted
  backup media for sensitive or student-identifiable artifacts;
- prove power-loss recovery cannot leave an unverified release active.
- prove accepted paid web/CLI work holds a shared lease on the root-owned
  activity lock through provider and artifact cleanup, while deploy, backup,
  and restore acquire a conflicting exclusive lease before maintenance.

Do not claim automatic backups or dashboard mutation unless demonstrated in
the release candidate. Before external distribution, require owner/professor
sign-off that they have permission to distribute every tracked evaluation PDF,
rubric/calibration artifact, and professor-comparison workbook and that none
contains student-identifiable data; do not infer that approval from a technical
scan or inspect curated source contents without authorization. Record the
repository owner's licensing decision too.

## Report

Return a table of passed, failed, skipped, and paid/not-run checks, with exact
commands and evidence. List remaining limitations, Evals migration status, and
the professor decisions still required. A handoff is not ready while a required
check is failed or undocumented.
