# Multimodal extraction guide

Read this only for PDF, image, slide, scan, or material whose meaning depends on visual layout.

## Goal

Recover the knowledge-bearing content before semantic split. Visual extraction exists to understand the material, not to create a second governance workflow.

## Extraction order

1. Prefer native text extraction for text-bearing PDF/DOCX/PPTX.
2. Use visual inspection for screenshots, charts, diagrams, scanned pages, layout-dependent tables, or places where native extraction is incomplete/garbled.
3. Use OCR only as a last resort when native extraction and direct visual reading cannot reliably recover necessary text in any language, including Chinese and mixed-language layouts.
4. Keep temporary render/extraction artifacts under the local non-synced work root, never inside the Vault.

Inspect enough resolution to recover labels, values, UI settings, arrows, legends, and relationships that affect meaning. For long images/pages, use overlapping crops only when needed for legibility.

## Knowledge preservation

Do not reduce visually encoded evidence to a vague caption. Preserve meaningful:

- operational parameters and UI settings;
- tables and quantitative values;
- process/architecture relationships;
- trends and comparison results;
- reusable prompts/scripts/templates visible in screenshots.

Convert visually recovered meaning into the representation most useful for the card: prose, Markdown table, Mermaid, or code block. The current transaction does not manage binary assets, so do not copy or embed a new image into the Vault. If the visual itself carries irreducible information, preserve its source reference and record the limitation for review rather than creating an unmanaged asset.

For **text-native** code blocks, Markdown tables, and prompt/templates that the snapshot identifies as lossless anchors, conversion is additive rather than destructive: retain the original anchor in at least one proposed card body, then add a derived prose/Mermaid/reformatted representation only if it improves usability. Do not use `omitted_anchors` merely to replace an anchor with another representation.

## Large documents

Split by semantic independence using the same rules as the main Skill. Page count and word count are signals to inspect structure, not fixed card-count rules. Avoid both one giant catch-all card and arbitrary 3–5-card fragmentation.

If a sound semantic split exposes a missing navigation structure, use `needs_governance_review` instead of compressing the material to fit an existing MOC.
