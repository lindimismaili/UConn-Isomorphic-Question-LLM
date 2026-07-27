# Raspberry Pi Backup and Restore

Use the root-owned installed `uconn-isomorphic-backup-restore` command. Do not copy/paste an
ad-hoc `tar` extraction into `/`: the command owns the deployment lock,
quiesces paid work, validates the active release, creates non-overwriting
checksum-first generations, and journals restore progress across signals or
power loss.

The procedure is intentionally local-only. Restore disables automatic updates,
Cloudflare connectors, nginx, and the backend before changing managed data.
Public hosting remains disabled after completion.

## Create a backup

Use a separately mounted ext4, btrfs, XFS, or F2FS filesystem backed by LUKS
dm-crypt. The command walks Linux sysfs block-device ancestry and fails closed
unless it finds a `CRYPT-LUKS1` or `CRYPT-LUKS2` device-mapper identity; an
ordinary ext4 USB stick is refused. The destination must enforce Unix
owner/mode metadata and have the managed-data size plus at least 512 MiB free.
Each managed root and every descendant must remain on its parent filesystem;
mounting a managed root itself, or placing a nested/bind mount below it, is
rejected rather than silently copied.

From the reviewed professor checkout:

```bash
read -r -p 'Mounted encrypted backup filesystem: ' BACKUP_MOUNT
BACKUP_DESTINATION="${BACKUP_MOUNT%/}/uconn-isomorphic-backups"
sudo install -d -o root -g root -m 700 "${BACKUP_DESTINATION}"
sudo /usr/local/libexec/uconn-isomorphic-backup-restore backup \
  --destination "${BACKUP_DESTINATION}"
```

The command:

- takes the same nonblocking deployment lock as install/update/rollback, then
  holds the conflicting exclusive work lease while it drains, snapshots, and
  safely reopens the service;
- enters maintenance before the final exact-zero active-work check;
- stops the updater and backend, then rejects any remaining backend process,
  listener, or process with a managed file open;
- proves `current` is the exact root-owned release selected by its full commit
  SHA and verifies its schema-2 release marker, requirements/package-lock
  hashes, immutable digest (including `site/dist`), version metadata,
  ownership, modes, structurally checked virtual environment, and persistent
  links;
- rejects symlinks, special files, filesystem boundaries, and nested mounts in
  all managed roots;
- validates the complete allowlisted deployment config and persistent runtime
  environment before copying credentials;
- records the exact commit and pinned HTTPS GitHub remote in a schema-2
  manifest and embeds a verified Git bundle containing that commit and its
  reachable history, so the restore installer does not fetch mutable source;
- writes unique mode-`0600` `.partial` files through the already validated
  destination directory descriptor, validates the archive, fsyncs both, and
  publishes the checksum first and archive second within that same opened
  directory without overwriting an older generation; a later run removes only
  strictly named, root-owned interrupted partials; and
- restarts and exact-commit health-checks the backend before removing
  maintenance or restarting a previously active update timer.

Keep the `.tgz` and matching `.sha256` together. Publication is deliberately
checksum-first, not a filesystem-atomic two-file transaction: a sudden power
loss can leave a harmless checksum without its archive. Never select an
unpaired generation for restore.

The archive contains the OpenAI key, browser token, professor-selected runtime
policy, course data, and the reachable Git history. LUKS protects the block
device only while it is locked: unmount and close it after the backup, keep it
offline, and never copy the pair onto plaintext storage.

The adjacent `.sha256` detects accidental corruption, but it is unkeyed and
does not authenticate the archive against someone who can rewrite both files
on an unlocked device. Physical/offline custody is therefore part of the
security boundary. If tamper provenance matters, copy the checksum value into
a separately protected signed handoff record or sign it with the institution's
existing artifact-signing process; do not store that only beside the archive.

## Restore on a spare card or replacement Pi

First install 64-bit Raspberry Pi OS and clone the fixed repository URL as the
normal professor account. Obtain the full 40-hex commit from the backup
filename or signed handoff record, then fetch and detach at that exact commit:

```bash
(
set -euo pipefail
read -r -p 'Reviewed 40-hex backup commit: ' RESTORE_COMMIT
printf '%s\n' "${RESTORE_COMMIT}" | grep -Eq '^[0-9a-f]{40}$'
git clone https://github.com/lindimismaili/UConn-IsomorphicQuestionLLM.git \
  UConn-IsomorphicQuestionLLM
cd UConn-IsomorphicQuestionLLM
GIT_TERMINAL_PROMPT=0 GIT_NO_REPLACE_OBJECTS=1 \
  git -c core.hooksPath=/dev/null -c protocol.file.allow=never \
  fetch origin "${RESTORE_COMMIT}"
GIT_NO_REPLACE_OBJECTS=1 \
  git -c core.hooksPath=/dev/null checkout --detach "${RESTORE_COMMIT}"
test "$(GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD)" = "${RESTORE_COMMIT}"
)
```

Run the normal installer once if this is a blank card, leave Cloudflare and
automatic updates disabled, then stop using the dashboard. Restore requires the
standard installed service/config layout and the same pinned GitHub remote.
The embedded source bundle makes the transaction reproducible after this
compatible helper and reviewed checkout exist; it is not a bootstrap image for
a completely unprovisioned Pi.

Start the data restore:

```bash
cd UConn-IsomorphicQuestionLLM
read -r -p 'Absolute backup archive path: ' RESTORE_ARCHIVE
read -r -p 'Absolute matching checksum path: ' RESTORE_CHECKSUM
read -r -p 'Reviewed 40-hex backup commit: ' RESTORE_COMMIT
sudo /usr/local/libexec/uconn-isomorphic-backup-restore restore \
  --archive "${RESTORE_ARCHIVE}" \
  --checksum "${RESTORE_CHECKSUM}" \
  --checkout "$(pwd -P)" \
  --commit "${RESTORE_COMMIT}"
```

The archive and checksum must still be in the same root-owned directory on a
separately mounted, verifiably LUKS-backed Linux filesystem. Restore refuses a
plaintext staging copy, a symlink, a hard-linked file, mismatched parent
directories, unsafe ownership/modes, or an unpaired checksum.

The command copies the archive once through an `O_NOFOLLOW` descriptor into a
root-only staging directory and hashes and validates that immutable copy. It
checks canonical archive paths, file types, modes, member/path/expanded-size
caps, the source-bundle digest, the exact commit, the pinned remote, strict
allowlisted `deploy.conf` and environment data, and absence of unresolved
activation/integrity state. It materializes the installer in a root-owned
partial directory and publishes the complete immutable snapshot with one
rename. It then fsyncs a root-owned `quiescing` journal before writing
maintenance or disabling the first unit, so any partial stop has a recoverable
transaction.
Each managed root is extracted into a same-filesystem staging directory; the
helper never merges into an old tree and fsyncs the journal again before each
rename:

```text
/var/lib/uconn-isomorphic-restore.pending
```

The backend and nginx read the pending marker and remain fail-closed. Paid
starts hold a shared lease on the stable root-owned work-lock inode; each
restore/recover/install mutation phase holds the conflicting exclusive lease,
while the durable pending and maintenance markers keep the gaps between
operator commands fail-closed. The normal installer provisions that single-link
`0444` inode under a root-owned `0755` directory and installs a systemd-tmpfiles
rule to recreate it after reboot without replacing a safe live inode. Per-root
originals are retained beside each managed root as
`.pre-restore-<transaction-id>` until the operator removes them after a
successful physical restore drill.

Complete the exact-commit reinstall and offline browser-token rotation:

```bash
sudo /usr/local/libexec/uconn-isomorphic-backup-restore install
```

The replacement browser token is shown once. The backup/restore parent keeps
the deployment lock while its sanitized root child inherits that same locked
file description; the child cannot enter this hidden mode with a free or
unrelated lock. A root-owned authorization marker under `/run` permits only the
in-progress install and disappears on reboot. The durable pending marker
remains until the selected release and direct loopback health/version endpoints
prove the exact backup commit. The restore child does not replace the installed
helper while that journal exists; after journal removal, the parent atomically
refreshes the helper from the verified active release. The replacement Pi's
current hostname becomes the local `.local` address. Automatic updates and
Cloudflare stay disabled.
The final output prints the non-secret transaction ID; record it for the later
explicit finalization step.

## Interrupted restore

Inspect only non-secret state:

```bash
sudo /usr/local/libexec/uconn-isomorphic-backup-restore status
```

After `SIGKILL`, reboot, or power loss during extraction/swap, rerun:

```bash
sudo /usr/local/libexec/uconn-isomorphic-backup-restore recover
```

Recovery reacquires the deployment and exclusive work locks, re-disables every
exposure unit when the swap is unfinished, repeats
process/listener/open-file quiescence, validates the same checkout and durable
archive digest, reconciles each rename from the journal, and resumes
idempotently. If installation had already reached the durable `completed`
phase, recovery instead restarts the exact release if needed and finishes
cleanup without requiring an installer snapshot that was already removed. Do
not remove the journal, restore-allow marker, maintenance marker, stage
directories, or `.pre-restore-*` roots by hand.

If the restored generation must be abandoned, restore the retained pre-restore
roots with:

```bash
sudo /usr/local/libexec/uconn-isomorphic-backup-restore rollback
```

Rollback quarantines a new/partial restored root rather than deleting it,
restores every available original, fsyncs both `/etc` and `/var` parent
filesystems, retains a root-only rolled-back journal, and leaves all services
disabled in maintenance. Rerun the reviewed normal installer only after
investigating the failure.

After the physical drill succeeds, permanently remove only the retained
marker-bound pre-restore roots for that exact transaction:

```bash
read -r -p 'Completed restore transaction ID: ' RESTORE_TRANSACTION_ID
sudo /usr/local/libexec/uconn-isomorphic-backup-restore finalize \
  --transaction-id "${RESTORE_TRANSACTION_ID}"
```

`finalize` refuses while any deployment/restore/integrity/maintenance state is
pending, re-verifies the active commit and direct loopback health, and deletes
only paths carrying the matching transaction marker. This is intentionally
explicit and irreversible; do it only after the retained rollback data is no
longer needed. Failed-restored-root quarantines created by `rollback` are not
silently deleted.

## Acceptance drill

Before professor handoff, perform one physical spare-SD restore:

1. Create two successive backup generations and prove the first was not
   overwritten.
2. Verify both checksums.
3. Interrupt one restore during installer-snapshot construction, one after
   staging, one during root swapping, and one during rollback; run `recover`
   after reboot and confirm each transaction converges.
4. Finish `install`, authenticate with the newly shown browser token, and
   confirm the UI, uploads, outputs, history, and one no-cost health check.
5. Confirm `systemctl is-enabled uconn-isomorphic-update.timer` is disabled,
   the Cloudflare connector is disabled, and `/version.json` reports the backup
   commit.
6. Record the completed transaction ID, run `finalize` only after the restored
   data is accepted, and verify only the matching `.pre-restore-*` roots were
   removed.
7. Keep the physical drill record with the signed release handoff.

The automated suite exercises archive traversal/link rejection, mount-boundary
rejection, exact manifest commits, canonical path aliases, hostile proxy
bypass, forged journals, interrupted-secret cleanup, journal-last finalization,
and kill points around root rename and rollback. Linux-only tests exercise the
real flock interface when run as root. The suite cannot prove SD-card, USB
enclosure, filesystem, real power-loss, systemd, or network behavior; the
physical drill remains a release requirement.
