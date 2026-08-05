"""Canonical smoke harness entrypoint for the reorganized VM lab.

Source: vm_lab reconstitution scope
Integrated: 2026-08-04
Purpose: Runs the direct integration harness used by the promotion ledger.
IO: Delegates to test_vm_lab, which writes timestamped run artifacts.
"""

from __future__ import annotations

from test_vm_lab import main


if __name__ == "__main__":
    raise SystemExit(main())
