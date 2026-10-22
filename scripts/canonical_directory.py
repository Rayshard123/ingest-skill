from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ZONE_ROOTS = {
    "A": "A_原理层",
    "C": "C_操作层",
    "D": "D_策略沙盒",
    "F": "F_政策法规",
    "G": "G_通用知识树",
    "P": "P_场景层",
}

ZONE_ID_RE = re.compile(r"^([ACDFGP])(\d+)\.(\d+)$", re.IGNORECASE)


class CanonicalResolutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: object | None = None):
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class RegistryEntry:
    zone_id: str
    zone: str
    zone_number: int
    card_number: int
    path: str


def _inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CanonicalResolutionError(
            "PATH_ESCAPE",
            f"resolved path escapes the Vault: {path}",
        ) from exc
    return path


def parse_registry(registry_path: Path) -> list[RegistryEntry]:
    if not registry_path.is_file():
        raise CanonicalResolutionError("REGISTRY_MISSING", f"Registry is missing: {registry_path}")

    entries: list[RegistryEntry] = []
    by_id: dict[str, list[str]] = {}
    by_path: dict[str, list[str]] = {}
    text = registry_path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 7:
            continue
        zone_id = cells[1]
        match = ZONE_ID_RE.fullmatch(zone_id)
        if not match:
            continue
        relative = cells[5].replace("\\", "/")
        if not relative.startswith("wiki/") or not relative.endswith(".md"):
            raise CanonicalResolutionError(
                "REGISTRY_PATH_INVALID",
                f"Registry line {line_number} has an invalid card path: {relative!r}",
            )
        normalized_id = f"{match.group(1).upper()}{int(match.group(2))}.{int(match.group(3))}"
        entry = RegistryEntry(
            zone_id=normalized_id,
            zone=match.group(1).upper(),
            zone_number=int(match.group(2)),
            card_number=int(match.group(3)),
            path=relative,
        )
        entries.append(entry)
        by_id.setdefault(zone_id.upper(), []).append(relative)
        by_path.setdefault(relative.casefold(), []).append(normalized_id)

    duplicate_ids = {key: value for key, value in by_id.items() if len(value) > 1}
    duplicate_paths = {key: value for key, value in by_path.items() if len(value) > 1}
    if duplicate_ids or duplicate_paths:
        raise CanonicalResolutionError(
            "REGISTRY_DUPLICATE",
            "Registry contains duplicate zone_id or path entries",
            details={"zone_ids": duplicate_ids, "paths": duplicate_paths},
        )
    return entries


def resolve_canonical_directory(
    root: Path,
    zone: str,
    zone_number: int,
    entries: list[RegistryEntry],
) -> Path:
    root = root.resolve()
    zone = zone.upper()
    if zone not in ZONE_ROOTS:
        raise CanonicalResolutionError("ZONE_INVALID", f"unsupported zone: {zone!r}")
    if not isinstance(zone_number, int) or isinstance(zone_number, bool) or zone_number < 0:
        raise CanonicalResolutionError("ZONE_NUMBER_INVALID", f"invalid zone number: {zone_number!r}")

    zone_root = _inside(root, root / "wiki" / ZONE_ROOTS[zone])
    if not zone_root.is_dir():
        raise CanonicalResolutionError(
            "ZONE_ROOT_MISSING",
            f"canonical zone root is missing: {zone_root}",
        )

    if zone == "P":
        if zone_number != 1:
            raise CanonicalResolutionError(
                "DIRECTORY_NOT_FOUND",
                f"P zone only has the canonical P1 physical directory: {zone_root}",
            )
        candidate = zone_root
    else:
        prefix = f"{zone}{zone_number:02d}_"
        candidates = sorted(
            path for path in zone_root.iterdir()
            if path.is_dir() and path.name.startswith(prefix)
        )
        if not candidates:
            raise CanonicalResolutionError(
                "DIRECTORY_NOT_FOUND",
                f"no canonical directory exists for {zone}{zone_number}",
                details={"expected_prefix": prefix, "zone_root": str(zone_root)},
            )
        if len(candidates) != 1:
            raise CanonicalResolutionError(
                "DIRECTORY_AMBIGUOUS",
                f"multiple physical directories match {zone}{zone_number}",
                details=[str(path) for path in candidates],
            )
        candidate = _inside(root, candidates[0])

    inconsistent: list[str] = []
    normalized_ids: dict[str, list[str]] = {}
    for entry in entries:
        if entry.zone != zone or entry.zone_number != zone_number:
            continue
        normalized_ids.setdefault(entry.zone_id, []).append(entry.path)
        registered = _inside(root, root / Path(entry.path))
        try:
            registered.relative_to(candidate)
        except ValueError:
            inconsistent.append(entry.path)
    if inconsistent:
        raise CanonicalResolutionError(
            "REGISTRY_DIRECTORY_MISMATCH",
            f"Registry paths for {zone}{zone_number} do not agree with the canonical directory",
            details={"canonical": str(candidate), "paths": inconsistent},
        )
    semantic_duplicates = {key: paths for key, paths in normalized_ids.items() if len(paths) > 1}
    if semantic_duplicates:
        raise CanonicalResolutionError(
            "REGISTRY_ZONE_ID_AMBIGUOUS",
            f"target Zone {zone}{zone_number} contains semantically duplicate zone_id values",
            details=semantic_duplicates,
        )
    return candidate


def build_markdown_title_index(root: Path) -> dict[str, list[Path]]:
    root = root.resolve()
    index: dict[str, list[Path]] = {}
    candidates: list[Path] = []
    root_moc = root / "_MOC_主干道.md"
    if root_moc.is_file():
        candidates.append(root_moc)
    wiki_root = root / "wiki"
    if wiki_root.is_dir():
        candidates.extend(wiki_root.rglob("*.md"))
    for path in candidates:
        index.setdefault(path.stem, []).append(_inside(root, path))
    return index


def resolve_unique_title(index: dict[str, list[Path]], title: str, *, kind: str) -> Path:
    matches = index.get(title, [])
    if not matches:
        raise CanonicalResolutionError(
            f"{kind.upper()}_NOT_FOUND",
            f"{kind} does not exist: {title!r}",
        )
    if len(matches) != 1:
        raise CanonicalResolutionError(
            f"{kind.upper()}_AMBIGUOUS",
            f"{kind} title resolves to multiple files: {title!r}",
            details=[str(path) for path in matches],
        )
    return matches[0]


def wikilink_target(value: object) -> str | None:
    match = re.search(r"\[\[([^\]|#]+)", str(value))
    return match.group(1).strip() if match else None
