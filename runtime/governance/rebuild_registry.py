from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")

# New authoritative YAML parser (P2-16)
from frontmatter import (
    parse_frontmatter_yaml,
    ZONE_ID_PATTERN,
    ZONE_DIR_PATTERN,
    CARD_TYPES as FM_CARD_TYPES,
)


ZONE_DIR = re.compile(r"^([A-Z])_", re.I)
ZONE_ID = re.compile(r"^([A-Z])(\d+)\.(\d+)$", re.I)
ZONE_ORDER = {letter: index for index, letter in enumerate("ABCDEFGP")}
CARD_TYPES = {"concept", "principle", "sop", "checklist", "model", "case", "playbook"}
DOCUMENT_TYPES = {"card", "moc", "other", "ambiguous"}


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def parse_frontmatter(text: str) -> dict[str, object]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((i for i, line in enumerate(lines[1:200], 1) if line.strip() == "---"), None)
    if end is None:
        return {}

    frontmatter: dict[str, object] = {}
    current: str | None = None
    for raw in lines[1:end]:
        if re.match(r"^\s+-\s+", raw) and current:
            value = re.sub(r"^\s+-\s+", "", raw).strip().strip("\"'")
            if not isinstance(frontmatter.get(current), list):
                frontmatter[current] = []
            frontmatter[current].append(value)
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", raw)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        current = key
        if value.startswith("[") and value.endswith("]"):
            frontmatter[key] = [
                item.strip().strip("\"'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        elif value.lower() in {"null", "~"}:
            frontmatter[key] = ""
        else:
            frontmatter[key] = value.strip().strip("\"'")
    return frontmatter


def recover_frontmatter(text: str) -> tuple[dict[str, object], int | None]:
    """Return the first YAML-like block and its zero-based start line.

    This is classification evidence only. A block found after line 0 is not valid
    Frontmatter and must still fail the appropriate document guard.
    """
    lines = text.splitlines()
    for start, line in enumerate(lines[:200]):
        if line.strip() != "---":
            continue
        candidate = "\n".join(lines[start:])
        parsed = parse_frontmatter(candidate)
        if parsed:
            return parsed, start
    return {}, None


def classify_document(
    path: Path,
    root: Path | None = None,
    *,
    text: str | None = None,
    frontmatter: dict[str, object] | None = None,
) -> dict[str, object]:
    """Classify a Markdown document before applying a document-specific guard.

    The returned evidence is intentionally mechanical. Conflicting explicit Card
    and MOC metadata yields ``ambiguous`` and must never fall through to Card Guard.
    """
    root = root.resolve() if root else None
    path = path.resolve() if path.is_absolute() else path
    if text is None and path.is_file():
        text = read_text(path)
    text = text or ""
    strict = frontmatter if frontmatter is not None else parse_frontmatter(text)
    recovered, recovered_at = recover_frontmatter(text)
    metadata = strict or recovered

    tags = metadata.get("tags", [])
    tag_values = tags if isinstance(tags, list) else [str(tags)] if tags else []
    lowered_tags = {str(tag).strip().lower() for tag in tag_values}
    declared_type = str(metadata.get("type", "")).strip().lower()
    zone_id = str(metadata.get("zone_id", "")).strip()

    moc_evidence: list[str] = []
    if declared_type == "moc":
        moc_evidence.append("frontmatter.type=moc")
    if "moc" in lowered_tags:
        moc_evidence.append("frontmatter.tags contains MOC")
    if path.stem == path.parent.name:
        moc_evidence.append("filename matches parent directory")
    if path.stem.startswith("_MOC_"):
        moc_evidence.append("root MOC filename")

    card_evidence: list[str] = []
    if declared_type in CARD_TYPES:
        card_evidence.append(f"frontmatter.type={declared_type}")
    if ZONE_ID.fullmatch(zone_id):
        card_evidence.append(f"frontmatter.zone_id={zone_id}")

    relative: Path | None = None
    if root:
        try:
            relative = path.resolve().relative_to(root)
        except (ValueError, OSError):
            relative = None
    elif not path.is_absolute():
        relative = path
    in_card_zone = bool(
        relative
        and len(relative.parts) >= 3
        and relative.parts[0] == "wiki"
        and ZONE_DIR.match(relative.parts[1])
    )
    if in_card_zone:
        card_evidence.append("path is inside a governed Card zone")

    explicit_card = declared_type in CARD_TYPES or bool(ZONE_ID.fullmatch(zone_id))
    if moc_evidence and explicit_card:
        document_type = "ambiguous"
    elif moc_evidence:
        document_type = "moc"
    elif card_evidence and in_card_zone:
        document_type = "card"
    elif declared_type or zone_id:
        document_type = "ambiguous"
    else:
        document_type = "other"
    assert document_type in DOCUMENT_TYPES
    return {
        "document_type": document_type,
        "identity": "passed" if document_type in {"card", "moc", "other"} else "review_required",
        "evidence": {"moc": moc_evidence, "card": card_evidence},
        "frontmatter_at_file_start": bool(strict),
        "recovered_frontmatter_start_line": recovered_at + 1 if recovered_at is not None else None,
    }



def registry_frontmatter(root: Path) -> list[str]:
    actual = root / "_registry.md"
    if actual.is_file():
        text = read_text(actual)
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            end = next((i for i, line in enumerate(lines[1:200], 1) if line.strip() == "---"), None)
            if end is not None:
                return ["---", *lines[1:end], "---"]
    return ["---", "tags: [system, registry]", "---"]

def is_moc(path: Path, frontmatter: dict[str, object]) -> bool:
    return classify_document(path, frontmatter=frontmatter)["document_type"] == "moc"


def section_label(path: Path, zone_id: str) -> str:
    m = ZONE_ID.match(zone_id)
    zone = m.group(1).upper() if m else zone_id[0].upper()
    return f"{zone} — {path.parent.name}"


def clean_cell(value: str) -> str:
    return value.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild _registry.md from card frontmatter")
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--audit-classification", action="store_true",
                        help="Run old vs new classifier in parallel and report differences")
    args = parser.parse_args()

    root = args.root.resolve()
    wiki = root / "wiki"
    cards: list[dict[str, object]] = []
    errors: list[str] = []
    classification_diffs: list[dict[str, object]] = []
    parse_errors: list[dict[str, object]] = []

    for path in sorted(wiki.rglob("*.md")):
        relative = path.relative_to(root)
        if len(relative.parts) < 3 or not ZONE_DIR.match(relative.parts[1]):
            continue
        text = read_text(path)

        # ── New authoritative parser (YAML) ──
        yaml_fm, yaml_err = parse_frontmatter_yaml(text)

        # ── Classification audit (parallel run) ──
        # Legacy: use old parse_frontmatter for classify_document
        legacy_fm = parse_frontmatter(text)
        legacy_classification = classify_document(path, root, text=text, frontmatter=legacy_fm)

        if yaml_err:
            # Record parse error, do not silently skip
            doc_type = str(legacy_classification.get("document_type", "unknown"))
            is_card = doc_type == "card" and bool(ZONE_ID.match(str(legacy_fm.get("zone_id", ""))))
            if is_card:
                parse_errors.append({
                    "path": relative.as_posix(),
                    "zone_id": str(legacy_fm.get("zone_id", "")),
                    "error": yaml_err,
                    "legacy_document_type": doc_type,
                })
            # If legacy classified it as card but YAML parse fails, P2-16 must not pass
            continue

        # Use YAML-parsed frontmatter for classification
        classification = classify_document(path, root, text=text, frontmatter=yaml_fm)

        # ── Classification diff audit ──
        if args.audit_classification:
            old_type = legacy_classification.get("document_type")
            new_type = classification.get("document_type")
            if old_type != new_type:
                classification_diffs.append({
                    "path": relative.as_posix(),
                    "old_document_type": old_type,
                    "new_document_type": new_type,
                    "old_zone_id": str(legacy_fm.get("zone_id", "")),
                    "new_zone_id": str(yaml_fm.get("zone_id", "") if yaml_fm else ""),
                })

        if classification["document_type"] != "card":
            continue
        zone_id = str(yaml_fm.get("zone_id", ""))
        match = ZONE_ID.match(zone_id)
        if not match:
            errors.append(f"invalid or missing zone_id: {relative.as_posix()} ({zone_id!r})")
            continue
        tags = yaml_fm.get("tags", [])
        tag_values = tags if isinstance(tags, list) else [str(tags)] if tags else []
        cards.append(
            {
                "zone_id": zone_id,
                "zone_letter": match.group(1).upper(),
                "zone_number": int(match.group(2)),
                "card_number": int(match.group(3)),
                "title": path.stem,
                "type": str(yaml_fm.get("type", "")),
                "status": str(yaml_fm.get("status", "published")) or "published",
                "path": relative.as_posix(),
                "keywords": ", ".join(clean_cell(str(tag)) for tag in tag_values),
                "section": section_label(relative, zone_id),
            }
        )

    by_id: dict[str, list[str]] = defaultdict(list)
    by_path: dict[str, int] = defaultdict(int)
    for card in cards:
        by_id[str(card["zone_id"])].append(str(card["path"]))
        by_path[str(card["path"])] += 1
    for zone_id, paths in by_id.items():
        if len(paths) > 1:
            errors.append(f"duplicate zone_id {zone_id}: {'; '.join(paths)}")
    for path, count in by_path.items():
        if count > 1:
            errors.append(f"duplicate path {path}: {count}")
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1

    cards.sort(
        key=lambda card: (
            ZONE_ORDER[str(card["zone_letter"])],
            int(card["zone_number"]),
            int(card["card_number"]),
            str(card["path"]),
        )
    )

    lines = registry_frontmatter(root) + [
        "",
        "# 知识卡片注册表",
        "",
        "> 本文件由 Governance shared runtime 从业务卡片 Frontmatter 确定性生成。",
        "> Frontmatter 是身份真相源；禁止手工逐条维护本表。",
        f"> **总卡片数**: {len(cards)}",
        "",
    ]
    current_section = None
    for card in cards:
        if card["section"] != current_section:
            current_section = card["section"]
            lines.extend(
                [
                    f"## {current_section}",
                    "| zone_id | 卡片标题 | type | status | path | 关键词 |",
                    "|---|---|---|---|---|---|",
                ]
            )
        lines.append(
            "| {zone_id} | [[{title}]] | {type} | {status} | {path} | {keywords} |".format(
                **{key: clean_cell(str(value)) for key, value in card.items()}
            )
        )
    lines.append("")

    args.out.resolve().write_text("\n".join(lines), encoding="utf-8", newline="\n")
    output = {
        "ok": True,
        "cards": len(cards),
        "sections": len({str(card["section"]) for card in cards}),
        "out": str(args.out.resolve()),
    }
    if args.audit_classification:
        output["classification_diffs"] = classification_diffs
        output["classification_diff_count"] = len(classification_diffs)
    if parse_errors:
        output["parse_errors"] = parse_errors
        output["parse_error_count"] = len(parse_errors)
        output["ok"] = False  # Parse errors mean registry cannot be trusted
    print(
        json.dumps(output, ensure_ascii=False, indent=2),
    )
    return 0 if (output["ok"] and not parse_errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
