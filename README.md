# Ingest Skill (public edition)

A source-to-Wiki knowledge compilation Skill with an **untrusted AI proposal** and a **deterministic Python transaction executor**. The model handles understanding, semantic splitting, candidate-level deduplication, `create` / `enrich` decisions, and card authoring. The executor validates a versioned JSON plan and owns the lock, staging, governance checks, commit, rollback, and index refresh.

**Public-release boundary:** This repository includes no production Vault, raw business materials, internal category-to-branch mapping, card corpus, credentials, or local profile. `Leon` is an example Wiki name used in the original Skill; it is not a requirement for the executor.

## What is included

| Path | Purpose |
|---|---|
| `SKILL.md` | Agent-facing semantic and safety instructions; progressive disclosure of references |
| `references/` | Plan authoring, JSON Schema, dedup, lossless anchors, placement and device/sync guidance |
| `scripts/ingest_executor.py` | Deterministic plan validator and transaction engine |
| `scripts/canonical_directory.py` | Resolve existing Zone directories and IDs against the Registry |
| `scripts/*.ps1` | Windows setup, snapshot, validation and transaction wrappers |
| `runtime/governance/` | Bundled Wiki governance checks, Frontmatter validation and Registry rebuild |
| `runtime/requirements.lock` | Pinned Python package versions |
| `scripts/test_ingest_v1.py`, `evals/` | Isolated integration regression suite and semantic/routing evaluations |

## Compatibility / prerequisites

This is a **specialized implementation for an existing governed Wiki**, not a general Markdown importer. Its executor assumes the existing `wiki/` Zone roots `A_原理层`, `C_操作层`, `D_策略沙盒`, `F_政策法规`, `G_通用知识树`, `P_场景层`; `_SYSTEM.md`, `_registry.md`, a root `_MOC_*.md` and an `_ops` directory. Before using it against another Wiki, adapt the canonical directory and governance contracts deliberately and rerun the tests.

- Windows PowerShell 5.1 for the bundled `.ps1` entry points.
- Python **3.11+** and the packages in `runtime/requirements.lock`.
- A local Wiki with the above structure and existing MOC headings; do not point at a production Vault until setup and isolated testing are complete.
- Optional external tools: `gbrain` for candidate recall and post-commit indexing; `officecli` for Office-document extraction. Indexing can be deferred with `-SkipIndex`, but that leaves search freshness pending and **does not bypass governance**.
- For a two-device synced Vault: coordinate exclusive write access and confirm sync status explicitly. A host-bound profile is required on each device and **must live outside** the Vault and the public repository.

## Quick start (Windows PowerShell)

1. Place this Skill under your agent's local Skills directory; keep the supplied folder structure intact.
2. Confirm your existing Wiki matches the prerequisites above and make a backup of the Vault.
3. On each authorized device, run one-time setup with **your own paths** (example values only):

```powershell
powershell -NonInteractive -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>\scripts\setup-device.ps1" `
  -Device A `
  -VaultRoot "D:\wiki-vault" `
  -WorkRoot "D:\ingest-work"
```

The setup creates a local Python environment, installs pinned packages, and saves the hostname-bound profile under `%LOCALAPPDATA%\LeonWiki\` by default. Check `scripts/setup-device.ps1` for override parameters.

4. Ingest follows this order: understand source → split knowledge → retrieve/compare existing cards → decide `create` / `enrich` / `needs_governance_review` → classify against **existing local MOCs** → compile cards → snapshot/serialize/validate → execute.
5. For one material, after drafting is complete:

```powershell
powershell -NonInteractive -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>\scripts\new_ingest_snapshot.ps1" `
  -Material "D:\incoming\source.md" `
  -Out "D:\ingest-work\snapshot.json" `
  -Anchors
```

Create `D:\ingest-work\ingest_plan.json` to match `references/ingest-plan.md` and the authoritative `references/ingest-plan.schema.json`; do **not** invent final IDs, paths or hashes. Then:

```powershell
powershell -NonInteractive -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>\scripts\validate_ingest_plan.ps1" `
  -Plan "D:\ingest-work\ingest_plan.json"

# After confirming local device ownership, absence of conflicting writes and completed sync:
powershell -NonInteractive -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>\scripts\run_ingest_pipeline.ps1" `
  -Plan "D:\ingest-work\ingest_plan.json" `
  -SyncConfirmed
```

`-SyncConfirmed` is an explicit assertion by the operator, **not** an automated check that syncing is complete. Do not pass it unless the checks in `references/device-lock.md` hold. The executor can create only the transient `_ops/governance.lock.json` inside the Vault as process state; all other transaction artifacts belong under the non-synced WorkRoot.

## Local taxonomy and privacy boundary

The public `references/zoning-and-frontmatter.md` documents broad role semantics (A / C / D / F / G / P), **not private numbered branch meanings**. Those are resolved from the installed Wiki's current MOCs only **after** extracting stable knowledge units. No branch should be guessed or silently created. If no valid existing placement is found, return `needs_governance_review`.

When publishing forks or bug reports, never copy private `_MOC_*.md` content, `_SYSTEM.md`, `_registry.md`, real cards, source materials, host-bound profiles, snapshots, plans, journals, indexes or logs into this repository. Sanitized fixtures are supplied in `scripts/test_ingest_v1.py`.

## Tests

The integration suite builds temporary synthetic Wiki fixtures, exercises schema and write guards, commit, rollback, recovery, lossless preservation and path boundaries. Set `INGEST_V1_TEST_ROOT` to an existing **non-synced directory outside the production Vault**:

```powershell
New-Item -ItemType Directory -Force "D:\ingest-test-root" | Out-Null
$env:INGEST_V1_TEST_ROOT = "D:\ingest-test-root"
python -B -m unittest discover -s scripts -p "test_ingest_v1.py" -v
```

The Python integration suite can also be run on platforms without PowerShell; PowerShell wrappers and device sync behavior require separate Windows validation. Two PowerShell wrapper integration tests are skipped automatically on non-Windows hosts. Do not interpret those skips as a Windows validation pass.

## Failure and safety model

The AI-generated plan is untrusted. Plan Schema and governed filesystem state remain authoritative. Write attempts fail closed on missing or ambiguous MOCs/directories, conflicting IDs/hashes, invalid metadata, changed source anchors, or failed governance. For a rejected plan, resolve the reported failure; do not bypass it in prose. For interrupted transactions, follow `references/device-lock.md` and inspect the external journal. Indexing failures may leave a committed transaction in `COMMITTED_INDEX_PENDING`; do not mistake that for a failed rollback or a fully fresh search index.

## Publication and licensing

The repository deliberately omits private category mappings and removes compiled `.pyc` cache files. `.gitignore` excludes common local state; **a gitignore is not a security boundary**. Inspect `git status`, staged diffs, and the complete Git history before pushing. This project is released under the [MIT License](LICENSE), copyright (c) 2026 Leon. The license applies to original code and documentation in this repository; confirm rights to any third-party material before redistributing it. Dependencies retain their own respective licenses.
