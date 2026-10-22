from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")

from rebuild_registry import (
    CARD_TYPES,
    ZONE_ID,
    classify_document,
    parse_frontmatter,
    read_text,
    recover_frontmatter,
)

# New authoritative YAML parser (P2-16)
from frontmatter import (
    parse_frontmatter_yaml,
    classify_sources_status,
    classify_tags_status,
    check_field_completeness,
    CARD_REQUIRED_FIELDS,
)


ALLOWED_STATUSES = {
    "queued",
    "running",
    "blocked",
    "review_required",
    "passed",
    "failed",
    "archived",
}

REQUIRED_STATE_FIELDS = {
    "schema_version",
    "contract_version",
    "repository",
    "current_phase",
    "current_batch",
    "status",
    "active_device",
    "started_at",
    "updated_at",
    "completed_phases",
    "blocked_phases",
    "batches",
    "validation",
    "stop_reason",
    "next_action",
}

CONDITIONAL_GATES = {"business_brain_page_reconciliation", "embedding_missing_stale", "five_fixed_queries"}


def parse_json_stdout(stdout_str: str, strict: bool = True) -> dict[str, Any]:
    stdout_clean = stdout_str.strip()
    if strict:
        return json.loads(stdout_clean)
    
    # Historical compatibility path (deprecated)
    start_idx = stdout_clean.find('{')
    if start_idx == -1:
        raise json.JSONDecodeError("No JSON object found", stdout_clean, 0)
    
    decoder = json.JSONDecoder()
    payload, end_idx = decoder.raw_decode(stdout_clean, start_idx)
    
    # Warn about deprecation (to stderr)
    sys.stderr.write(
        "DEPRECATION WARNING: parsed JSON with historical noise. "
        "This compatibility path is deprecated and will be removed.\n"
    )
    return payload


def run_json_subprocess(args: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    """Run a machine-readable child and parse its one UTF-8 JSON document."""
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=False,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
    )
    stdout = result.stdout.decode("utf-8", errors="strict")
    stderr = result.stderr.decode("utf-8", errors="strict")
    if result.returncode != 0:
        raise RuntimeError(
            f"JSON subprocess exited with {result.returncode}: {stderr.strip()}"
        )
    return parse_json_stdout(stdout, strict=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def validate_state(state: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_STATE_FIELDS - state.keys())
    if missing:
        errors.append(f"missing state fields: {', '.join(missing)}")
    if state.get("status") not in ALLOWED_STATUSES:
        errors.append(f"invalid global status: {state.get('status')!r}")
    repository = Path(str(state.get("repository", "")))
    configured_root = repository.resolve() if repository.is_absolute() else (root / repository).resolve()
    if configured_root != root.resolve():
        errors.append(f"repository mismatch: {configured_root} != {root}")

    batches = state.get("batches", [])
    if not isinstance(batches, list):
        return errors + ["batches must be a list"]
    by_id: dict[str, dict[str, Any]] = {}
    for item in batches:
        if not isinstance(item, dict):
            errors.append("batch entry must be an object")
            continue
        batch_id = str(item.get("batch", ""))
        if not batch_id:
            errors.append("batch without id")
            continue
        if batch_id in by_id:
            errors.append(f"duplicate batch id: {batch_id}")
        by_id[batch_id] = item
        if item.get("status") not in ALLOWED_STATUSES:
            errors.append(f"invalid status for {batch_id}: {item.get('status')!r}")
        max_files = item.get("max_files")
        if not isinstance(max_files, int) or max_files < 0:
            errors.append(f"invalid max_files for {batch_id}: {max_files!r}")
        if item.get("execution_mode") not in {"read_only", "manual_preparation", "completed_external", "scripted"}:
            errors.append(f"invalid execution_mode for {batch_id}: {item.get('execution_mode')!r}")

    for batch_id, item in by_id.items():
        for dependency in item.get("dependencies", []):
            if dependency not in by_id:
                errors.append(f"unknown dependency for {batch_id}: {dependency}")
    current = by_id.get(str(state.get("current_batch", "")))
    if current is None:
        errors.append("current_batch is not present in batches")
    elif current.get("phase") != state.get("current_phase"):
        errors.append("current_batch phase does not match current_phase")

    completed = set(state.get("completed_phases", []))
    blocked = {str(item.get("phase")) for item in state.get("blocked_phases", []) if isinstance(item, dict)}
    overlap = sorted(completed & blocked)
    if overlap:
        errors.append(f"phases cannot be completed and blocked: {', '.join(overlap)}")
    return errors


def markdown_index(root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in root.rglob("*.md"):
        if "_ops" in path.relative_to(root).parts:
            continue
        result.setdefault(path.stem, []).append(path)
    return result


def wikilink_target(value: object) -> str | None:
    match = re.search(r"\[\[([^\]|#]+)", str(value))
    return match.group(1).strip() if match else None


def validate_document(path: Path, root: Path, index: dict[str, list[Path]]) -> dict[str, Any]:
    text = read_text(path)
    classification = classify_document(path, root, text=text)
    document_type = str(classification["document_type"])

    # New authoritative YAML parser (P2-16)
    yaml_fm, yaml_err = parse_frontmatter_yaml(text)

    # Legacy parser (retained for compatibility in MOC guard and diagnostics)
    legacy_strict = parse_frontmatter(text)
    legacy_recovered, _ = recover_frontmatter(text)
    metadata = legacy_strict or legacy_recovered

    failures: list[str] = []

    if document_type == "moc":
        if not text.startswith("---"):
            failures.append("moc_frontmatter_not_at_file_start")
        elif not legacy_strict:
            failures.append("moc_frontmatter_unparseable")
        tags = metadata.get("tags", [])
        tag_values = tags if isinstance(tags, list) else [str(tags)] if tags else []
        if "moc" not in {str(tag).strip().lower() for tag in tag_values}:
            failures.append("moc_tag_missing")
        is_root_moc = path.resolve() == (root / "_MOC_主干道.md").resolve()
        if not is_root_moc:
            parent = wikilink_target(metadata.get("parent", ""))
            targets = index.get(Path(parent).stem, []) if parent else []
            navigation_targets = [
                target for target in targets
                if classify_document(target, root, text=read_text(target))["document_type"] == "moc"
            ]
            if not parent or len(navigation_targets) != 1:
                failures.append("moc_parent_missing_or_invalid")
        declared_type = str(metadata.get("type", "")).strip().lower()
        if declared_type and declared_type != "moc":
            failures.append("moc_type_invalid")
        if not re.search(r"(?m)^#\s+\S", text):
            failures.append("moc_h1_missing")
        registry = read_text(root / "_registry.md") if (root / "_registry.md").exists() else ""
        relative = path.resolve().relative_to(root).as_posix()
        if relative in registry:
            failures.append("moc_present_in_registry")
    elif document_type == "card":
        if not text.startswith("---"):
            failures.append("card_frontmatter_not_at_file_start")
        elif not legacy_strict:
            failures.append("card_frontmatter_unparseable")
        # Use new YAML parser for field completeness (P2-16)
        field_errors = check_field_completeness(yaml_fm, yaml_err)
        missing = sorted(
            field for field, status in field_errors.items()
            if status not in ("ok",)
        )
        failures.extend(f"card_required_field_missing:{field}" for field in missing)
        if yaml_err:
            failures.append(f"card_frontmatter_yaml_error:{yaml_err}")
        declared_type = str(metadata.get("type", "")).strip().lower()
        if declared_type and declared_type not in CARD_TYPES:
            failures.append("card_type_invalid")
        if yaml_fm and yaml_fm.get("zone_id") and not ZONE_ID.fullmatch(str(yaml_fm["zone_id"])):
            failures.append("card_zone_id_invalid")
    elif document_type in {"other", "ambiguous"}:
        failures.append("document_classification_review_required")

    return {
        "path": path.resolve().relative_to(root).as_posix(),
        "document_type": document_type,
        "identity": classification["identity"],
        "classification_evidence": classification["evidence"],
        "guard_status": "passed" if not failures else "review_required" if document_type in {"other", "ambiguous"} else "failed",
        "failure": failures[0] if failures else None,
        "failures": failures,
    }


def run_document_guard(root: Path, paths: list[Path]) -> dict[str, Any]:
    index = markdown_index(root)
    results = [validate_document(path.resolve(), root, index) for path in paths]
    summary = {kind: 0 for kind in ("card", "moc", "other", "ambiguous")}
    for result in results:
        summary[result["document_type"]] += 1
    failed = [result for result in results if result["failure"]]
    return {
        "ok": not failed,
        "summary": {"total": len(results), **summary, "failed_or_review_required": len(failed)},
        "failures": [{"path": item["path"], "failure": item["failure"]} for item in failed],
        "results": results,
    }


def registry_checks(root: Path) -> dict[str, dict[str, Any]]:
    script = root / "_ops" / "rebuild_registry.py"
    actual = root / "_registry.md"
    if not script.exists() or not actual.exists():
        return {
            "registry_rebuild": {"status": "failed", "evidence": "script or _registry.md missing"},
            "registry_deterministic_hash": {"status": "failed", "evidence": "prerequisite missing"},
            "duplicate_id_path": {"status": "failed", "evidence": "prerequisite missing"},
            "unregistered_ghost": {"status": "failed", "evidence": "prerequisite missing"},
        }
    with tempfile.TemporaryDirectory(prefix="wiki-governance-") as temp_dir:
        out1 = Path(temp_dir) / "registry-1.md"
        out2 = Path(temp_dir) / "registry-2.md"
        runs = []
        for output in (out1, out2):
            try:
                runs.append(run_json_subprocess(
                    [sys.executable, str(script), str(root), "--out", str(output)],
                    cwd=root,
                ))
            except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                evidence = str(exc)
                failed = {"status": "failed", "evidence": evidence}
                return {name: dict(failed) for name in (
                    "registry_rebuild",
                    "registry_deterministic_hash",
                    "duplicate_id_path",
                    "unregistered_ghost",
                )}
        if len(runs) != 2:
            evidence = "registry subprocess did not return two JSON results"
            failed = {"status": "failed", "evidence": evidence}
            return {name: dict(failed) for name in (
                "registry_rebuild",
                "registry_deterministic_hash",
                "duplicate_id_path",
                "unregistered_ghost",
            )}
        hash1, hash2, actual_hash = sha256(out1), sha256(out2), sha256(actual)
        deterministic = hash1 == hash2
        matches = hash1 == actual_hash
        
        payload = runs[0]
        return {
            "registry_rebuild": {
                "status": "passed" if matches else "failed",
                "evidence": {"cards": payload.get("cards"), "generated_sha256": hash1, "actual_sha256": actual_hash},
            },
            "registry_deterministic_hash": {
                "status": "passed" if deterministic else "failed",
                "evidence": {"first": hash1, "second": hash2},
            },
            "duplicate_id_path": {
                "status": "passed",
                "evidence": "rebuild_registry.py completed without duplicate ID/path errors",
            },
            "unregistered_ghost": {
                "status": "passed" if matches else "failed",
                "evidence": "formal Registry exactly matches deterministic Frontmatter rebuild" if matches else "formal Registry differs from rebuild",
            },
        }


def build_gate_report(root: Path, state: dict[str, Any], batch_id: str | None) -> dict[str, Any]:
    gates = registry_checks(root)
    required = list(state.get("validation", {}).get("required_gates", []))
    evidence_path = root / "_ops" / "validation" / f"{batch_id}.json" if batch_id else None
    external: dict[str, Any] = {}
    if evidence_path and evidence_path.exists():
        external = load_json(evidence_path).get("gates", {})
    for gate in required:
        if gate not in gates:
            value = external.get(gate)
            gates[gate] = value if isinstance(value, dict) else {
                "status": "not_configured",
                "evidence": f"no evidence supplied in {evidence_path}" if evidence_path else "no batch selected",
            }
    invalid = [
        name for name in required
        if gates.get(name, {}).get("status") != "passed"
        and not (name in CONDITIONAL_GATES and gates.get(name, {}).get("status") == "not_required")
    ]
    return {
        "batch": batch_id,
        "all_required_gates_passed": not invalid,
        "failed_or_incomplete_gates": invalid,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Leon's Wiki governance state and optional batch gates")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--batch")
    parser.add_argument("--full-gates", action="store_true")
    parser.add_argument("--document-guard", action="store_true")
    parser.add_argument("--path", action="append", type=Path, default=[])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    state_path = args.state.resolve() if args.state else root / "_ops" / "governance_state.json"
    try:
        if args.document_guard:
            if not args.path:
                raise ValueError("--document-guard requires at least one --path")
            if args.report is None:
                raise ValueError("--document-guard requires --report for detailed file-level results")
            selected: list[Path] = []
            for value in args.path:
                candidate = value.resolve() if value.is_absolute() else (root / value).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"document path is outside repository: {candidate}") from exc
                if not candidate.is_file():
                    raise ValueError(f"document path does not exist: {candidate}")
                selected.append(candidate)
            document_report = run_document_guard(root, selected)
            report_path = args.report.resolve() if args.report.is_absolute() else (root / args.report).resolve()
            atomic_write_json(report_path, document_report)
            compact = {
                "ok": document_report["ok"],
                "summary": document_report["summary"],
                "failures": document_report["failures"],
                "report": str(report_path),
            }
            print(json.dumps(compact, ensure_ascii=False, indent=2) if args.json else json.dumps(compact, ensure_ascii=False))
            return 0 if document_report["ok"] else 1
        state = load_json(state_path)
        errors = validate_state(state, root)
        report: dict[str, Any] = {"ok": not errors, "state": str(state_path), "errors": errors}
        if args.full_gates:
            report["validation"] = build_gate_report(root, state, args.batch)
            report["ok"] = report["ok"] and report["validation"]["all_required_gates_passed"]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {"ok": False, "state": str(state_path), "errors": [str(exc)]}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
