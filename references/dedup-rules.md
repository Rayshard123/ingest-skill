# Dedup semantic tie-breakers

Read this only when candidate identity is genuinely unclear after body comparison.

The dedup question is: **does the candidate represent the same reusable knowledge identity, or merely a related topic?** Search similarity alone cannot answer it.

| Comparison | Decision |
|---|---|
| Same procedure/model/decision logic, new material adds compatible detail | `enrich` |
| Same broad topic, but different stage, scenario, assumptions, action, or conclusion | `create` |
| Shared vocabulary with no meaningful knowledge overlap | `create` + `false_positive` |
| Competing versions where merge would silently choose truth | `needs_governance_review` |
| Evidence is insufficient to determine identity | `needs_governance_review` |

For enrichment, the existing target must be included as a hash-pinned `same_source` candidate. Here `same_source` means the same knowledge/SOP/model lineage expected by the executor; it is not a license to merge cards merely because they cite the same raw source.

Prefer a coherent enrichment when the new content improves the same card's decision value. Prefer a separate card when merging would create multiple independent questions or make retrieval less precise.

In delivery, explain the body-level conclusion. Scores may be reported as supporting evidence but never as the reason by themselves.
