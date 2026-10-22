"""
Shared Frontmatter parser for Leon's Wiki governance.

This module is the SINGLE authoritative parser for Frontmatter YAML.
All governance metrics, Registry, lint, and validation MUST consume
results from this module, not from the legacy parse_frontmatter().

Contract sources:
- _SYSTEM.md v2.3 (知识卡片格式 section)
- _ops/governance_rules.md (Card Frontmatter Guard section)
"""

from __future__ import annotations

import re
import yaml
from pathlib import Path


# ── Exported constants ──────────────────────────────────────────────────

# Zone ID format: letter + number + number (e.g. A3.1, C13.78, G2.17)
# Current zones from _SYSTEM.md: A, C, D, F, G, P
ZONE_ID_PATTERN = re.compile(r"^([ACDFGP])(\d+)\.(\d+)$", re.IGNORECASE)

# Paths under wiki/ that are governed card zones
ZONE_DIR_PATTERN = re.compile(
    r"^([ACDFGP])_(?:原理层|操作层|策略沙盒|政策法规|通用知识树|场景层)$"
)

# Card types from _SYSTEM.md
CARD_TYPES = {"concept", "principle", "sop", "checklist", "model", "case", "playbook"}

# Required fields from _SYSTEM.md + governance_rules.md
CARD_REQUIRED_FIELDS = {
    "type", "zone", "zone_id", "parent", "tags",
    "sources", "confidence", "created", "updated",
}

# Sources status labels (mutually exclusive)
SOURCES_ABSENT = "absent"
SOURCES_NULL = "null"
SOURCES_EMPTY_LIST = "empty_list"
SOURCES_NON_EMPTY = "non_empty_list"
SOURCES_INVALID_TYPE = "invalid_type"
SOURCES_PARSE_ERROR = "parse_error"

SOURCES_STATUS_LABELS = {
    SOURCES_ABSENT: "sources key not in frontmatter",
    SOURCES_NULL: "sources: null / sources: ~ / sources: (empty value)",
    SOURCES_EMPTY_LIST: "sources: []",
    SOURCES_NON_EMPTY: "sources: [path, ...] (one or more string items)",
    SOURCES_INVALID_TYPE: "sources value is not a string list (scalar, int, bool, dict, mixed list)",
    SOURCES_PARSE_ERROR: "frontmatter YAML could not be parsed",
}


# ── Frontmatter boundary detection ──────────────────────────────────────

# Safety limit: 256 KiB for frontmatter region
_FM_MAX_BYTES = 256 * 1024


def _locate_frontmatter_bounds(text: str) -> tuple[int, int] | None:
    """Locate the start and end line of the YAML frontmatter block.

    Rules:
    - After stripping UTF-8 BOM, line 0 must be exactly ``---``.
    - Find the NEXT line that is exactly ``---`` (after strip).
    - A line containing ``---`` as part of a value (e.g. a file path with
      ``---`` in it) is NOT a boundary.
    - Support LF and CRLF line endings.
    - If no closing ``---`` is found within a safety byte limit, return None.

    Returns:
        (start_line, end_line) 0-based, or None if not found.
    """
    # Strip UTF-8 BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    # Find the closing ``---``
    # Safety: track byte offset to avoid unbounded scanning
    byte_offset = 0
    for i in range(1, len(lines)):
        # Approximate byte offset (UTF-8, worst case 4 bytes/char)
        byte_offset += len(lines[i - 1].encode("utf-8")) + 1  # +1 for newline
        if byte_offset > _FM_MAX_BYTES:
            return None  # safety limit exceeded
        if lines[i].strip() == "---":
            return (0, i)

    return None  # no closing --- found


def _extract_frontmatter_text(text: str) -> tuple[str, str | None]:
    """Extract the raw YAML text between frontmatter boundaries.

    Returns:
        (yaml_text, error_message)
        - yaml_text: the raw text between ``---`` markers (may be empty).
        - error_message: None if bounds found, str if error.
    """
    bounds = _locate_frontmatter_bounds(text)
    if bounds is None:
        return "", "frontmatter_boundary_not_found"
    _, end = bounds
    lines = text.splitlines()
    # Remove UTF-8 BOM from line 0 if present
    first_line = lines[0]
    if first_line.startswith("\ufeff"):
        first_line = first_line[1:]
    if first_line.strip() != "---":
        return "", "frontmatter_boundary_not_found"
    # Lines 1..end-1 are the frontmatter content
    if end <= 1:
        return "", "frontmatter_empty"
    return "\n".join(lines[1:end]), None


# ── YAML parsing ────────────────────────────────────────────────────────


def parse_frontmatter_yaml(text: str) -> tuple[dict | None, str | None]:
    """Parse the frontmatter block using ``yaml.safe_load``.

    Returns:
        (parsed_dict, error_message)
        - If successful:         (dict, None)
        - Boundary not found:    (None, "frontmatter_boundary_not_found")
        - Frontmatter empty:     (None, "frontmatter_empty")
        - YAML parse error:      (None, "yaml_parse_error: <details>")
        - Top-level not mapping: (None, "frontmatter_not_a_mapping: <type>")
    """
    yaml_text, err = _extract_frontmatter_text(text)
    if err:
        return None, err
    if not yaml_text.strip():
        return None, "frontmatter_empty"
    try:
        result = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return None, f"yaml_parse_error: {e}"
    if not isinstance(result, dict):
        return None, f"frontmatter_not_a_mapping: got {type(result).__name__}"
    return result, None


# ── Sources status classification ───────────────────────────────────────


def classify_sources_status(
    fm: dict | None, error: str | None,
) -> str:
    """Classify the ``sources`` field into one of the mutually exclusive statuses.

    Args:
        fm: Parsed frontmatter dict (None if parse failed).
        error: Error message from ``parse_frontmatter_yaml`` (None if OK).

    Returns:
        One of ``SOURCES_*`` constants.
    """
    if error:
        return SOURCES_PARSE_ERROR
    if fm is None:
        return SOURCES_PARSE_ERROR
    if "sources" not in fm:
        return SOURCES_ABSENT

    val = fm["sources"]
    if val is None:
        return SOURCES_NULL
    if isinstance(val, list):
        if len(val) == 0:
            return SOURCES_EMPTY_LIST
        # All items must be strings
        if all(isinstance(item, str) for item in val):
            return SOURCES_NON_EMPTY
        return SOURCES_INVALID_TYPE
    # String scalar, int, bool, dict, etc. -- not a list
    return SOURCES_INVALID_TYPE


# ── Tags status classification ──────────────────────────────────────────


def classify_tags_status(fm: dict) -> str:
    """Classify the ``tags`` field.

    Returns:
        ``"present"``, ``"absent"``, ``"null"``, or ``"invalid_type"``.
    """
    if "tags" not in fm:
        return "absent"
    val = fm["tags"]
    if val is None:
        return "null"
    if isinstance(val, list):
        return "present"  # empty list is still present per contract
    return "invalid_type"


# ── Document classification (replacement for legacy classify_document) ──


def classify_document_yaml(
    path: Path,
    root: Path | None = None,
    *,
    text: str | None = None,
    frontmatter: dict | None = None,
    yaml_error: str | None = None,
) -> dict:
    """Classify a Markdown document's type using YAML-parsed frontmatter.

    This is the new authoritative classifier. It mirrors the logic of
    the legacy ``classify_document()`` in ``rebuild_registry.py`` but
    consumes its frontmatter from ``parse_frontmatter_yaml()`` instead of
    the legacy line-level parser.

    Returns the same schema as legacy ``classify_document()``.
    """
    from rebuild_registry import classify_document as legacy_classify

    # Use legacy classify_document but with YAML-parsed frontmatter
    # This is a bridge: we pass the YAML-parsed frontmatter as the
    # 'frontmatter' parameter, which classify_document uses as 'strict'.
    result = legacy_classify(
        path, root, text=text, frontmatter=frontmatter,
    )
    return result


# ── Field-level completeness check ──────────────────────────────────────


def check_field_completeness(
    fm: dict | None,
    yaml_error: str | None,
    field_rules: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Check all required fields against contract rules.

    Returns a dict mapping field name to one of:
        ``"ok"``, ``"missing"``, ``"null"``, ``"invalid_type"``,
        ``"parse_error"``.

    If ``field_rules`` is None, the default rules from ``_SYSTEM.md``
    and ``governance_rules.md`` are used.
    """
    errors: dict[str, str] = {}

    if yaml_error:
        for field in CARD_REQUIRED_FIELDS:
            errors[field] = "parse_error"
        return errors
    if fm is None:
        for field in CARD_REQUIRED_FIELDS:
            errors[field] = "parse_error"
        return errors

    # ── type ──
    val = fm.get("type")
    if val is None or val == "":
        errors["type"] = "missing"
    elif isinstance(val, str):
        errors["type"] = "ok"
    else:
        errors["type"] = "invalid_type"

    # ── zone ──
    val = fm.get("zone")
    if val is None or val == "":
        errors["zone"] = "missing"
    elif isinstance(val, str):
        errors["zone"] = "ok"
    else:
        errors["zone"] = "invalid_type"

    # ── zone_id ──
    val = fm.get("zone_id")
    if val is None or val == "":
        errors["zone_id"] = "missing"
    elif isinstance(val, str) and ZONE_ID_PATTERN.match(val):
        errors["zone_id"] = "ok"
    else:
        errors["zone_id"] = "invalid_type"

    # ── parent ──
    val = fm.get("parent")
    if val is None or val == "":
        errors["parent"] = "missing"
    elif isinstance(val, str):
        errors["parent"] = "ok"
    else:
        errors["parent"] = "invalid_type"

    # ── tags ──
    val = fm.get("tags")
    if "tags" not in fm:
        errors["tags"] = "missing"
    elif val is None:
        errors["tags"] = "null"
    elif isinstance(val, list):
        errors["tags"] = "ok"  # empty list is valid per contract
    else:
        errors["tags"] = "invalid_type"

    # ── sources ──
    src_status = classify_sources_status(fm, None)
    if src_status in (SOURCES_ABSENT, SOURCES_NULL, SOURCES_INVALID_TYPE):
        errors["sources"] = "missing"
    elif src_status == SOURCES_PARSE_ERROR:
        errors["sources"] = "parse_error"
    else:
        errors["sources"] = "ok"  # empty_list and non_empty are valid

    # ── confidence ──
    val = fm.get("confidence")
    if val is None or val == "":
        errors["confidence"] = "missing"
    elif isinstance(val, str):
        errors["confidence"] = "ok"
    else:
        errors["confidence"] = "invalid_type"

    # ── created ──
    val = fm.get("created")
    if val is None or val == "":
        errors["created"] = "missing"
    elif isinstance(val, str):
        errors["created"] = "ok"
    elif hasattr(val, "isoformat"):  # datetime.date / datetime
        errors["created"] = "ok"
    else:
        errors["created"] = "invalid_type"

    # ── updated ──
    val = fm.get("updated")
    if val is None or val == "":
        errors["updated"] = "missing"
    elif isinstance(val, str):
        errors["updated"] = "ok"
    elif hasattr(val, "isoformat"):
        errors["updated"] = "ok"
    else:
        errors["updated"] = "invalid_type"

    return errors