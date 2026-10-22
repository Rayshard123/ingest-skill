from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ingest_executor import ExecutionOptions, execute_plan, sha256


TEST_ROOT = Path(os.environ["INGEST_V1_TEST_ROOT"]).resolve()
GOVERNANCE_SOURCE = SCRIPT_DIR.parent / "runtime" / "governance"


ROOT_MOC = """---
tags: [MOC, main]
---

# Root MOC

## P1 Playbooks
"""

ZONE_MOC = """---
tags: [MOC, category]
parent: "[[_MOC_主干道]]"
---

# C3 MOC

## Existing Cards

- [[Existing Card]]`C3.1`
"""

EXISTING_CARD = """---
type: sop
zone: C
zone_id: C3.1
parent: "[[C03_通用流程]]"
tags: [existing]
sources:
  - codex://fixture/existing.txt
domain: operations
risk_level: none
confidence: high
created: '2026-08-01'
updated: '2026-08-01'
---

# Existing Card

**Existing summary.**

## Core

Existing material.

> [!MY-EXPERIENCE] Leon
> Keep this byte-for-byte.
"""


def write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "_ops/governance.lock.json":
            continue
        result[relative] = sha256(path)
    return result


class Fixture:
    def __init__(self, base: Path):
        self.base = base
        self.root = base / "vault"
        self.runtime = base / "runtime"
        self.governance = base / "governance"
        self.material = base / "raw-material.txt"
        self.plan = self.runtime / "ingest_plan.json"
        self.profile = base / "ingest-device.json"
        self.zone = self.root / "wiki" / "C_操作层" / "C03_通用流程"
        self.moc = self.zone / "C03_通用流程.md"
        self.card = self.zone / "Existing Card.md"

    def build(self) -> None:
        self.zone.mkdir(parents=True)
        (self.root / "wiki" / "A_原理层").mkdir(parents=True)
        (self.root / "wiki" / "D_策略沙盒").mkdir(parents=True)
        (self.root / "wiki" / "F_政策法规").mkdir(parents=True)
        (self.root / "wiki" / "G_通用知识树").mkdir(parents=True)
        (self.root / "wiki" / "P_场景层").mkdir(parents=True)
        (self.root / "_ops").mkdir()
        self.runtime.mkdir()
        self.governance.mkdir()
        write_utf8(self.root / "_SYSTEM.md", "# Fixture System\n")
        write_utf8(self.root / "_MOC_主干道.md", ROOT_MOC)
        write_utf8(self.moc, ZONE_MOC)
        write_utf8(self.card, EXISTING_CARD)
        write_utf8(self.material, "raw material v1\n")
        for name in ("governance_validate.py", "rebuild_registry.py", "frontmatter.py"):
            shutil.copy2(GOVERNANCE_SOURCE / name, self.governance / name)
        self.rebuild_registry()

    def rebuild_registry(self) -> None:
        output = self.base / "registry-output.md"
        result = subprocess.run(
            [sys.executable, str(self.governance / "rebuild_registry.py"), str(self.root), "--out", str(output)],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout.decode("utf-8") + result.stderr.decode("utf-8"))
        shutil.copy2(output, self.root / "_registry.md")

    def base_block(self) -> dict[str, Any]:
        return {
            "registry_sha256": sha256(self.root / "_registry.md"),
            "system_sha256": sha256(self.root / "_SYSTEM.md"),
            "materials": [
                {
                    "uri": "codex://fixture/raw-material.txt",
                    "local_path": str(self.material.resolve()),
                    "sha256": sha256(self.material),
                }
            ],
        }

    def create_operation(self) -> dict[str, Any]:
        return {
            "op_id": "create-001",
            "action": "create",
            "classification": {"zone": "C", "number": 3, "rationale": "Executable general workflow."},
            "title": "New Atomic Card",
            "parent": {
                "moc_title": "C03_通用流程",
                "heading": "## Existing Cards",
                "expected_sha256": sha256(self.moc),
            },
            "metadata": {
                "type": "sop",
                "tags": ["new", "workflow"],
                "sources": ["codex://fixture/raw-material.txt"],
                "domain": "operations",
                "risk_level": "none",
                "confidence": "high",
                "created": "2026-08-07",
                "updated": "2026-08-07",
            },
            "body": "# New Atomic Card\n\n**Summary.**\n\n## Core\n\nDeterministic content.\n",
            "dedup": {
                "decision": "create",
                "candidates": [
                    {
                        "title": "Existing Card",
                        "expected_sha256": sha256(self.card),
                        "similarity": 0.42,
                        "relation": "false_positive",
                        "rationale": "Different workflow.",
                    }
                ],
            },
        }

    def enrich_operation(self, *, mutate_my_experience: bool = False) -> dict[str, Any]:
        body = """# Existing Card

**Enriched summary.**

## Core

Existing material plus a verified increment.

> [!MY-EXPERIENCE] Leon
> Keep this byte-for-byte.
"""
        if mutate_my_experience:
            body = body.replace("Keep this byte-for-byte.", "AI changed this.")
        return {
            "op_id": "enrich-001",
            "action": "enrich",
            "classification": {"zone": "C", "number": 3, "rationale": "Existing identity."},
            "target": {"title": "Existing Card", "expected_sha256": sha256(self.card)},
            "parent": {"moc_title": "C03_通用流程", "expected_sha256": sha256(self.moc)},
            "metadata_patch": {
                "tags": ["existing", "enriched"],
                "sources": ["codex://fixture/existing.txt", "codex://fixture/raw-material.txt"],
                "updated": "2026-08-07",
            },
            "body": body,
            "dedup": {
                "decision": "enrich",
                "candidates": [
                    {
                        "title": "Existing Card",
                        "expected_sha256": sha256(self.card),
                        "similarity": 0.95,
                        "relation": "same_source",
                        "rationale": "Same model and source lineage.",
                    }
                ],
            },
        }

    def write_plan(self, operations: list[dict[str, Any]]) -> Path:
        payload = {
            "schema_version": "1.0",
            "plan_id": "fixture-plan",
            "created_at": "2026-08-07T16:00:00+08:00",
            "base": self.base_block(),
            "operations": operations,
        }
        self.plan.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.plan

    def write_profile(
        self,
        *,
        device: str = "A",
        hostname: str | None = None,
        work_root: Path | None = None,
        runtime_root: Path | None = None,
        python_executable: Path | None = None,
    ) -> Path:
        python = Path(sys.executable).resolve()
        payload = {
            "schema_version": "1.0",
            "device": device,
            "hostname": hostname or socket.gethostname(),
            "vault_root": str(self.root.resolve()),
            "work_root": str((work_root or self.runtime).resolve()),
            "runtime_root": str((runtime_root or python.parent).resolve()),
            "python_executable": str((python_executable or python).resolve()),
        }
        self.profile.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.profile

    def execute(
        self,
        *,
        hook=None,
        failpoints: set[str] | None = None,
        skip_index: bool = True,
        index_commands: list[list[str]] | None = None,
        governance_root: Path | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        return execute_plan(
            ExecutionOptions(
                root=self.root,
                plan_path=self.plan,
                device="A",
                work_root=self.runtime,
                sync_confirmed=True,
                governance_root=governance_root or self.governance,
                resume=resume,
                skip_index=skip_index,
                index_commands=index_commands,
                test_hook=hook,
                failpoints=failpoints or set(),
            )
        )


class IngestV1IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=TEST_ROOT)
        self.fixture = Fixture(Path(self.temporary.name))
        self.fixture.build()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_document_guard_nameerror_is_fixed(self) -> None:
        report = self.fixture.base / "guard.json"
        result = subprocess.run(
            [
                sys.executable,
                str(self.fixture.governance / "governance_validate.py"),
                "--root",
                str(self.fixture.root),
                "--document-guard",
                "--path",
                "wiki/C_操作层/C03_通用流程/Existing Card.md",
                "--report",
                str(report),
                "--json",
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertTrue(json.loads(result.stdout.decode("utf-8"))["ok"])

    def test_root_moc_document_guard_has_explicit_parent_exception(self) -> None:
        report = self.fixture.base / "root-moc-guard.json"
        result = subprocess.run(
            [
                sys.executable,
                str(self.fixture.governance / "governance_validate.py"),
                "--root",
                str(self.fixture.root),
                "--document-guard",
                "--path",
                "_MOC_主干道.md",
                "--report",
                str(report),
                "--json",
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertTrue(json.loads(result.stdout.decode("utf-8"))["ok"])

    def test_successful_create_commits_card_moc_and_registry(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        result = self.fixture.execute()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["reservations"], {"create-001": "C3.2"})
        new_card = self.fixture.zone / "New Atomic Card.md"
        self.assertTrue(new_card.is_file())
        self.assertIn("zone_id: C3.2", new_card.read_text(encoding="utf-8"))
        self.assertIn("[[New Atomic Card]]", self.fixture.moc.read_text(encoding="utf-8"))
        self.assertIn("wiki/C_操作层/C03_通用流程/New Atomic Card.md", (self.fixture.root / "_registry.md").read_text(encoding="utf-8"))
        self.assertEqual(list((self.fixture.root / "_ops").iterdir()), [])

    def test_plan_inside_vault_is_rejected(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        inside_plan = self.fixture.root / "ingest_plan.json"
        shutil.copy2(self.fixture.plan, inside_plan)
        result = execute_plan(
            ExecutionOptions(
                root=self.fixture.root,
                plan_path=inside_plan,
                device="B",
                work_root=self.fixture.runtime,
                sync_confirmed=True,
                governance_root=self.fixture.governance,
            )
        )
        self.assertEqual(result["failure"]["code"], "PLAN_INSIDE_VAULT")

    def test_work_root_overlapping_vault_is_rejected(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        result = execute_plan(
            ExecutionOptions(
                root=self.fixture.root,
                plan_path=self.fixture.plan,
                device="B",
                work_root=self.fixture.root / "_ops" / "work",
                sync_confirmed=True,
                governance_root=self.fixture.governance,
            )
        )
        self.assertEqual(result["failure"]["code"], "WORK_ROOT_OVERLAPS_VAULT")

    def test_governance_runtime_inside_vault_is_rejected(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        result = execute_plan(
            ExecutionOptions(
                root=self.fixture.root,
                plan_path=self.fixture.plan,
                device="B",
                work_root=self.fixture.runtime,
                sync_confirmed=True,
                governance_root=self.fixture.root / "_ops",
            )
        )
        self.assertEqual(result["failure"]["code"], "GOVERNANCE_RUNTIME_OVERLAPS_VAULT")

    def test_ai_directory_proposal_is_schema_rejected_with_zero_write(self) -> None:
        operation = self.fixture.create_operation()
        operation["target_directory"] = "wiki/C_操作层/C03_不存在"
        self.fixture.write_plan([operation])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "PLAN_SCHEMA_INVALID")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_ai_zone_id_proposal_is_schema_rejected_with_zero_write(self) -> None:
        operation = self.fixture.create_operation()
        operation["zone_id"] = "C3.999"
        self.fixture.write_plan([operation])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "PLAN_SCHEMA_INVALID")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_missing_canonical_directory_is_rejected(self) -> None:
        operation = self.fixture.create_operation()
        operation["classification"]["number"] = 4
        self.fixture.write_plan([operation])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "DIRECTORY_NOT_FOUND")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_duplicate_physical_directory_fuses_batch(self) -> None:
        (self.fixture.root / "wiki" / "C_操作层" / "C03_流程副本").mkdir()
        self.fixture.write_plan([self.fixture.create_operation()])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "DIRECTORY_AMBIGUOUS")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_create_moc_proposal_is_schema_rejected(self) -> None:
        operation = self.fixture.create_operation()
        operation["create_moc"] = {"title": "New MOC"}
        self.fixture.write_plan([operation])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "PLAN_SCHEMA_INVALID")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_registry_change_after_plan_is_rejected(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        with (self.fixture.root / "_registry.md").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "BASE_STATE_CHANGED")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_zone_id_concurrent_occupation_is_rejected(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])

        def occupy(event: str, details: dict[str, Any]) -> None:
            if event != "after_staging":
                return
            concurrent = EXISTING_CARD.replace("zone_id: C3.1", "zone_id: C3.2").replace("# Existing Card", "# Concurrent Card")
            write_utf8(self.fixture.zone / "Concurrent Card.md", concurrent)
            self.fixture.rebuild_registry()

        result = self.fixture.execute(hook=occupy)
        self.assertEqual(result["failure"]["code"], "ZONE_ID_CONFLICT")
        self.assertTrue((self.fixture.zone / "Concurrent Card.md").is_file())
        self.assertFalse((self.fixture.zone / "New Atomic Card.md").exists())

    def test_missing_parent_moc_is_rejected(self) -> None:
        operation = self.fixture.create_operation()
        operation["parent"] = {
            "moc_title": "Missing MOC",
            "heading": "## Existing Cards",
            "expected_sha256": "0" * 64,
        }
        self.fixture.write_plan([operation])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "MOC_NOT_FOUND")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_no_standard_moc_needs_governance_review(self) -> None:
        review = {
            "op_id": "review-001",
            "action": "needs_governance_review",
            "title": "Unmappable Card",
            "reason": "no_existing_moc",
            "details": "No existing MOC can legally mount this card.",
            "classification": {"zone": "C", "number": 3},
        }
        self.fixture.write_plan([review])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "NEEDS_GOVERNANCE_REVIEW")
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_my_experience_change_is_rejected(self) -> None:
        self.fixture.write_plan([self.fixture.enrich_operation(mutate_my_experience=True)])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "MY_EXPERIENCE_CHANGED")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_enrichment_h1_must_match_target_title(self) -> None:
        operation = self.fixture.enrich_operation()
        operation["body"] = operation["body"].replace("# Existing Card", "# Changed Identity", 1)
        self.fixture.write_plan([operation])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "CARD_H1_MISMATCH")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_create_sources_cannot_be_empty(self) -> None:
        operation = self.fixture.create_operation()
        operation["metadata"]["sources"] = []
        self.fixture.write_plan([operation])
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "PLAN_SCHEMA_INVALID")

    def test_enrichment_sources_patch_cannot_be_empty(self) -> None:
        operation = self.fixture.enrich_operation()
        operation["metadata_patch"]["sources"] = []
        self.fixture.write_plan([operation])
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "PLAN_SCHEMA_INVALID")

    def test_governance_nonzero_exit_blocks_commit(self) -> None:
        write_utf8(self.fixture.governance / "governance_validate.py", "import sys\nprint('validator failed', file=sys.stderr)\nsys.exit(9)\n")
        self.fixture.write_plan([self.fixture.create_operation()])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertEqual(result["failure"]["code"], "STAGING_GOVERNANCE_FAILED")
        self.assertIn("exited with 9", result["failure"]["message"])
        self.assertEqual(tree_hashes(self.fixture.root), before)
        self.assertEqual(list((self.fixture.root / "_ops").iterdir()), [])

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "requires Windows PowerShell")
    def test_pipeline_propagates_rejection_and_emits_no_success(self) -> None:
        operation = self.fixture.create_operation()
        operation["body"] = "# Wrong Title\n\nInvalid.\n"
        self.fixture.write_plan([operation])
        self.fixture.write_profile(device="A")
        before = tree_hashes(self.fixture.root)
        os.environ["PSExecutionPolicyPreference"] = "Bypass"
        pipeline = SCRIPT_DIR / "run_ingest_pipeline.ps1"
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(pipeline),
                "-Plan",
                str(self.fixture.plan),
                "-ProfilePath",
                str(self.fixture.profile),
                "-SyncConfirmed",
                "-SkipIndex",
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        combined = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
        self.assertNotEqual(result.returncode, 0, combined)
        self.assertNotIn("transaction completed", combined)
        self.assertIn("CARD_H1_MISMATCH", combined)
        self.assertEqual(tree_hashes(self.fixture.root), before)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "requires Windows PowerShell")
    def test_pipeline_uses_device_b_profile_successfully(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        self.fixture.write_profile(device="B")
        pipeline = SCRIPT_DIR / "run_ingest_pipeline.ps1"
        result = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-NoProfile",
                "-File",
                str(pipeline),
                "-Plan",
                str(self.fixture.plan),
                "-ProfilePath",
                str(self.fixture.profile),
                "-SyncConfirmed",
                "-SkipIndex",
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        combined = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, combined)
        self.assertIn("transaction completed", combined)
        self.assertEqual(list((self.fixture.root / "_ops").iterdir()), [])

    def test_expired_lock_backup_stays_outside_vault(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        lock = {
            "device": "A",
            "hostname": "old-host",
            "process": "expired:1",
            "acquired_at": "2026-08-01T00:00:00+08:00",
            "expires_at": "2026-08-01T01:00:00+08:00",
            "phase": "INGEST_V1",
            "batch": "expired",
        }
        write_utf8(self.fixture.root / "_ops" / "governance.lock.json", json.dumps(lock))
        result = self.fixture.execute(resume=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(list((self.fixture.root / "_ops").iterdir()), [])
        transaction_dir = Path(result["journal"]).parent
        self.assertEqual(len(list(transaction_dir.glob("expired-governance-lock-*.json"))), 1)

    def test_mid_commit_failure_rolls_back_exactly(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute(failpoints={"commit_after_first"})
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertTrue(result["rollback"]["ok"])
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_registry_rebuild_failure_rolls_back_exactly(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute(failpoints={"registry_rebuild"})
        self.assertEqual(result["failure"]["code"], "REGISTRY_REBUILD_FAILED")
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_gbrain_failure_keeps_markdown_and_marks_index_pending(self) -> None:
        self.fixture.write_plan([self.fixture.create_operation()])
        result = self.fixture.execute(
            skip_index=False,
            index_commands=[[sys.executable, "-c", "import sys; sys.exit(7)"]],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "COMMITTED_INDEX_PENDING")
        self.assertTrue(result["index_pending"])
        self.assertTrue((self.fixture.zone / "New Atomic Card.md").is_file())
        journal = json.loads(Path(result["journal"]).read_text(encoding="utf-8"))
        self.assertTrue(journal["index_pending"])

    def test_enrichment_preserves_my_experience_after_commit(self) -> None:
        original_block = "> [!MY-EXPERIENCE] Leon\n> Keep this byte-for-byte."
        self.fixture.write_plan([self.fixture.enrich_operation()])
        result = self.fixture.execute()
        self.assertTrue(result["ok"], result)
        updated = self.fixture.card.read_text(encoding="utf-8")
        self.assertIn(original_block, updated)
        self.assertIn("Existing material plus a verified increment.", updated)



    def test_read_text_handles_bom_and_encodings(self) -> None:
        from ingest_executor import read_text
        bom_file = self.fixture.base / "bom.txt"
        bom_file.write_bytes(b"\xef\xbb\xbf# Header\nHello UTF-8 BOM\n")
        self.assertEqual(read_text(bom_file), "# Header\nHello UTF-8 BOM\n")

        gbk_file = self.fixture.base / "gbk.txt"
        gbk_file.write_bytes("中文测试内容".encode("gbk"))
        self.assertEqual(read_text(gbk_file), "中文测试内容")

    def test_device_a_and_b_profiles_share_the_same_executor(self) -> None:
        from ingest_executor import load_device_profile

        for device in ("A", "B"):
            profile_path = self.fixture.write_profile(device=device)
            profile = load_device_profile(profile_path)
            self.assertEqual(profile.device, device)
            self.assertEqual(profile.vault_root, self.fixture.root.resolve())

    def test_profile_for_another_hostname_is_rejected(self) -> None:
        from ingest_executor import IngestFailure, load_device_profile

        profile_path = self.fixture.write_profile(device="B", hostname="NOT-THIS-HOST")
        with self.assertRaises(IngestFailure) as context:
            load_device_profile(profile_path)
        self.assertEqual(context.exception.code, "PROFILE_HOST_MISMATCH")

    def test_profile_work_root_containing_vault_is_rejected(self) -> None:
        from ingest_executor import IngestFailure, load_device_profile

        profile_path = self.fixture.write_profile(device="B", work_root=self.fixture.base)
        with self.assertRaises(IngestFailure) as context:
            load_device_profile(profile_path)
        self.assertEqual(context.exception.code, "WORK_ROOT_OVERLAPS_VAULT")

    def test_profile_runtime_root_containing_vault_is_rejected(self) -> None:
        from ingest_executor import IngestFailure, load_device_profile

        fake_python = self.fixture.base / "fake-python.exe"
        fake_python.write_bytes(b"fixture")
        profile_path = self.fixture.write_profile(
            device="B",
            runtime_root=self.fixture.base,
            python_executable=fake_python,
        )
        with self.assertRaises(IngestFailure) as context:
            load_device_profile(profile_path, verify_python=False)
        self.assertEqual(context.exception.code, "RUNTIME_ROOT_OVERLAPS_VAULT")

    def test_snapshot_cli_rejects_parent_work_root_before_vault_write(self) -> None:
        profile_path = self.fixture.write_profile(device="B", work_root=self.fixture.base)
        forbidden_output = self.fixture.root / "snapshot.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "ingest_executor.py"),
                "snapshot",
                "--profile",
                str(profile_path),
                "--material",
                str(self.fixture.material),
                "--out",
                str(forbidden_output),
            ],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["failure"]["code"], "WORK_ROOT_OVERLAPS_VAULT")
        self.assertFalse(forbidden_output.exists())

    def test_title_index_ignores_templates_and_root_process_docs(self) -> None:
        from canonical_directory import build_markdown_title_index

        write_utf8(self.fixture.root / "templates" / "Existing Card.md", "# Template\n")
        write_utf8(self.fixture.root / "PRODUCT.md", "# Existing Card\n")
        index = build_markdown_title_index(self.fixture.root)
        self.assertEqual(index["Existing Card"], [self.fixture.card.resolve()])
        self.assertNotIn("PRODUCT", index)

    def test_chinese_prompt_template_is_a_lossless_anchor(self) -> None:
        from ingest_executor import extract_anchors

        text = "> 提示词模板：你是一名流程分析师。\n> 输入：流程执行日志。\n> 输出：按优先级给出改进清单与理由。\n"
        anchors = extract_anchors(text)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["anchor_type"], "prompt_template")

    def test_snapshot_anchors_separation_validates_cleanly(self) -> None:
        from ingest_executor import snapshot_payload, validate_plan
        material_with_code = self.fixture.base / "material_code.txt"
        write_utf8(
            material_with_code,
            "```python\ndef snapshot_demo():\n    return 42\n```\n",
        )
        snapshot = snapshot_payload(self.fixture.root, [material_with_code], extract_anchors_flag=True)
        self.assertIn("base", snapshot)
        self.assertIn("anchors", snapshot)
        self.assertNotIn("anchors", snapshot["base"])
        self.assertEqual(len(snapshot["anchors"]), 1)
        self.assertEqual(
            set(snapshot["anchors"][0]),
            {"anchor_type", "snippet_hash", "preview"},
            "snapshot must not expose full normalized anchor text into AI context",
        )
        
        # Test that base directly validates against schema without schema failure
        plan_dict = {
            "schema_version": "1.0",
            "plan_id": "snapshot-plan",
            "created_at": "2026-08-07T16:00:00+08:00",
            "base": snapshot["base"],
            "operations": [self.fixture.create_operation()],
        }
        validate_plan(plan_dict)

    def test_lossless_preservation_blocks_unexempted_omitted_anchors(self) -> None:
        from ingest_executor import extract_anchors, read_text
        raw_material = self.fixture.base / "material_anchors.txt"
        write_utf8(
            raw_material,
            "Raw text\n```python\ndef critical_func():\n    return 'MUST_PRESERVE'\n```\n",
        )
        self.fixture.material = raw_material
        
        # Plan creates a card omitting the code block anchor and declaring no omitted_anchors
        op = self.fixture.create_operation()
        op["body"] = "# New Atomic Card\n\n**Summary.**\n\n## Core\n\nOmitted code block entirely.\n"
        self.fixture.write_plan([op])
        
        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "LOSSLESS_PRESERVATION_FAILED")
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_lossless_preservation_validates_omitted_anchor_rationale(self) -> None:
        from ingest_executor import extract_anchors, read_text
        raw_material = self.fixture.base / "material_anchors2.txt"
        code_text = "```python\ndef optional_func():\n    return 'CAN_OMIT'\n```\n"
        write_utf8(raw_material, code_text)
        self.fixture.material = raw_material

        anchors = extract_anchors(read_text(raw_material))
        self.assertEqual(len(anchors), 1)
        snippet_hash = anchors[0]["snippet_hash"]

        # Case A: rationale too short (< 10 chars) -> rejected at Schema level by validate_plan
        op_short = self.fixture.create_operation()
        op_short["body"] = "# New Atomic Card\n\n**Summary.**\n\n## Core\n\nNo code.\n"
        op_short["omitted_anchors"] = [
            {
                "anchor_type": "code_block",
                "snippet_hash": snippet_hash,
                "rationale": "Short", # < 10 chars
            }
        ]
        self.fixture.write_plan([op_short])
        result_short = self.fixture.execute()
        self.assertFalse(result_short["ok"])
        self.assertEqual(result_short["failure"]["code"], "PLAN_SCHEMA_INVALID")

        # Test direct validate_lossless_preservation error for short rationale
        from ingest_executor import validate_lossless_preservation, IngestFailure
        with self.assertRaises(IngestFailure) as ctx:
            validate_lossless_preservation(
                self.fixture.root,
                [],
                {"operations": [op_short]},
                [raw_material],
            )
        self.assertEqual(ctx.exception.code, "OMITTED_ANCHOR_RATIONALE_TOO_SHORT")
        self.assertEqual(ctx.exception.exit_code, 4)

        # Case B: valid rationale (>= 10 chars) -> succeeds
        op_valid = self.fixture.create_operation()
        op_valid["body"] = "# New Atomic Card\n\n**Summary.**\n\n## Core\n\nNo code.\n"
        op_valid["omitted_anchors"] = [
            {
                "anchor_type": "code_block",
                "snippet_hash": snippet_hash,
                "rationale": "This code block is irrelevant boilerplate in tests.",
            }
        ]
        self.fixture.write_plan([op_valid])
        result_valid = self.fixture.execute()
        self.assertTrue(result_valid["ok"], result_valid)
        self.assertEqual(result_valid["lossless_preservation"]["exempted"], 1)

    def test_forged_omitted_anchor_hash_is_rejected(self) -> None:
        raw_material = self.fixture.base / "material_forged.txt"
        write_utf8(
            raw_material,
            "Raw text\n```python\ndef real_func():\n    return 'REAL_CODE'\n```\n",
        )
        self.fixture.material = raw_material

        # Plan keeps the real_func in card body, but attaches a forged omitted_anchor with fake hash
        op = self.fixture.create_operation()
        op["body"] = "# New Atomic Card\n\n**Summary.**\n\n```python\ndef real_func():\n    return 'REAL_CODE'\n```\n"
        op["omitted_anchors"] = [
            {
                "anchor_type": "code_block",
                "snippet_hash": "a" * 64,  # Forged fake hash
                "rationale": "Forged anchor declaration that does not correspond to raw material.",
            }
        ]
        self.fixture.write_plan([op])

        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "OMITTED_ANCHOR_UNKNOWN")
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(tree_hashes(self.fixture.root), before)

    def test_omitted_anchor_type_mismatch_is_rejected(self) -> None:
        from ingest_executor import extract_anchors, read_text
        raw_material = self.fixture.base / "material_type_mismatch.txt"
        code_text = "```python\ndef mismatch_func():\n    return 'TYPE_MISMATCH'\n```\n"
        write_utf8(raw_material, code_text)
        self.fixture.material = raw_material

        anchors = extract_anchors(read_text(raw_material))
        self.assertEqual(len(anchors), 1)
        real_hash = anchors[0]["snippet_hash"]

        # Code block deleted, but omitted_anchor misclassifies anchor_type as "table" instead of "code_block"
        op = self.fixture.create_operation()
        op["body"] = "# New Atomic Card\n\n**Summary.**\n\nNo code here.\n"
        op["omitted_anchors"] = [
            {
                "anchor_type": "table",  # Mismatched! Real type is code_block
                "snippet_hash": real_hash,
                "rationale": "Misclassified anchor type declared for exemption.",
            }
        ]
        self.fixture.write_plan([op])

        before = tree_hashes(self.fixture.root)
        result = self.fixture.execute()
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "OMITTED_ANCHOR_UNKNOWN")
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(tree_hashes(self.fixture.root), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)


