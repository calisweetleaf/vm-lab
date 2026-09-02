#!/usr/bin/env bash
# Per-boot runtime reconciliation for Somnus VM Lab.
#
# Cloud Agent VMs are backed by overlayfs, where a regular file's st_dev differs
# from its parent directory's st_dev. The host launch-authority and disposable
# fixture checks enforce normal POSIX semantics (a file and the directory that
# contains it live on the same device), so on overlayfs those physical-security
# proofs fail with "path crosses a filesystem boundary".
#
# Mounting tmpfs at /tmp — where those tests create their disposable roots (and
# where the pinned P3 image fixture is expected) — restores same-device
# semantics, exactly as a normal Linux dev host with a tmpfs /tmp would provide.
# This is idempotent: it is a no-op when /tmp already exposes consistent devices.
#
# Note: a mount is scoped to the mount namespace it runs in. If the platform
# runs this `start` step in a detached namespace separate from the interactive
# agent shell, the mount may not be visible there. Running this script directly
# in the working shell (`bash .cursor/start.sh`) reconciles /tmp for that shell
# and all its subsequent commands, which is only needed for the launch-authority
# and disposable-fixture physical proofs (everything else needs no /tmp change).
set -uo pipefail

# Returns 0 when a freshly created file and its parent directory report the same
# st_dev under /tmp (the POSIX-normal case the physical proofs rely on).
tmp_devices_consistent() {
  python3 - <<'PY'
import os, sys, tempfile
try:
    d = tempfile.mkdtemp(dir="/tmp")
    f = os.path.join(d, ".probe")
    open(f, "w").close()
    ok = os.lstat(d).st_dev == os.lstat(f).st_dev
    os.remove(f); os.rmdir(d)
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
}

echo "== Somnus VM Lab start: reconciling /tmp filesystem semantics =="

if tmp_devices_consistent; then
  echo "start: /tmp already provides consistent file/dir devices; no action needed"
  exit 0
fi

echo "start: /tmp reports mismatched file/dir st_dev (overlayfs); mounting tmpfs at /tmp"
if sudo mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /tmp; then
  if tmp_devices_consistent; then
    echo "start: tmpfs mounted at /tmp; file/dir devices are now consistent"
    exit 0
  fi
  echo "start: WARNING: tmpfs mounted but /tmp devices still inconsistent" >&2
else
  echo "start: WARNING: could not mount tmpfs at /tmp (insufficient privileges?)" >&2
fi

# The environment remains usable; only the launch-authority / disposable-fixture
# physical proofs that assume same-device /tmp are affected. Do not block boot.
echo "start: continuing without tmpfs /tmp (affected: disposable_launch_authority proof)"
exit 0
