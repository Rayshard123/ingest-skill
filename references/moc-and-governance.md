# Existing-MOC semantic mounting contract

Read this only after semantic split/classification when the correct parent MOC or heading is uncertain.

MOC is a **placement dependency**, not an understanding dependency. Do not read the MOC tree before extracting and stabilizing knowledge units.

Prefer the narrowest available lookup: query the relevant branch/target MOC first. Read `_MOC_主干道.md` only when you need its navigation map to locate an existing branch; do not load unrelated MOCs.

Ingest may mount a card only under an already existing MOC and, for `create`, an already existing exact heading. AI may select that semantic destination; it may not create, rename, split, move, or repair MOCs/headings during Ingest.

Choose the MOC that best represents where a future user would look for the card. Do not choose a merely convenient MOC to avoid review, and do not change a sound semantic split to fit navigation structure.

If no unique suitable existing destination exists, emit `needs_governance_review`.

The executor independently verifies MOC identity, hash, location, heading existence, links, staging governance, and capacity. Those checks do not belong in AI reasoning.
