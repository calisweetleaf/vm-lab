"""Real-filesystem, non-launch proofs for the Phase P4 gate coordinator.

These tests deliberately create a tiny real qcow2 through the installed
``qemu-img`` and run only ``--version`` probes for the installed
``qemu-system-x86_64``.  They never present a valid execution token, never
invoke the public ``execute`` mode, and never launch a QEMU machine.  The
consumed boundary here is the gate's pre-daemon rejection and its exact
prepared-fixture contract, not GATE-P4 physical machine truth.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "run_gate_p4.py"
if os.fspath(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(SOURCE_ROOT))

from somnus_vm.host.images import ImageManifest, ToolVersion  # noqa: E402
from somnus_vm.host.qemu_runtime_journal import CHECKPOINT_ORDER  # noqa: E402
from somnus_vm.host.p4_gate import (  # noqa: E402
    P4_GATE_AUTHORIZATION_PREFIX,
    P4_GATE_MATRIX_AUTHORIZATION_PREFIX,
    P4_GATE_MATRIX_SPEC_NAME,
    P4_GATE_CONFIG_NAME,
    P4_GATE_EVIDENCE_DIRECTORY,
    P4_GATE_SPEC_NAME,
    GateProductionPath,
    P4GateError,
    P4GateMatrixSpec,
    P4GateSpec,
    preflight_gate,
    preflight_gate_matrix,
    prepare_gate_fixture,
    prepare_gate_matrix,
)


_ROOT_NAMES = ("state", "runtime", "logs", "images", "backups", "secrets")
_QEMU_BINARY = shutil.which("qemu-system-x86_64")
_QEMU_IMG_BINARY = shutil.which("qemu-img")


def _tree_fingerprint(root: Path) -> tuple[tuple[str, str, int, int, str], ...]:
    """Return exact path/type/mode/size/hash facts for one private fixture."""

    rows: list[tuple[str, str, int, int, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: os.fspath(item)):
        details = path.lstat()
        relative = os.fspath(path.relative_to(root))
        if path.is_symlink():
            kind = "symlink"
            digest = os.readlink(path)
        elif path.is_dir():
            kind = "directory"
            digest = ""
        elif path.is_file():
            kind = "file"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            kind = "other"
            digest = ""
        rows.append(
            (relative, kind, stat.S_IMODE(details.st_mode), details.st_size, digest)
        )
    return tuple(rows)


@unittest.skipUnless(
    _QEMU_BINARY is not None and _QEMU_IMG_BINARY is not None,
    "P4 coordinator proofs require installed qemu-system-x86_64 and qemu-img",
)
class P4GateCoordinatorNonLaunchTests(unittest.TestCase):
    """Exercise preparation and fail-closed preflight without QEMU launch."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="vm-lab-p4-gate-coordinator-",
            dir="/tmp",
        )
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)
        self.source = self.root / "tiny-source.qcow2"
        completed = subprocess.run(
            [str(_QEMU_IMG_BINARY), "create", "-f", "qcow2", str(self.source), "8M"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "qemu-img could not create real coordinator source: "
                f"{completed.stderr.decode('utf-8', errors='replace')}"
            )
        os.chmod(self.source, 0o600)
        self.manifest_path = self.root / "tiny-source-manifest.json"
        self._write_manifest()
        self.production = self.root / "operator-production-aipc.qcow2"
        self.production.write_bytes(b"not-a-disposable-fixture")
        os.chmod(self.production, 0o600)
        self.exclusions = (GateProductionPath("operator-aipc", str(self.production)),)

    def tearDown(self) -> None:
        for root in getattr(self, "matrix_roots", ()):
            shutil.rmtree(root, ignore_errors=True)
        self.temporary.cleanup()

    def _write_manifest(self) -> None:
        manifest = ImageManifest(
            schema_version=1,
            image_id=uuid4(),
            origin="vm-lab-p4-gate-test",
            sha256=hashlib.sha256(self.source.read_bytes()).hexdigest(),
            size_bytes=self.source.stat().st_size,
            format="qcow2",
            virtual_size_bytes=8 * 1024 * 1024,
            backing_chain=(),
            architecture="x86_64",
            os_family="ubuntu",
            os_release="24.04",
            os_variant="minimal",
            created_at="2026-08-07T00:00:00.000000Z",
            tool_versions=(ToolVersion("qemu-img", "8.2.2"),),
            guest_payload_manifest=(),
        )
        self.manifest_path.write_bytes(manifest.canonical_bytes)
        os.chmod(self.manifest_path, 0o600)

    def _prepare(
        self,
        name: str = "fixture",
        *,
        exclusions: tuple[GateProductionPath, ...] | None = None,
    ) -> Path:
        return prepare_gate_fixture(
            fixture_root=self.root / name,
            manifest_path=self.manifest_path,
            source_path=self.source,
            production_exclusions=self.exclusions if exclusions is None else exclusions,
            qemu_binary=str(_QEMU_BINARY),
            qemu_img_binary=str(_QEMU_IMG_BINARY),
        )

    def _assert_no_runtime_mutation(self, fixture_root: Path) -> None:
        self.assertFalse((fixture_root / "state" / "registry.sqlite3").exists())
        self.assertFalse((fixture_root / "runtime" / "control.sock").exists())
        self.assertEqual(list((fixture_root / "logs").iterdir()), [])
        for forbidden_suffix in (".pid", ".qmp", ".sock"):
            self.assertEqual(
                list(fixture_root.rglob(f"*{forbidden_suffix}")),
                [],
            )

    def _prepare_matrix(self) -> Path:
        if not hasattr(self, "matrix_roots"):
            self.matrix_roots: list[Path] = []
        while True:
            matrix_root = Path("/tmp") / f"m{uuid4().hex[:6]}"
            if not matrix_root.exists():
                break
        self.matrix_roots.append(matrix_root)
        return prepare_gate_matrix(
            matrix_root=matrix_root,
            manifest_path=self.manifest_path,
            source_path=self.source,
            production_exclusions=self.exclusions,
            qemu_binary=str(_QEMU_BINARY),
            qemu_img_binary=str(_QEMU_IMG_BINARY),
        )

    def _assert_matrix_has_no_runtime_mutation(self, matrix_root: Path) -> None:
        self.assertFalse((matrix_root / "matrix-progress.json").exists())
        self.assertFalse((matrix_root / "matrix-failure.json").exists())
        self.assertFalse((matrix_root / "matrix-result.json").exists())
        for scenario_root in (matrix_root / "scenarios").iterdir():
            self.assertTrue(scenario_root.is_dir())
            self._assert_no_runtime_mutation(scenario_root)

    @staticmethod
    def _write_canonical(path: Path, value: object) -> None:
        path.write_bytes(
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        )
        os.chmod(path, 0o600)

    def test_prepare_and_preflight_matrix_create_exact_serial_fresh_scenarios(
        self,
    ) -> None:
        matrix_path = self._prepare_matrix()
        matrix_root = matrix_path.parent

        self.assertEqual(matrix_path, matrix_root / P4_GATE_MATRIX_SPEC_NAME)
        self.assertEqual(
            {entry.name for entry in matrix_root.iterdir()},
            {"scenarios", P4_GATE_MATRIX_SPEC_NAME},
        )
        matrix, manifest, preflight = preflight_gate_matrix(matrix_path)

        self.assertEqual(len(matrix.scenarios), len(CHECKPOINT_ORDER) + 1)
        self.assertEqual(
            tuple(item.ordinal for item in matrix.scenarios),
            tuple(range(len(CHECKPOINT_ORDER) + 1)),
        )
        self.assertEqual(
            tuple(item.name for item in matrix.scenarios),
            ("00-success",)
            + tuple(
                f"{ordinal:02d}-{checkpoint}"
                for ordinal, checkpoint in enumerate(CHECKPOINT_ORDER, start=1)
            ),
        )
        self.assertEqual(
            tuple(item.kill_at_checkpoint for item in matrix.scenarios),
            (None,) + CHECKPOINT_ORDER,
        )
        run_ids = tuple(item.run_id for item in matrix.scenarios)
        self.assertEqual(len(set(run_ids)), len(run_ids))
        self.assertNotIn(matrix.matrix_id, run_ids)

        scenario_specs = tuple(
            P4GateSpec.from_path(item.spec_path) for item in matrix.scenarios
        )
        declaration_ids = tuple(
            item.declaration_request_id for item in scenario_specs
        )
        self.assertEqual(len(set(declaration_ids)), len(declaration_ids))
        self.assertEqual(set(declaration_ids) & set(run_ids), set())
        self.assertEqual(
            tuple(item.run_id for item in scenario_specs),
            run_ids,
        )
        self.assertEqual(
            tuple(item.fixture_root for item in scenario_specs),
            tuple(
                str(matrix_root / "scenarios" / f"{item.ordinal:02d}")
                for item in matrix.scenarios
            ),
        )
        self.assertEqual(
            tuple(
                hashlib.sha256(item.canonical_bytes).hexdigest()
                for item in scenario_specs
            ),
            tuple(item.spec_sha256 for item in matrix.scenarios),
        )
        self.assertEqual(manifest.sha256, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(preflight.source_sha256, manifest.sha256)
        self.assertEqual(preflight.source_size_bytes, self.source.stat().st_size)
        self.assertEqual(
            len(preflight.scenario_preflight_sha256),
            len(CHECKPOINT_ORDER) + 1,
        )
        self.assertEqual(
            len(set(preflight.scenario_preflight_sha256)),
            len(CHECKPOINT_ORDER) + 1,
        )
        self._assert_matrix_has_no_runtime_mutation(matrix_root)

    def test_wrong_matrix_execution_phrase_denies_before_runtime_mutation(self) -> None:
        matrix_path = self._prepare_matrix()
        matrix_root = matrix_path.parent
        before = _tree_fingerprint(matrix_root)

        with self.assertRaisesRegex(
            P4GateError,
            "explicit full GATE-P4 matrix authority is absent",
        ):
            preflight_gate_matrix(
                matrix_path,
                authorization=f"{P4_GATE_MATRIX_AUTHORIZATION_PREFIX}{uuid4()}",
                require_execution_authority=True,
            )

        self.assertEqual(_tree_fingerprint(matrix_root), before)
        self._assert_matrix_has_no_runtime_mutation(matrix_root)

    def test_matrix_order_path_and_scenario_spec_tamper_fail_before_runtime(
        self,
    ) -> None:
        matrix_path = self._prepare_matrix()
        matrix_root = matrix_path.parent
        baseline_matrix = json.loads(matrix_path.read_text(encoding="ascii"))
        baseline_spec_paths = tuple(
            Path(item["spec_path"]) for item in baseline_matrix["scenarios"]
        )
        baseline_specs = {path: path.read_bytes() for path in baseline_spec_paths}
        before = _tree_fingerprint(matrix_root)

        with self.subTest("scenario order"):
            tampered = json.loads(json.dumps(baseline_matrix))
            tampered["scenarios"][1], tampered["scenarios"][2] = (
                tampered["scenarios"][2],
                tampered["scenarios"][1],
            )
            self._write_canonical(matrix_path, tampered)
            with self.assertRaisesRegex(P4GateError, "ordinals are not contiguous"):
                P4GateMatrixSpec.from_path(matrix_path)
            self._write_canonical(matrix_path, baseline_matrix)

        with self.subTest("escaped scenario specification path"):
            tampered = json.loads(json.dumps(baseline_matrix))
            tampered["scenarios"][0]["spec_path"] = tampered["scenarios"][1][
                "spec_path"
            ]
            self._write_canonical(matrix_path, tampered)
            with self.assertRaisesRegex(
                P4GateError,
                "outside its exact owner root",
            ):
                P4GateMatrixSpec.from_path(matrix_path)
            self._write_canonical(matrix_path, baseline_matrix)

        with self.subTest("scenario specification content"):
            scenario_path = baseline_spec_paths[0]
            tampered_spec = json.loads(baseline_specs[scenario_path].decode("ascii"))
            tampered_spec["declaration_request_id"] = str(uuid4())
            self._write_canonical(scenario_path, tampered_spec)
            with self.assertRaisesRegex(
                P4GateError,
                "scenario specification changed after preparation",
            ):
                preflight_gate_matrix(matrix_path)
            scenario_path.write_bytes(baseline_specs[scenario_path])
            os.chmod(scenario_path, 0o600)

        self.assertEqual(_tree_fingerprint(matrix_root), before)
        self._assert_matrix_has_no_runtime_mutation(matrix_root)

    def test_prepare_and_preflight_create_only_the_exact_private_fixture(self) -> None:
        spec_path = self._prepare()
        fixture_root = self.root / "fixture"

        self.assertEqual(spec_path, fixture_root / "runtime" / P4_GATE_SPEC_NAME)
        self.assertEqual(
            {entry.name for entry in fixture_root.iterdir()},
            set(_ROOT_NAMES),
        )
        for name in _ROOT_NAMES:
            details = (fixture_root / name).lstat()
            self.assertTrue(stat.S_ISDIR(details.st_mode))
            self.assertEqual(stat.S_IMODE(details.st_mode), 0o700)
        self.assertEqual(
            {entry.name for entry in (fixture_root / "runtime").iterdir()},
            {P4_GATE_CONFIG_NAME, P4_GATE_SPEC_NAME, P4_GATE_EVIDENCE_DIRECTORY},
        )
        self._assert_no_runtime_mutation(fixture_root)

        spec, configuration, manifest, preflight = preflight_gate(spec_path)

        self.assertEqual(spec.fixture_root, str(fixture_root))
        self.assertEqual(
            spec.authorization_token,
            f"{P4_GATE_AUTHORIZATION_PREFIX}{spec.run_id}",
        )
        self.assertEqual(configuration.storage.state_root, fixture_root / "state")
        self.assertEqual(
            manifest.sha256,
            hashlib.sha256(self.source.read_bytes()).hexdigest(),
        )
        self.assertEqual(preflight.source_sha256, manifest.sha256)
        self.assertEqual(preflight.source_size_bytes, self.source.stat().st_size)
        self.assertEqual(preflight.qemu["path"], str(Path(_QEMU_BINARY).resolve()))
        self.assertEqual(
            preflight.qemu_img["path"],
            str(Path(_QEMU_IMG_BINARY).resolve()),
        )
        self.assertEqual(preflight.qemu["owner_uid"], 0)
        self.assertEqual(preflight.qemu_img["owner_uid"], 0)
        self._assert_no_runtime_mutation(fixture_root)

    def test_absent_exact_token_denies_before_daemon_registry_or_qemu_mutation(
        self,
    ) -> None:
        spec_path = self._prepare()
        fixture_root = self.root / "fixture"
        before = _tree_fingerprint(fixture_root)

        with self.assertRaisesRegex(
            P4GateError,
            "explicit disposable GATE-P4 authority",
        ):
            preflight_gate(
                spec_path,
                authorization="wrong-token",
                require_execution_authority=True,
            )

        completed = subprocess.run(
            [
                sys.executable,
                str(GATE_SCRIPT),
                "_worker",
                "launch",
                "--spec",
                str(spec_path),
                "--authorization",
                "wrong-token",
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            2,
            completed.stderr.decode("utf-8", errors="replace"),
        )
        self.assertIn(
            b"GATE-P4 denied: explicit disposable GATE-P4 authority is absent",
            completed.stderr,
        )
        self.assertEqual(_tree_fingerprint(fixture_root), before)
        self._assert_no_runtime_mutation(fixture_root)

    def test_source_hash_and_size_tamper_deny_preflight_without_runtime_creation(
        self,
    ) -> None:
        spec_path = self._prepare()
        fixture_root = self.root / "fixture"
        original = self.source.read_bytes()
        altered = bytes([original[0] ^ 0x01]) + original[1:]
        self.source.write_bytes(altered)
        os.chmod(self.source, 0o600)

        with self.assertRaisesRegex(P4GateError, "source image bytes disagree"):
            preflight_gate(spec_path)
        self._assert_no_runtime_mutation(fixture_root)

        with self.source.open("ab") as handle:
            handle.write(b"size-tamper")
            handle.flush()
            os.fsync(handle.fileno())
        with self.assertRaisesRegex(P4GateError, "source image bytes disagree"):
            preflight_gate(spec_path)
        self._assert_no_runtime_mutation(fixture_root)

    def test_noncanonical_or_escaped_spec_is_denied_without_runtime_creation(
        self,
    ) -> None:
        spec_path = self._prepare()
        fixture_root = self.root / "fixture"
        raw = spec_path.read_bytes()
        spec_path.write_bytes(raw + b"\n")
        os.chmod(spec_path, 0o600)
        with self.assertRaisesRegex(P4GateError, "not canonical JSON"):
            P4GateSpec.from_path(spec_path)
        self._assert_no_runtime_mutation(fixture_root)

        spec_path.write_bytes(raw)
        decoded = json.loads(raw.decode("ascii"))
        decoded["config_path"] = str(fixture_root / "state")
        spec_path.write_bytes(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("ascii")
        )
        os.chmod(spec_path, 0o600)
        with self.assertRaisesRegex(P4GateError, "config must be inside"):
            preflight_gate(spec_path)
        self._assert_no_runtime_mutation(fixture_root)

    def test_production_exclusion_overlap_and_symlink_escape_are_denied(self) -> None:
        overlap_root = self.root / "fixture-overlap"
        overlap = GateProductionPath("production-root", str(overlap_root.parent))
        spec_path = self._prepare("fixture-overlap", exclusions=(overlap,))
        with self.assertRaisesRegex(P4GateError, "overlaps production exclusion"):
            preflight_gate(spec_path)
        self._assert_no_runtime_mutation(overlap_root)

        escaped = self.root / "production-symlink"
        escaped.symlink_to(overlap_root.parent, target_is_directory=True)
        with self.assertRaisesRegex(P4GateError, "must not traverse a symlink"):
            GateProductionPath("production-escape", str(escaped))


if __name__ == "__main__":
    unittest.main(verbosity=2)
