#!/usr/bin/env bash
# Idempotent repository bootstrap for Somnus VM Lab.
#
# The live runtime is standard-library-only, so there are no project
# dependencies to install here. This step instead fails loud if any tool the
# repository's canonical commands and physical proofs require is missing,
# matching the repository's fail-closed doctrine. It performs read-only checks,
# so it is safe to run repeatedly against cached or partially prepared state.
set -euo pipefail

fail() { echo "install: FATAL: $*" >&2; exit 1; }

echo "== Somnus VM Lab install: verifying toolchain =="

command -v python >/dev/null 2>&1 || fail "python (python-is-python3) is not on PATH"
python - <<'PY' || fail "python must be >= 3.12"
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)
PY
echo "python:            $(python --version 2>&1) ($(command -v python))"

command -v qemu-img >/dev/null 2>&1 || fail "qemu-img is not on PATH"
echo "qemu-img:          $(qemu-img --version | head -1)"

command -v qemu-system-x86_64 >/dev/null 2>&1 || fail "qemu-system-x86_64 is not on PATH"
echo "qemu-system-x86_64: $(qemu-system-x86_64 --version | head -1)"

python -m pip --version >/dev/null 2>&1 || fail "pip is not available"
python - <<'PY' || fail "setuptools and wheel are required for the installed-wheel smoke lane"
import setuptools, wheel  # noqa: F401
PY
echo "packaging:         pip $(python -m pip --version | awk '{print $2}'), setuptools/wheel present"

echo "== install complete: toolchain satisfied =="
