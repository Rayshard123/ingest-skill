---
name: ingest
description: Load when the user asks to ingest or compile raw material into Leon's Wiki, or to redo an Ingest because semantic splitting, dedup, enrich/create judgment, or card quality was poor. Do not load for standalone Wiki governance, taxonomy/MOC maintenance, retrieval, or executor debugging.
---

# Ingest

## Goal

Turn raw material into durable, reusable Wiki cards. Spend model reasoning on **understanding, knowledge extraction, semantic split, dedup, enrich/create judgment, and high-quality card compilation**.

The AI writes an untrusted plan under the profile WorkRoot; scripts/schema own transaction safety. Do not reproduce transaction internals in semantic reasoning.

## Runtime context boundary

Start from **this Skill + the Raw Material**. Do **not** preload `_SYSTEM.md`, `_registry.md`, `_MOC_主干道.md`, all MOCs, or all references; scripts may hash global files without putting their text into model context. Stabilize semantic units first, retrieve relevant candidate cards rather than the full Registry, and read only the MOC needed for classification or placement.

## Semantic contract

Understand the material before fitting it into the Wiki. Recover the claims, mechanisms, procedures, decisions, assumptions, constraints, exceptions, evidence, examples, formulas, prompts, code, tables, and other assets that change how the knowledge should be applied.

Split by **independent reusable knowledge identity**. A card should answer one durable question or support one reusable decision/action while retaining the context needed to apply it correctly. Atomic does not mean context-poor: keep causal chains, decision frameworks, worked examples, and procedures together when separating them would damage meaning.

Retrieve candidates **per semantic unit**, then compare candidate bodies. Similarity scores are retrieval hints only. Enrich when the new material improves the same knowledge/SOP/model lineage; create when the reusable knowledge identity is distinct. If identity cannot be resolved without guessing, use `needs_governance_review`.

Only after the knowledge unit is stable, classify it and mount it under an existing semantically valid MOC/heading. Navigation must adapt to the knowledge decision, not drive the semantic split. If no valid existing destination exists, use governance review rather than weakening the card or inventing structure.

Compile for future retrieval and use, not as a source summary. Put reusable knowledge early; preserve conditions, thresholds, causal logic, steps, evidence, examples, exceptions, and uncertainty that affect application. Use sections, warnings, tables, checklists, formulas, code, templates, and related links only when they carry information.

For `enrich`, rewrite the complete proposed body into one coherent card. Preserve every existing `MY-EXPERIENCE` block byte-for-byte. For `create`, never author a `MY-EXPERIENCE` block.

## Hard boundaries

- Write governed files only through the deterministic executor.
- Keep every process artifact outside the Vault. The sole transient exception is exact path `_ops/governance.lock.json`; historical `_ops/` content is not an Ingest dependency or permission to add artifacts.
- Ingest may select existing Wiki structure; it may not create, rename, split, move, or repair taxonomy/MOCs/headings.
- Do not manufacture deterministic transaction authority. Propose semantic content and source-grounded decisions; let scripts/schema derive and verify machine state.
- Preserve knowledge-bearing static assets from the raw material. Omit one only when it is genuinely irrelevant boilerplate and the plan records the exact snapshot identity plus a concrete rationale.
- Treat executor/schema rejection as failure. Do not encode rejected authority in free text or alternate fields.
- Headless PowerShell safety: never run inline `powershell -Command` with double quotes containing `$` variables (outer shell expansion strips them). Call `.ps1` scripts via `-File`, use `view_file` to read files, and always specify `-NonInteractive`.

## Progressive disclosure

Load only what the current decision requires:

| Need | Read |
|---|---|
| PDF/image/slide/scan or layout-dependent meaning | `references/multimodal-gate.md` |
| classification/card-shape ambiguity or runtime Zone branch discovery | `references/zoning-and-frontmatter.md`; then inspect only the relevant local MOC needed to resolve an existing numbered branch/heading |
| candidate identity remains unclear after body comparison | `references/dedup-rules.md` |
| parent MOC/heading is semantically uncertain | `references/moc-and-governance.md` + relevant MOC only |
| snapshot reports anchors and you may omit or re-represent one | `references/lossless-preservation.md` |
| semantic decisions and card bodies are finished; serialize the plan | `references/ingest-plan.md` |
| candidate recall tool, office-file extraction, or raw-material directory conventions | `references/local-environment.md` |
| recovery, expired transaction, missing device profile, or write-precondition uncertainty | `references/device-lock.md` |

Do not preload all references. Do not read `ingest-plan.schema.json` for semantic work; it is machine authority for validation.

## Gotchas

- **Similarity-score bias:** a high embedding score can still be a different decision, stage, assumption, or SOP. Dedup is a body-level identity judgment.
- **Thin-card bias:** aggressive atomicity can strip prerequisites, causal links, exceptions, and examples until the card becomes unusable.
- **Append-only enrich:** pasting new material onto an old card creates chronology, not integrated knowledge. Recompile the whole proposed body.
- **Template compliance theater:** mandatory `TIP`, `WARNING`, checklist, or fixed card counts create filler. Structure follows information value.
- **Governance avoidance:** never merge, compress, or misclassify sound knowledge merely because no existing MOC can mount it.
- **Lossless overreach:** executor protection for code/tables/prompts is a safety net, not a reason to create cards around anchors. For knowledge-bearing anchors, derived representations may be added but do not replace the preserved original.
- **Visual-text collapse:** when meaning lives in a diagram, screenshot, table layout, or chart, extract that meaning before semantic split.

## Plan and execution

After semantic/card decisions are complete, read `references/ingest-plan.md`, use `scripts/new_ingest_snapshot.ps1`, serialize under the profile WorkRoot, and validate. Copy machine-produced hashes unchanged; do not open global files just to inspect hashes or use PowerShell 5.1 redirection for JSON.

At handoff, read `references/device-lock.md`, verify its preconditions, and invoke `scripts/run_ingest_pipeline.ps1` once. If the profile is missing, run `scripts/setup-device.ps1` with user-supplied local device and absolute paths; never invent or copy another host's profile. The wrapper/executor owns the full transaction.

Report the knowledge result first: created/enriched/review-blocked cards, split rationale, dedup conclusions, chosen existing destination, material omissions requiring review, and executor outcome. Never report success after a nonzero pipeline result.
