# Retrieval and extraction conventions

Read this when you need to retrieve candidate cards for dedup, extract text from office documents, or interpret the raw-material directory layout.

## Candidate recall

Use `gbrain query` for semantic+keyword hybrid retrieval. The Vault is indexed by the executor's `gbrain import` + `gbrain embed --stale` after every successful commit, so the index is always current.

For each semantic unit, issue **multiple queries from different angles** (title keywords, core concept, synonyms, related procedures) to maximise recall. A single query rarely surfaces all relevant candidates.

```bash
gbrain query "your search phrase" --detail low   # ranked chunks
gbrain search "exact keyword"                     # FTS fallback
gbrain get <slug>                                 # full page when a chunk confirms relevance
```

Do not rely on file-system grep or filename scanning for candidate recall — literal matching misses semantically related cards with different wording.

## File extraction

Use `officecli` to extract text from office documents:

```bash
officecli read "<absolute-path>"    # .docx / .pptx / .xlsx
```

For PDF / image / scan, follow the multimodal gate (`references/multimodal-gate.md`).

## Raw-material directory layout

- Files in the `done/` subdirectory (relative to the raw-material root) are **already processed** — skip them unless the user explicitly asks to redo.
- Files outside `done/` are pending material for the current ingest session.
