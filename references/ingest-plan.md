# `ingest_plan.json` AI authoring contract (R3; schema 1.0)

Read this only after semantic split, dedup decisions, classification, parent-MOC selection, and card drafting are complete. The plan is a serialization of those decisions, not a place to perform governance reasoning.

`ingest-plan.schema.json` is the machine authority. Unknown fields are rejected.

## Never author deterministic authority

Do not put final `zone_id`, physical directory/path, MOC/Zone creation, shell commands, validation bypasses, commit instructions, lock instructions, Registry instructions, or rollback instructions in the plan.

## Base snapshot

Run `snapshot --anchors` immediately before serialization and copy its `base` object unchanged. The compact `anchors` array is an AI-facing manifest only; hashes come from the snapshot tool, never from manual calculation. Snapshot hashes `_SYSTEM.md` and `_registry.md` machine-side; **do not open either file merely to populate or verify those hash fields**.

## `create`

Provide only the semantic proposal:

- `classification`: `zone`, `number`, optional rationale;
- semantic `title`;
- existing `parent.moc_title`, existing exact `heading`, and current MOC hash;
- permitted metadata;
- complete body without Frontmatter;
- dedup candidates with current hashes and semantic relation judgments;
- `omitted_anchors` only for snapshot anchors intentionally excluded as non-knowledge boilerplate.

The first H1 must be `# <title>`, and `metadata.sources` must contain at least one real source. The executor derives filename, directory, parent metadata, and final ID.

## `enrich`

Provide the existing target title/hash, its existing parent MOC title/hash, a limited metadata patch, the complete proposed body, and dedup evidence. Include the target as a hash-pinned `same_source` candidate.

The first H1 must remain `# <target.title>`. Preserve all existing `MY-EXPERIENCE` blocks byte-for-byte. Do not treat enrichment as append-only; integrate the increment into a coherent full card. If `metadata_patch.sources` is present, it must not be empty.

## `needs_governance_review`

Use only when semantic classification, dedup identity, or a valid existing MOC cannot be resolved without inventing structure or silently choosing between conflicting interpretations.

## Anchors

If a snapshot anchor is intentionally omitted, copy its `anchor_type` and `snippet_hash` exactly and give a concrete rationale. Do not omit an anchor merely to shorten the card; omission is for content that is truly irrelevant to the durable knowledge unit.

## Snapshot and validation commands

Capture the base/anchor manifest after card drafting is stable and immediately before plan serialization:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>/scripts/new_ingest_snapshot.ps1" `
  -Material "<absolute_raw_material_path>" `
  -Out "<profile_work_root>/snapshot.json" `
  -Anchors
```

For multiple raw materials, pass an array to `-Material`. The wrapper writes UTF-8 directly; do not use PowerShell 5.1 `>` redirection. Write `ingest_plan.json` under the same profile WorkRoot, then validate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill_dir>/scripts/validate_ingest_plan.ps1" `
  -Plan "<plan_path>"
```

Schema rejection means the proposal must be corrected; do not hide rejected authority inside free-text fields.

## Shell safety (headless-pipeline-critical)

All `powershell` invocations anywhere in the Ingest session MUST follow these rules. Violations cause the headless pipeline to hang waiting for interactive keyboard input and can block the batch runner for up to 45 minutes.

**Prohibited — variable-expansion hang:**
```powershell
# WRONG: outer shell expands $workRoot → empty string before inner powershell receives the command
powershell -Command "powershell -NoProfile ... -Command \"$workRoot = 'D:\...' ...\"
```

**Required — use `-File` for script calls (canonical form, always preferred):**
```powershell
powershell -NonInteractive -NoProfile -ExecutionPolicy Bypass -File "<absolute_script_path>" `
  -Param1 "value1" -Param2 "value2"
```

**Allowed — inline commands with single-quoted literals only:**
```powershell
# Single quotes prevent $ interpolation by the outer shell
powershell -NonInteractive -NoProfile -ExecutionPolicy Bypass `
  -Command 'Test-Path -LiteralPath ''D:\exact\literal\path'''
```

**Required on every invocation:** `-NonInteractive` — ensures any cmdlet that is missing a mandatory argument throws a terminating error immediately instead of blocking for keyboard input.

