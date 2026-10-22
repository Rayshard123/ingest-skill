# Semantic classification and card-shape guide

Read this **after semantic split**, when classification, branch selection, or card shape needs help. This repository deliberately omits any private, industry-specific branch mapping. Do not infer a branch number from the public examples.

## Source of classification authority

1. First extract stable knowledge units independently of the Wiki structure.
2. Determine the broad Zone by the semantic role below.
3. After the semantic decision is stable, consult the **current local Vault's** relevant `_MOC_*.md` and its navigation structure to locate an *existing* branch number and exact heading. Use `_MOC_主干道.md` only when needed to find that branch.
4. Confirm that the target branch and heading really exist. If the matching branch cannot be established unambiguously from the local Wiki, emit `needs_governance_review` rather than guessing a number or inventing a MOC.

The local Wiki's MOCs can contain private category labels. They are **runtime input**, not bundled project content and must not be copied into this public repository. `_SYSTEM.md` and `_registry.md` may be consulted by deterministic scripts, without loading their full contents into model context merely for plan hashes.

## Zone intent

| Zone | Semantic role |
|---|---|
| A | Principles, mechanisms, mental models: why and how something works |
| C | Repeatable operational procedures and checklists: how to execute |
| D | Operational tactics that entail material policy or misuse risk; use an appropriate `risk_level` |
| F | External policies, standards, legal rules, or authoritative requirements |
| G | Cross-domain tools, reusable thinking models, and broadly applicable knowledge |
| P | Scenario playbooks orchestrating multiple cards and decisions around one complex situation |

Classify the knowledge unit, not the source document; one source may yield multiple Zones. **Letters do not specify numbered branches.** No prefilled A0/C3/F1 mapping is shipped.

## High-value boundary checks

- **Explanation vs execution**: mechanisms and principles belong to A; reusable operational steps belong to C.
- **External authority vs internal implementation**: an external rule itself belongs to F; decisions or workflows derived from it can belong to A or C.
- **Routine vs elevated-risk execution**: standard compliant procedures belong to C; unusually risky or policy-sensitive tactics belong to D.
- **Reusable knowledge vs orchestration**: a detailed single SOP is still a C card; a scenario-specific path coordinating multiple cards belongs to P.
- **Cross-domain vs source-domain**: G is not a catchall for material that merely fails to match an existing branch.
- **Tooling vs governance**: automation methods are classified by their reusable semantic purpose; Ingest must not change the Wiki taxonomy to fit a source.

If a legitimate card has no unambiguous existing MOC/heading, use `needs_governance_review`.

## Metadata semantics

AI proposes only metadata allowed by the plan schema. Executor owns final `zone_id`, physical location, and committed parent representation.

Choose `type` by function: `concept`, `principle`, `sop`, `checklist`, `model`, `case`, or `playbook`. `confidence` reflects evidence quality, not writing confidence. `risk_level` describes operational/policy risk. Keep tags sparse and retrieval-useful.

Sources:

- Vault material: keep the existing `raw/...` source convention.
- External local material: keep the established `codex://...` convention.
- Do not copy external raw material into the synced Vault merely to satisfy `sources`.

## Card shape

Required semantic minimum:

1. `# <semantic title>`
2. a bold one-sentence summary
3. enough structured body to make the knowledge standalone and reusable

Everything else is conditional. Use callouts, tables, formulas, code, examples, checklists, warnings, templates, and related links only when they carry information. Do not create filler `TIP`/`WARNING` sections.

Related-card wikilinks must use resolvable pure titles. Link a concept-dictionary term only when an existing page resolves uniquely; normal Ingest does not create or repair concept pages. For enrichment, preserve `MY-EXPERIENCE` blocks byte-for-byte. For new cards, never create them.

## Playbook-specific shape

A P-zone playbook should normally contain triggering scenario, intended outcome, ordered actions/checklist, material decision branches, and links to the underlying principle/SOP cards it orchestrates. Keep orchestration at the Playbook level; do not duplicate every linked card's full content.

## Conflicting knowledge

When new evidence materially conflicts with an existing claim, preserve the conflict with source/time context and calibrated confidence. Do not silently overwrite one version as fact. Use governance review when resolution requires an authority decision rather than semantic compilation.
