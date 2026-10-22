# Lossless preservation: semantic exception guide

Read this only when the snapshot reports anchors, or when you are deciding whether a preserved code block, table, or prompt/template may be represented differently in the card.

Preserve an anchor when it carries executable logic, data, formulas, reusable prompts/templates, worked structures, or evidence whose meaning would be damaged by paraphrase. Place it inside the card where it supports the surrounding knowledge; an anchor alone is not a reason to create a card.

**Representation changes are additive, not substitutive.** If a preserved text-native anchor would be clearer as prose, Mermaid, a reformatted table, or another derived representation, keep the original anchor in at least one proposed card body and add the derived representation alongside it. A transformed representation does not count as preserving the original anchor for the deterministic gate.

Use `omitted_anchors` only for genuine omission, never merely because the same content was reformatted or transformed. Omit only genuine boilerplate or content irrelevant to every durable knowledge unit. For each omission, copy the exact `anchor_type` and `snippet_hash` from the snapshot and give a concrete semantic rationale. Never invent or calculate hashes.

The executor enforces normalized anchor preservation deterministically. The AI decides only **preserve vs. genuinely omit, where the preserved asset belongs, and whether an additional derived representation improves the card**.
