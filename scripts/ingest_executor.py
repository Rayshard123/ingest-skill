from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

sys.dont_write_bytecode = True

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from canonical_directory import (
    CanonicalResolutionError,
    RegistryEntry,
    build_markdown_title_index,
    parse_registry,
    resolve_canonical_directory,
    resolve_unique_title,
    wikilink_target,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "references" / "ingest-plan.schema.json"
DEFAULT_GOVERNANCE_ROOT = SKILL_ROOT / "runtime" / "governance"
LOCK_TTL = timedelta(hours=2)
LOCK_RELATIVE = Path("_ops/governance.lock.json")
FORBIDDEN_TITLE_CHARS = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
ZONE_ID_RE = re.compile(r"^([ACDFGP])(\d+)\.(\d+)$", re.IGNORECASE)
PROMPT_ANCHOR_HINTS = (
    "sop",
    "case",
    "hello",
    "prompt",
    "checklist",
    "template",
    "instruction",
    "role",
    "input",
    "output",
    "提示词",
    "提示",
    "模板",
    "清单",
    "检查表",
    "步骤",
    "操作",
    "角色",
    "输入",
    "输出",
    "示例",
    "案例",
    "话术",
    "指令",
)


class IngestFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: object | None = None,
        exit_code: int = 2,
    ):
        super().__init__(message)
        self.code = code
        self.details = details
        self.exit_code = exit_code

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.details is not None:
            payload["details"] = self.details
        return payload


@dataclass
class ExecutionOptions:
    root: Path
    plan_path: Path
    device: str
    work_root: Path
    sync_confirmed: bool
    hostname: str = field(default_factory=socket.gethostname)
    governance_root: Path = DEFAULT_GOVERNANCE_ROOT
    profile_path: Path | None = None
    resume: bool = False
    skip_index: bool = False
    index_commands: list[list[str]] | None = None
    test_hook: Callable[[str, dict[str, Any]], None] | None = None
    failpoints: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DeviceProfile:
    path: Path
    device: str
    hostname: str
    vault_root: Path
    work_root: Path
    runtime_root: Path
    python_executable: Path


@dataclass
class ResolvedOperation:
    op: dict[str, Any]
    action: str
    canonical_relative: str
    target_relative: str
    parent_relative: str
    zone_id: str | None = None
    original_sha256: str | None = None
    original_my_experience: list[str] = field(default_factory=list)
    original_body: str = ""


def now() -> datetime:
    return datetime.now().astimezone()


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise IngestFailure("JSON_ROOT_INVALID", f"JSON root must be an object: {path}")
    return data


def atomic_write_bytes(path: Path, data: bytes) -> None:
    if not path.parent.is_dir():
        raise IngestFailure("PARENT_DIRECTORY_MISSING", f"parent directory does not exist: {path.parent}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise IngestFailure("PATH_ESCAPE", f"path escapes the Vault: {path}") from exc


def _absolute_profile_path(value: object, *, field_name: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise IngestFailure("PROFILE_PATH_NOT_ABSOLUTE", f"profile {field_name} must be absolute: {value!r}")
    return path.resolve()


def load_device_profile(profile_path: Path, *, verify_python: bool = True) -> DeviceProfile:
    path = profile_path.resolve()
    if not path.is_file():
        raise IngestFailure("PROFILE_MISSING", f"device profile is missing: {path}")
    data = load_json(path)
    required = {
        "schema_version",
        "device",
        "hostname",
        "vault_root",
        "work_root",
        "runtime_root",
        "python_executable",
    }
    missing = sorted(required - set(data))
    unknown = sorted(set(data) - required)
    if missing or unknown:
        raise IngestFailure(
            "PROFILE_SCHEMA_INVALID",
            "device profile fields do not match the supported schema",
            details={"missing": missing, "unknown": unknown},
        )
    if data["schema_version"] != "1.0":
        raise IngestFailure("PROFILE_SCHEMA_INVALID", "device profile schema_version must be 1.0")
    non_strings = sorted(name for name in required if not isinstance(data[name], str))
    if non_strings:
        raise IngestFailure(
            "PROFILE_SCHEMA_INVALID",
            "all device profile values must be strings",
            details={"non_string_fields": non_strings},
        )
    device = str(data["device"]).upper()
    if device not in {"A", "B"}:
        raise IngestFailure("DEVICE_FORBIDDEN", "Ingest writes require a Device A or B profile")
    hostname = str(data["hostname"]).strip()
    if not hostname:
        raise IngestFailure("PROFILE_SCHEMA_INVALID", "device profile hostname must not be empty")
    actual_hostname = socket.gethostname()
    if hostname.casefold() != actual_hostname.casefold():
        raise IngestFailure(
            "PROFILE_HOST_MISMATCH",
            "device profile belongs to a different host",
            details={"profile": hostname, "actual": actual_hostname},
        )

    vault_root = _absolute_profile_path(data["vault_root"], field_name="vault_root")
    work_root = _absolute_profile_path(data["work_root"], field_name="work_root")
    runtime_root = _absolute_profile_path(data["runtime_root"], field_name="runtime_root")
    python_executable = _absolute_profile_path(data["python_executable"], field_name="python_executable")
    if is_within(vault_root, path):
        raise IngestFailure("PROFILE_INSIDE_VAULT", f"profile must be outside the Vault: {path}")
    for field_name, candidate in (("work_root", work_root), ("runtime_root", runtime_root)):
        if is_within(vault_root, candidate) or is_within(candidate, vault_root):
            raise IngestFailure(
                f"{field_name.upper()}_OVERLAPS_VAULT",
                f"profile {field_name} must not overlap the Vault: {candidate}",
            )
    if not is_within(runtime_root, python_executable):
        raise IngestFailure(
            "PYTHON_OUTSIDE_RUNTIME",
            "profile python_executable must be inside runtime_root",
            details={"runtime_root": str(runtime_root), "python_executable": str(python_executable)},
        )
    if not python_executable.is_file():
        raise IngestFailure("PYTHON_MISSING", f"profile Python executable is missing: {python_executable}")
    if verify_python and python_executable != Path(sys.executable).resolve():
        raise IngestFailure(
            "PYTHON_PROFILE_MISMATCH",
            "executor is not running with the Python fixed by the device profile",
            details={"profile": str(python_executable), "actual": str(Path(sys.executable).resolve())},
        )
    return DeviceProfile(
        path=path,
        device=device,
        hostname=hostname,
        vault_root=vault_root,
        work_root=work_root,
        runtime_root=runtime_root,
        python_executable=python_executable,
    )


def options_from_profile(
    profile: DeviceProfile,
    plan_path: Path,
    *,
    sync_confirmed: bool,
    resume: bool = False,
    skip_index: bool = False,
) -> ExecutionOptions:
    return ExecutionOptions(
        root=profile.vault_root,
        plan_path=plan_path,
        device=profile.device,
        work_root=profile.work_root,
        sync_confirmed=sync_confirmed,
        hostname=profile.hostname,
        governance_root=DEFAULT_GOVERNANCE_ROOT,
        profile_path=profile.path,
        resume=resume,
        skip_index=skip_index,
    )


def validate_title(title: str) -> None:
    if title in {".", ".."} or title != title.strip() or FORBIDDEN_TITLE_CHARS.search(title):
        raise IngestFailure("TITLE_INVALID", f"title cannot be converted to a safe filename: {title!r}")
    if title.endswith(".") or title.endswith(" "):
        raise IngestFailure("TITLE_INVALID", f"title has an unsafe Windows suffix: {title!r}")


def validate_plan(plan: dict[str, Any], schema_path: Path = SCHEMA_PATH) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(plan), key=lambda item: list(item.absolute_path))
    if errors:
        formatted = []
        for error in errors[:20]:
            location = "/".join(str(part) for part in error.absolute_path) or "$"
            formatted.append({"path": location, "message": error.message})
        raise IngestFailure(
            "PLAN_SCHEMA_INVALID",
            "ingest_plan.json does not satisfy the strict R3 schema",
            details=formatted,
        )

    op_ids: set[str] = set()
    proposed_titles: set[str] = set()
    for operation in plan["operations"]:
        op_id = operation["op_id"]
        if op_id in op_ids:
            raise IngestFailure("PLAN_DUPLICATE_OP_ID", f"duplicate op_id: {op_id}")
        op_ids.add(op_id)
        title = operation.get("title") or operation.get("target", {}).get("title")
        if title:
            validate_title(title)
        action = operation["action"]
        if action in {"create", "enrich"}:
            body = operation["body"].lstrip("\ufeff")
            if body.startswith("---"):
                raise IngestFailure("PLAN_FRONTMATTER_FORBIDDEN", f"AI body contains frontmatter: {op_id}")
            expected_h1 = f"# {title}"
            first_h1 = next((line.strip() for line in body.splitlines() if line.startswith("# ")), None)
            if first_h1 != expected_h1:
                raise IngestFailure(
                    "CARD_H1_MISMATCH",
                    f"card H1 must exactly match its title: {op_id}",
                    details={"expected": expected_h1, "actual": first_h1},
                )
        if action == "create":
            if title in proposed_titles:
                raise IngestFailure("PLAN_DUPLICATE_TITLE", f"duplicate proposed title: {title}")
            proposed_titles.add(title)
            if re.search(r"(?im)^\s*>\s*\[!MY-EXPERIENCE\]", body):
                raise IngestFailure(
                    "MY_EXPERIENCE_FORBIDDEN",
                    f"AI may not create a MY-EXPERIENCE block: {op_id}",
                )
            classification = operation["classification"]
            card_type = operation["metadata"]["type"]
            if classification["zone"] == "P" and card_type != "playbook":
                raise IngestFailure("PLAYBOOK_TYPE_REQUIRED", f"P-zone card must be type playbook: {op_id}")
            if classification["zone"] == "D" and operation["metadata"]["risk_level"] not in {"medium", "high"}:
                raise IngestFailure("D_ZONE_RISK_REQUIRED", f"D-zone card must have medium/high risk: {op_id}")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def decode_document(path: Path) -> tuple[str, bool, str]:
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if has_bom:
        raw = raw[3:]
    text = raw.decode("utf-8", errors="strict")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), has_bom, newline


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise IngestFailure("FRONTMATTER_MISSING", "document frontmatter must start on line 1")
    closing = next((index for index, line in enumerate(lines[1:400], 1) if line.strip() == "---"), None)
    if closing is None:
        raise IngestFailure("FRONTMATTER_UNCLOSED", "document frontmatter has no closing boundary")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise IngestFailure("FRONTMATTER_INVALID", f"frontmatter YAML is invalid: {exc}") from exc
    if not isinstance(metadata, dict):
        raise IngestFailure("FRONTMATTER_INVALID", "frontmatter must be a YAML mapping")
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return metadata, body


def render_document(
    metadata: dict[str, Any],
    body: str,
    *,
    bom: bool = False,
    newline: str = "\n",
) -> bytes:
    yaml_text = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    ).strip()
    normalized_body = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    document = f"---\n{yaml_text}\n---\n\n{normalized_body}\n"
    if newline != "\n":
        document = document.replace("\n", newline)
    encoded = document.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if bom else encoded


def extract_my_experience_blocks(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if re.match(r"^\s*>\s*\[!MY-EXPERIENCE\]", lines[index], flags=re.IGNORECASE):
            block = [lines[index]]
            index += 1
            while index < len(lines) and re.match(r"^\s*>", lines[index]):
                block.append(lines[index])
                index += 1
            blocks.append("\n".join(block))
            continue
        index += 1
    return blocks


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def lock_expired(lock: dict[str, Any]) -> bool:
    try:
        return parse_time(str(lock["expires_at"])) <= now()
    except (KeyError, TypeError, ValueError):
        return True


def acquire_lock(
    root: Path,
    device: str,
    plan_id: str,
    resume: bool,
    recovery_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    lock_path = root / LOCK_RELATIVE
    if not lock_path.parent.is_dir():
        raise IngestFailure("OPS_DIRECTORY_MISSING", f"_ops directory is missing: {lock_path.parent}")
    payload = {
        "device": device,
        "hostname": socket.gethostname(),
        "process": f"ingest_executor.py:{os.getpid()}",
        "acquired_at": iso(),
        "expires_at": iso(now() + LOCK_TTL),
        "phase": "INGEST_R3",
        "batch": plan_id,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    takeover: Path | None = None
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        try:
            existing = load_json(lock_path)
        except (OSError, ValueError, json.JSONDecodeError, IngestFailure) as exc:
            raise IngestFailure("LOCK_INVALID", f"existing lock cannot be trusted: {lock_path}") from exc
        if not lock_expired(existing):
            raise IngestFailure("LOCK_ACTIVE", "another governance/Ingest writer owns the Vault lock", details=existing)
        if not resume:
            raise IngestFailure("LOCK_EXPIRED", "expired lock exists; explicit --resume is required", details=existing)
        takeover = recovery_dir / f"expired-governance-lock-{int(now().timestamp())}.json"
        try:
            atomic_write_bytes(takeover, lock_path.read_bytes())
            lock_path.unlink()
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except BaseException:
            if takeover.exists() and not lock_path.exists():
                atomic_write_bytes(lock_path, takeover.read_bytes())
            raise
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return lock_path, payload


def release_lock(lock_path: Path, owner: dict[str, Any]) -> None:
    if not lock_path.exists():
        return
    try:
        current = load_json(lock_path)
    except (OSError, ValueError, json.JSONDecodeError, IngestFailure):
        return
    if current.get("process") == owner.get("process") and current.get("acquired_at") == owner.get("acquired_at"):
        lock_path.unlink()


def child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def run_process(arguments: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=False,
        check=False,
        env=child_environment(),
    )
    stdout = result.stdout.decode("utf-8", errors="strict")
    stderr = result.stderr.decode("utf-8", errors="strict")
    return {"returncode": result.returncode, "stdout": stdout, "stderr": stderr}


def parse_json_output(result: dict[str, Any], *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(result["stdout"].strip())
    except json.JSONDecodeError as exc:
        raise IngestFailure(
            "CHILD_JSON_INVALID",
            f"{name} did not emit exactly one JSON document",
            details=result,
        ) from exc
    if not isinstance(payload, dict):
        raise IngestFailure("CHILD_JSON_INVALID", f"{name} JSON root is not an object")
    return payload


def run_registry_rebuild(root: Path, output: Path, governance_root: Path) -> dict[str, Any]:
    script = governance_root / "rebuild_registry.py"
    if not script.is_file():
        raise IngestFailure("REGISTRY_REBUILDER_MISSING", f"Registry rebuilder is missing: {script}")
    result = run_process([sys.executable, str(script), str(root), "--out", str(output)], cwd=output.parent)
    if result["returncode"] != 0:
        raise IngestFailure(
            "REGISTRY_REBUILD_FAILED",
            f"Registry rebuilder exited with {result['returncode']}",
            details=result,
            exit_code=5,
        )
    payload = parse_json_output(result, name="rebuild_registry.py")
    if payload.get("ok") is not True or not output.is_file():
        raise IngestFailure("REGISTRY_REBUILD_FAILED", "Registry rebuild did not produce an accepted output", details=payload)
    return payload


def verify_registry_current(
    root: Path,
    transaction_dir: Path,
    governance_root: Path,
    *,
    suffix: str,
) -> dict[str, Any]:
    candidate = transaction_dir / f"registry-current-{suffix}.md"
    payload = run_registry_rebuild(root, candidate, governance_root)
    actual = root / "_registry.md"
    if not actual.is_file() or sha256(candidate) != sha256(actual):
        raise IngestFailure(
            "REGISTRY_STALE",
            "formal _registry.md does not match a deterministic rebuild",
            details={
                "actual": sha256(actual) if actual.is_file() else None,
                "rebuilt": sha256(candidate),
            },
        )
    return {"payload": payload, "sha256": sha256(candidate)}


def conflict_files(root: Path) -> list[str]:
    findings: list[str] = []
    for base in (root, root / "wiki"):
        if not base.is_dir():
            continue
        iterator = base.iterdir() if base == root else base.rglob("*")
        for path in iterator:
            if not path.is_file():
                continue
            lowered = path.name.casefold()
            if "-冲突" in path.name or "-nsconflict" in lowered:
                findings.append(relative_to_root(root, path))
    return sorted(set(findings))


def moc_link_present(text: str, title: str) -> bool:
    return bool(re.search(r"\[\[" + re.escape(title) + r"(?:[\]|#])", text))


def validate_moc(
    root: Path,
    index: dict[str, list[Path]],
    moc_title: str,
    expected_sha256: str,
    canonical: Path,
    zone: str,
    *,
    heading: str | None = None,
) -> Path:
    try:
        moc = resolve_unique_title(index, moc_title, kind="moc")
    except CanonicalResolutionError as exc:
        raise IngestFailure(exc.code, str(exc), details=exc.details) from exc
    if sha256(moc) != expected_sha256.upper():
        raise IngestFailure(
            "MOC_BASE_CHANGED",
            f"MOC hash no longer matches the proposal: {moc_title}",
            details={"expected": expected_sha256.upper(), "actual": sha256(moc)},
        )

    text, _, _ = decode_document(moc)
    metadata, _ = split_frontmatter(text)
    tags = metadata.get("tags", [])
    values = tags if isinstance(tags, list) else [tags]
    if "moc" not in {str(item).strip().casefold() for item in values}:
        raise IngestFailure("MOC_INVALID", f"target is not an existing governed MOC: {moc_title}")
    if not re.search(r"(?m)^#\s+\S", text):
        raise IngestFailure("MOC_INVALID", f"target MOC has no H1: {moc_title}")

    relative = relative_to_root(root, moc)
    if zone == "P":
        if relative != "_MOC_主干道.md":
            try:
                moc.relative_to(canonical)
            except ValueError as exc:
                raise IngestFailure("MOC_DIRECTORY_MISMATCH", f"P1 MOC is outside the canonical directory: {moc_title}") from exc
    else:
        try:
            moc.relative_to(canonical)
        except ValueError as exc:
            raise IngestFailure(
                "MOC_DIRECTORY_MISMATCH",
                f"MOC is outside the canonical directory for the proposed classification: {moc_title}",
                details={"moc": relative, "canonical": relative_to_root(root, canonical)},
            ) from exc

    if relative != "_MOC_主干道.md":
        parent_title = wikilink_target(metadata.get("parent"))
        if not parent_title:
            raise IngestFailure("MOC_PARENT_INVALID", f"MOC has no existing parent: {moc_title}")
        try:
            resolve_unique_title(index, parent_title, kind="moc_parent")
        except CanonicalResolutionError as exc:
            raise IngestFailure(exc.code, str(exc), details=exc.details) from exc

    if heading is not None:
        matches = [line for line in text.splitlines() if line.strip() == heading.strip()]
        if len(matches) != 1:
            raise IngestFailure(
                "MOC_HEADING_INVALID",
                f"MOC heading must already exist exactly once: {heading!r}",
                details={"moc": moc_title, "matches": len(matches)},
            )
    return moc


def verify_materials(plan: dict[str, Any]) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for material in plan["base"]["materials"]:
        path = Path(material["local_path"])
        if not path.is_absolute():
            raise IngestFailure(
                "MATERIAL_PATH_NOT_ABSOLUTE",
                f"material local_path must be absolute: {material['local_path']!r}",
            )
        path = path.resolve()
        if not path.is_file():
            raise IngestFailure("MATERIAL_MISSING", f"raw material is missing: {path}")
        actual = sha256(path)
        if actual != material["sha256"].upper():
            raise IngestFailure(
                "MATERIAL_CHANGED",
                f"raw material changed after plan generation: {path}",
                details={"expected": material["sha256"].upper(), "actual": actual},
            )
        verified.append({"uri": material["uri"], "path": str(path), "sha256": actual})
    return verified


def verify_base_hashes(root: Path, plan: dict[str, Any]) -> dict[str, str]:
    registry = root / "_registry.md"
    system = root / "_SYSTEM.md"
    if not registry.is_file() or not system.is_file():
        raise IngestFailure("BASE_FILE_MISSING", "_registry.md and _SYSTEM.md must both exist")
    actual = {"registry_sha256": sha256(registry), "system_sha256": sha256(system)}
    expected = {
        "registry_sha256": plan["base"]["registry_sha256"].upper(),
        "system_sha256": plan["base"]["system_sha256"].upper(),
    }
    if actual != expected:
        raise IngestFailure(
            "BASE_STATE_CHANGED",
            "Vault base state changed after plan generation; regenerate or explicitly re-plan",
            details={"expected": expected, "actual": actual},
        )
    return actual


def verify_dedup_candidates(
    root: Path,
    index: dict[str, list[Path]],
    operation: dict[str, Any],
) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for candidate in operation["dedup"]["candidates"]:
        try:
            path = resolve_unique_title(index, candidate["title"], kind="dedup_candidate")
        except CanonicalResolutionError as exc:
            raise IngestFailure(exc.code, str(exc), details=exc.details) from exc
        actual = sha256(path)
        if actual != candidate["expected_sha256"].upper():
            raise IngestFailure(
                "DEDUP_CANDIDATE_CHANGED",
                f"dedup candidate changed after AI review: {candidate['title']}",
                details={"expected": candidate["expected_sha256"].upper(), "actual": actual},
            )
        verified.append({
            "title": candidate["title"],
            "path": relative_to_root(root, path),
            "sha256": actual,
        })
    return verified


def reserve_zone_ids(
    entries: list[RegistryEntry],
    operations: list[dict[str, Any]],
) -> dict[str, str]:
    maxima: dict[tuple[str, int], int] = {}
    for entry in entries:
        key = (entry.zone, entry.zone_number)
        maxima[key] = max(maxima.get(key, 0), entry.card_number)
    reservations: dict[str, str] = {}
    for operation in operations:
        if operation["action"] != "create":
            continue
        classification = operation["classification"]
        key = (classification["zone"], classification["number"])
        next_number = maxima.get(key, 0) + 1
        maxima[key] = next_number
        reservations[operation["op_id"]] = f"{key[0]}{key[1]}.{next_number}"
    return reservations


def resolve_operations(
    root: Path,
    plan: dict[str, Any],
    entries: list[RegistryEntry],
    reservations: dict[str, str],
) -> tuple[list[ResolvedOperation], list[dict[str, str]]]:
    index = build_markdown_title_index(root)
    resolved: list[ResolvedOperation] = []
    hash_checks: dict[str, str] = {}
    target_paths: set[str] = set()
    planned_titles = {operation["title"] for operation in plan["operations"] if operation["action"] == "create"}

    for operation in plan["operations"]:
        action = operation["action"]
        classification = operation.get("classification")
        if action == "needs_governance_review":
            raise IngestFailure(
                "NEEDS_GOVERNANCE_REVIEW",
                "at least one proposed card has no deterministic governed destination",
                details={
                    "op_id": operation["op_id"],
                    "title": operation["title"],
                    "reason": operation["reason"],
                    "details": operation["details"],
                },
                exit_code=3,
            )
        try:
            canonical = resolve_canonical_directory(
                root,
                classification["zone"],
                classification["number"],
                entries,
            )
        except CanonicalResolutionError as exc:
            raise IngestFailure(exc.code, str(exc), details=exc.details) from exc

        verify_dedup_candidates(root, index, operation)
        parent = operation["parent"]
        moc = validate_moc(
            root,
            index,
            parent["moc_title"],
            parent["expected_sha256"],
            canonical,
            classification["zone"],
            heading=parent.get("heading"),
        )
        parent_relative = relative_to_root(root, moc)
        hash_checks[parent_relative] = parent["expected_sha256"].upper()

        if action == "create":
            title = operation["title"]
            if title in index:
                raise IngestFailure(
                    "CARD_TITLE_EXISTS",
                    f"new card title already resolves in the Vault: {title}",
                    details=[relative_to_root(root, path) for path in index[title]],
                )
            target = canonical / f"{title}.md"
            target_relative = relative_to_root(root, target)
            if target.exists():
                raise IngestFailure("CARD_PATH_EXISTS", f"new card target already exists: {target_relative}")
            original_sha = None
            original_blocks: list[str] = []
            original_body = ""
            zone_id = reservations[operation["op_id"]]
        else:
            target_title = operation["target"]["title"]
            try:
                target = resolve_unique_title(index, target_title, kind="enrichment_target")
            except CanonicalResolutionError as exc:
                raise IngestFailure(exc.code, str(exc), details=exc.details) from exc
            target_relative = relative_to_root(root, target)
            try:
                target.relative_to(canonical)
            except ValueError as exc:
                raise IngestFailure(
                    "TARGET_DIRECTORY_MISMATCH",
                    f"enrichment target is outside the canonical directory: {target_title}",
                ) from exc
            original_sha = sha256(target)
            expected_target = operation["target"]["expected_sha256"].upper()
            if original_sha != expected_target:
                raise IngestFailure(
                    "ENRICHMENT_TARGET_CHANGED",
                    f"enrichment target changed after plan generation: {target_title}",
                    details={"expected": expected_target, "actual": original_sha},
                )
            hash_checks[target_relative] = expected_target
            text, _, _ = decode_document(target)
            metadata, original_body = split_frontmatter(text)
            zone_id = str(metadata.get("zone_id", ""))
            match = ZONE_ID_RE.fullmatch(zone_id)
            if not match or match.group(1).upper() != classification["zone"] or int(match.group(2)) != classification["number"]:
                raise IngestFailure(
                    "TARGET_ZONE_MISMATCH",
                    f"enrichment target identity conflicts with the proposed classification: {target_title}",
                    details={"zone_id": zone_id, "classification": classification},
                )
            if str(metadata.get("zone", "")).upper() != classification["zone"]:
                raise IngestFailure("TARGET_ZONE_MISMATCH", f"target zone field conflicts with its classification: {target_title}")
            existing_parent = wikilink_target(metadata.get("parent"))
            if existing_parent != parent["moc_title"]:
                raise IngestFailure(
                    "TARGET_PARENT_MISMATCH",
                    f"target parent differs from the proposed existing MOC: {target_title}",
                    details={"frontmatter_parent": existing_parent, "proposal": parent["moc_title"]},
                )
            moc_text, _, _ = decode_document(moc)
            if not moc_link_present(moc_text, target_title):
                raise IngestFailure(
                    "TARGET_NOT_MOUNTED",
                    f"existing enrichment target is not mounted in its declared MOC: {target_title}",
                )
            candidates = operation["dedup"]["candidates"]
            if not any(
                candidate["title"] == target_title
                and candidate["relation"] == "same_source"
                and candidate["expected_sha256"].upper() == expected_target
                for candidate in candidates
            ):
                raise IngestFailure(
                    "DEDUP_TARGET_NOT_CONFIRMED",
                    f"enrichment target must be a hash-pinned same_source dedup candidate: {target_title}",
                )
            original_blocks = extract_my_experience_blocks(original_body)
            proposed_blocks = extract_my_experience_blocks(operation["body"])
            if proposed_blocks != original_blocks:
                raise IngestFailure(
                    "MY_EXPERIENCE_CHANGED",
                    f"MY-EXPERIENCE differs from the original target: {target_title}",
                    details={"original": original_blocks, "proposed": proposed_blocks},
                )

        if not target_relative.startswith("wiki/"):
            raise IngestFailure("CARD_TARGET_OUTSIDE_WIKI", f"card target must remain under wiki/: {target_relative}")
        if parent_relative != "_MOC_主干道.md" and not parent_relative.startswith("wiki/"):
            raise IngestFailure("MOC_TARGET_OUTSIDE_GOVERNED_PATHS", f"MOC target is not governed: {parent_relative}")
        if target_relative in target_paths:
            raise IngestFailure("TARGET_DUPLICATED_IN_PLAN", f"multiple operations target the same file: {target_relative}")
        target_paths.add(target_relative)
        resolved.append(
            ResolvedOperation(
                op=operation,
                action=action,
                canonical_relative=relative_to_root(root, canonical),
                target_relative=target_relative,
                parent_relative=parent_relative,
                zone_id=zone_id,
                original_sha256=original_sha,
                original_my_experience=original_blocks,
                original_body=original_body,
            )
        )

    for title in planned_titles:
        if sum(1 for item in resolved if item.action == "create" and item.op["title"] == title) != 1:
            raise IngestFailure("PLAN_DUPLICATE_TITLE", f"proposed card title is not unique: {title}")
    return resolved, [{"path": path, "sha256": digest} for path, digest in sorted(hash_checks.items())]


def metadata_for_create(operation: dict[str, Any], zone_id: str) -> dict[str, Any]:
    proposal = operation["metadata"]
    metadata: dict[str, Any] = {
        "type": proposal["type"],
        "zone": operation["classification"]["zone"],
        "zone_id": zone_id,
        "parent": f"[[{operation['parent']['moc_title']}]]",
        "tags": proposal["tags"],
        "sources": proposal["sources"],
    }
    if "domain" in proposal:
        metadata["domain"] = proposal["domain"]
    metadata.update({
        "risk_level": proposal["risk_level"],
        "confidence": proposal["confidence"],
        "created": proposal["created"],
        "updated": proposal["updated"],
    })
    return metadata


def insert_moc_link(text: str, heading: str, link_line: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    matching = [index for index, line in enumerate(lines) if line.strip() == heading.strip()]
    if len(matching) != 1:
        raise IngestFailure("MOC_HEADING_INVALID", f"MOC heading must exist exactly once: {heading!r}")
    if any(link_line.split("`")[0].strip() in line for line in lines):
        raise IngestFailure("MOC_LINK_DUPLICATE", f"MOC already contains the proposed link: {link_line}")
    start = matching[0]
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = [link_line, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def copy_staging_vault(root: Path, stage: Path, governance_root: Path) -> None:
    stage.mkdir(parents=True, exist_ok=False)
    shutil.copytree(root / "wiki", stage / "wiki")
    for name in ("_SYSTEM.md", "_registry.md", "_MOC_主干道.md"):
        source = root / name
        if source.is_file():
            shutil.copy2(source, stage / name)
    (stage / "_ops").mkdir()
    for name in ("governance_validate.py", "rebuild_registry.py", "frontmatter.py"):
        source = governance_root / name
        if not source.is_file():
            raise IngestFailure("GOVERNANCE_RUNTIME_MISSING", f"governance runtime file is missing: {source}")
        shutil.copy2(source, stage / "_ops" / name)


def write_stage_document(path: Path, data: bytes) -> None:
    if not path.parent.is_dir():
        raise IngestFailure(
            "BUSINESS_DIRECTORY_CREATION_FORBIDDEN",
            f"staging target parent does not already exist: {path.parent}",
        )
    atomic_write_bytes(path, data)


def apply_operations_to_stage(
    root: Path,
    stage: Path,
    resolved: list[ResolvedOperation],
) -> dict[str, str]:
    changed: dict[str, str] = {}
    moc_insertions: dict[str, list[ResolvedOperation]] = {}
    for item in resolved:
        target = stage / Path(item.target_relative)
        operation = item.op
        if item.action == "create":
            assert item.zone_id is not None
            data = render_document(metadata_for_create(operation, item.zone_id), operation["body"])
            write_stage_document(target, data)
            moc_insertions.setdefault(item.parent_relative, []).append(item)
        else:
            production_target = root / Path(item.target_relative)
            old_text, has_bom, newline = decode_document(production_target)
            metadata, _ = split_frontmatter(old_text)
            for key, value in operation["metadata_patch"].items():
                metadata[key] = value
            data = render_document(metadata, operation["body"], bom=has_bom, newline=newline)
            write_stage_document(target, data)
            staged_text, _, _ = decode_document(target)
            _, staged_body = split_frontmatter(staged_text)
            if extract_my_experience_blocks(staged_body) != item.original_my_experience:
                raise IngestFailure("MY_EXPERIENCE_CHANGED", f"staged enrichment changed MY-EXPERIENCE: {item.target_relative}")
        changed[item.target_relative] = sha256(target)

    for parent_relative, items in moc_insertions.items():
        target = stage / Path(parent_relative)
        text, has_bom, newline = decode_document(target)
        for item in items:
            tags = item.op["metadata"]["tags"]
            note = ", ".join(tags[:6])
            link = f"- [[{item.op['title']}]]`{item.zone_id}`"
            if note:
                link += f" — *{note}*"
            text = insert_moc_link(text, item.op["parent"]["heading"], link)
        data = text.replace("\n", newline).encode("utf-8")
        if has_bom:
            data = b"\xef\xbb\xbf" + data
        write_stage_document(target, data)
        changed[parent_relative] = sha256(target)
    return changed


def extract_wikilinks(text: str) -> set[str]:
    targets: set[str] = set()
    in_fence = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in WIKILINK_RE.finditer(line):
            target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            if target:
                targets.add(target)
    return targets


def validate_new_links(stage: Path, resolved: list[ResolvedOperation]) -> None:
    index = build_markdown_title_index(stage)
    for item in resolved:
        staged_text, _, _ = decode_document(stage / Path(item.target_relative))
        _, staged_body = split_frontmatter(staged_text)
        links = extract_wikilinks(staged_body)
        if item.action == "enrich":
            links -= extract_wikilinks(item.original_body)
        for target in sorted(links):
            if "/" in target or "\\" in target or target.casefold().endswith(".md"):
                raise IngestFailure(
                    "WIKILINK_NOT_PURE",
                    f"new Wikilink must use a pure title: {target!r}",
                    details={"path": item.target_relative},
                    exit_code=4,
                )
            try:
                resolve_unique_title(index, target, kind="wikilink")
            except CanonicalResolutionError as exc:
                raise IngestFailure(exc.code, str(exc), details=exc.details, exit_code=4) from exc
        if item.action == "create":
            moc_text, _, _ = decode_document(stage / Path(item.parent_relative))
            if not moc_link_present(moc_text, item.op["title"]):
                raise IngestFailure(
                    "MOC_MOUNT_MISSING",
                    f"staging MOC does not mount the new card: {item.op['title']}",
                    exit_code=4,
                )


def run_document_guard(stage: Path, changed_paths: Iterable[str], report_path: Path) -> dict[str, Any]:
    validator = stage / "_ops" / "governance_validate.py"
    if not validator.is_file():
        raise IngestFailure("VALIDATOR_MISSING", f"governance validator is missing in staging: {validator}")
    arguments = [
        sys.executable,
        str(validator),
        "--root",
        str(stage),
        "--document-guard",
        "--report",
        str(report_path),
        "--json",
    ]
    for relative in sorted(set(changed_paths)):
        arguments.extend(["--path", relative])
    result = run_process(arguments, cwd=stage)
    if result["returncode"] != 0:
        raise IngestFailure(
            "STAGING_GOVERNANCE_FAILED",
            f"governance validator exited with {result['returncode']}; commit is forbidden",
            details=result,
            exit_code=4,
        )
    payload = parse_json_output(result, name="governance_validate.py --document-guard")
    if payload.get("ok") is not True:
        raise IngestFailure(
            "STAGING_GOVERNANCE_FAILED",
            "governance validator returned ok=false; commit is forbidden",
            details=payload,
            exit_code=4,
        )
    return payload


def validate_staging(
    stage: Path,
    transaction_dir: Path,
    resolved: list[ResolvedOperation],
    changed: dict[str, str],
    plan: dict[str, Any],
    materials: list[Path],
) -> dict[str, Any]:
    first = transaction_dir / "registry-stage-1.md"
    second = transaction_dir / "registry-stage-2.md"
    staging_governance = stage / "_ops"
    payload = run_registry_rebuild(stage, first, staging_governance)
    run_registry_rebuild(stage, second, staging_governance)
    if sha256(first) != sha256(second):
        raise IngestFailure(
            "STAGING_REGISTRY_NONDETERMINISTIC",
            "two staging Registry rebuilds produced different hashes",
            exit_code=4,
        )
    atomic_write_bytes(stage / "_registry.md", first.read_bytes())
    validate_new_links(stage, resolved)
    guard = run_document_guard(stage, changed.keys(), transaction_dir / "document-guard.json")
    lossless = validate_lossless_preservation(stage, resolved, plan, materials)
    moc_capacity = []
    for relative in sorted({item.parent_relative for item in resolved if item.action == "create"}):
        text, _, _ = decode_document(stage / Path(relative))
        link_count = len(WIKILINK_RE.findall(text))
        moc_capacity.append({"path": relative, "links": link_count, "over_30": link_count > 30})
    return {
        "registry": payload,
        "registry_sha256": sha256(first),
        "document_guard": guard,
        "lossless_preservation": lossless,
        "moc_capacity": moc_capacity,
        "changed": changed,
    }


def verify_hash_checks(root: Path, checks: list[dict[str, str]]) -> None:
    for check in checks:
        path = root / Path(check["path"])
        if not path.is_file():
            raise IngestFailure("BASE_DOCUMENT_MISSING", f"hash-pinned document is missing: {check['path']}")
        actual = sha256(path)
        if actual != check["sha256"].upper():
            raise IngestFailure(
                "BASE_DOCUMENT_CHANGED",
                f"hash-pinned document changed before commit: {check['path']}",
                details={"expected": check["sha256"].upper(), "actual": actual},
            )


def verify_precommit(
    root: Path,
    plan: dict[str, Any],
    transaction_dir: Path,
    resolved: list[ResolvedOperation],
    hash_checks: list[dict[str, str]],
    governance_root: Path,
) -> None:
    entries = parse_registry(root / "_registry.md")
    used = {entry.zone_id for entry in entries}
    conflicts = [item.zone_id for item in resolved if item.action == "create" and item.zone_id in used]
    if conflicts:
        raise IngestFailure(
            "ZONE_ID_CONFLICT",
            "a reserved zone_id was occupied before commit",
            details=sorted(conflicts),
        )
    verify_base_hashes(root, plan)
    verify_materials(plan)
    verify_hash_checks(root, hash_checks)
    for item in resolved:
        if item.action == "create" and (root / Path(item.target_relative)).exists():
            raise IngestFailure("CARD_PATH_EXISTS", f"new target appeared before commit: {item.target_relative}")
        classification = item.op["classification"]
        try:
            current = resolve_canonical_directory(root, classification["zone"], classification["number"], entries)
        except CanonicalResolutionError as exc:
            raise IngestFailure(exc.code, str(exc), details=exc.details) from exc
        if relative_to_root(root, current) != item.canonical_relative:
            raise IngestFailure(
                "CANONICAL_DIRECTORY_CHANGED",
                f"canonical directory changed before commit: {item.op['op_id']}",
            )
    verify_registry_current(root, transaction_dir, governance_root, suffix="precommit")


def create_backup(
    root: Path,
    transaction_dir: Path,
    relative_paths: Iterable[str],
) -> dict[str, Any]:
    backup = transaction_dir / "backup"
    data_root = backup / "data"
    data_root.mkdir(parents=True, exist_ok=False)
    manifest: list[dict[str, Any]] = []
    for relative in sorted(set(relative_paths) | {"_registry.md"}):
        source = (root / Path(relative)).resolve()
        if not is_within(root, source):
            raise IngestFailure("BACKUP_PATH_ESCAPE", f"backup target escapes the Vault: {relative}")
        existed = source.is_file()
        item: dict[str, Any] = {"path": relative, "existed": existed, "sha256": sha256(source) if existed else None}
        if existed:
            destination = data_root / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        manifest.append(item)
    payload = {"created_at": iso(), "files": manifest}
    atomic_write_json(backup / "manifest.json", payload)
    return payload


def rollback_from_backup(root: Path, transaction_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    restored: list[str] = []
    removed: list[str] = []
    errors: list[str] = []
    data_root = transaction_dir / "backup" / "data"
    for item in reversed(manifest["files"]):
        relative = item["path"]
        target = (root / Path(relative)).resolve()
        if not is_within(root, target):
            errors.append(f"path escaped Vault during rollback: {relative}")
            continue
        try:
            if item["existed"]:
                backup = data_root / Path(relative)
                atomic_write_bytes(target, backup.read_bytes())
                restored.append(relative)
            elif target.exists():
                if not target.is_file():
                    raise RuntimeError("rollback target is not a file")
                target.unlink()
                removed.append(relative)
        except BaseException as exc:
            errors.append(f"{relative}: {exc}")

    mismatches: list[dict[str, Any]] = []
    for item in manifest["files"]:
        target = root / Path(item["path"])
        if item["existed"]:
            actual = sha256(target) if target.is_file() else None
            if actual != item["sha256"]:
                mismatches.append({"path": item["path"], "expected": item["sha256"], "actual": actual})
        elif target.exists():
            mismatches.append({"path": item["path"], "expected": None, "actual": "still_exists"})
    return {
        "ok": not errors and not mismatches,
        "restored": restored,
        "removed": removed,
        "errors": errors,
        "mismatches": mismatches,
    }


def verify_postcommit(
    root: Path,
    transaction_dir: Path,
    stage: Path,
    changed: dict[str, str],
    resolved: list[ResolvedOperation],
    governance_root: Path,
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for relative, expected in sorted(changed.items()):
        target = root / Path(relative)
        actual = sha256(target) if target.is_file() else None
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
    if mismatches:
        raise IngestFailure("POSTCOMMIT_HASH_MISMATCH", "committed files differ from validated staging", details=mismatches, exit_code=5)

    my_experience: list[dict[str, Any]] = []
    for item in resolved:
        if item.action != "enrich":
            continue
        text, _, _ = decode_document(root / Path(item.target_relative))
        _, body = split_frontmatter(text)
        actual = extract_my_experience_blocks(body)
        ok = actual == item.original_my_experience
        my_experience.append({"path": item.target_relative, "ok": ok, "sha256_before": item.original_sha256})
        if not ok:
            raise IngestFailure(
                "MY_EXPERIENCE_POSTCOMMIT_CHANGED",
                f"post-commit MY-EXPERIENCE verification failed: {item.target_relative}",
                exit_code=5,
            )
    registry = verify_registry_current(root, transaction_dir, governance_root, suffix="postcommit")
    if registry["sha256"] != sha256(stage / "_registry.md"):
        raise IngestFailure(
            "POSTCOMMIT_REGISTRY_MISMATCH",
            "committed Registry differs from validated staging",
            exit_code=5,
        )
    return {"changed_files": len(changed), "my_experience": my_experience, "registry": registry}


def run_index_commands(cwd: Path, commands: list[list[str]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for command in commands:
        result = run_process(command, cwd=cwd)
        captured = {
            "command": command,
            "returncode": result["returncode"],
            "stdout_tail": result["stdout"][-20000:],
            "stderr_tail": result["stderr"][-20000:],
        }
        results.append(captured)
        if result["returncode"] != 0:
            return {"ok": False, "index_pending": True, "results": results}
    return {"ok": True, "index_pending": False, "results": results}


class Journal:
    def __init__(self, path: Path, initial: dict[str, Any]):
        self.path = path
        self.data = initial
        self.write()

    def write(self) -> None:
        self.data["updated_at"] = iso()
        atomic_write_json(self.path, self.data)

    def event(self, name: str, **details: Any) -> None:
        self.data.setdefault("events", []).append({"at": iso(), "event": name, **details})
        self.write()


class IngestExecutor:
    def __init__(self, options: ExecutionOptions):
        self.options = options
        self.root = options.root.resolve()
        self.plan_path = options.plan_path.resolve()
        self.work_root = options.work_root.resolve()
        self.governance_root = options.governance_root.resolve()
        self.plan: dict[str, Any] = {}
        self.transaction_dir: Path | None = None
        self.stage: Path | None = None
        self.journal: Journal | None = None
        self.lock_path: Path | None = None
        self.lock_owner: dict[str, Any] | None = None
        self.commit_started = False
        self.commit_complete = False
        self.backup_manifest: dict[str, Any] | None = None

    def _hook(self, event: str, **details: Any) -> None:
        if self.options.test_hook is not None:
            self.options.test_hook(event, details)

    def _release(self) -> None:
        if self.lock_path is not None and self.lock_owner is not None:
            release_lock(self.lock_path, self.lock_owner)
            if self.journal is not None:
                self.journal.event("lock_released")
            self.lock_owner = None

    def _prepare(self) -> None:
        if not self.root.is_dir():
            raise IngestFailure("VAULT_MISSING", f"Vault root does not exist: {self.root}")
        if self.options.device.upper() not in {"A", "B"}:
            raise IngestFailure("DEVICE_FORBIDDEN", "Ingest writes require Device A or B")
        actual_hostname = socket.gethostname()
        if self.options.hostname.casefold() != actual_hostname.casefold():
            raise IngestFailure(
                "PROFILE_HOST_MISMATCH",
                "execution options belong to a different host",
                details={"profile": self.options.hostname, "actual": actual_hostname},
            )
        if not self.options.sync_confirmed:
            raise IngestFailure("SYNC_CONFIRMATION_REQUIRED", "explicit sync/other-device confirmation is required")
        if not self.plan_path.is_file():
            raise IngestFailure("PLAN_MISSING", f"ingest plan is missing: {self.plan_path}")
        if is_within(self.root, self.plan_path):
            raise IngestFailure("PLAN_INSIDE_VAULT", "ingest plan must be outside the Vault")
        if self.options.profile_path is not None and is_within(self.root, self.options.profile_path.resolve()):
            raise IngestFailure("PROFILE_INSIDE_VAULT", "device profile must be outside the Vault")
        if is_within(self.root, self.work_root) or is_within(self.work_root, self.root):
            raise IngestFailure("WORK_ROOT_OVERLAPS_VAULT", "staging/journal work root must not overlap the Vault")
        if not is_within(self.work_root, self.plan_path):
            raise IngestFailure("PLAN_OUTSIDE_WORK_ROOT", "ingest plan must be stored under the profile work_root")
        if is_within(self.root, self.governance_root) or is_within(self.governance_root, self.root):
            raise IngestFailure("GOVERNANCE_RUNTIME_OVERLAPS_VAULT", "governance runtime must not overlap the Vault")
        for name in ("governance_validate.py", "rebuild_registry.py", "frontmatter.py"):
            runtime_file = self.governance_root / name
            if not runtime_file.is_file():
                raise IngestFailure("GOVERNANCE_RUNTIME_MISSING", f"governance runtime file is missing: {runtime_file}")
        self.plan = load_json(self.plan_path)
        validate_plan(self.plan)
        reviews = [operation for operation in self.plan["operations"] if operation["action"] == "needs_governance_review"]
        if reviews:
            raise IngestFailure(
                "NEEDS_GOVERNANCE_REVIEW",
                "plan contains a card that cannot be mounted to an existing MOC",
                details=reviews,
                exit_code=3,
            )
        self.work_root.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%dT%H%M%S%z")
        self.transaction_dir = self.work_root / f"{self.plan['plan_id']}-{stamp}-{os.getpid()}"
        self.transaction_dir.mkdir(exist_ok=False)
        shutil.copy2(self.plan_path, self.transaction_dir / "ingest_plan.json")
        self.stage = self.transaction_dir / "staging"
        self.journal = Journal(
            self.transaction_dir / "journal.json",
            {
                "schema_version": "1.0",
                "plan_id": self.plan["plan_id"],
                "plan_sha256": sha256(self.plan_path),
                "vault_root": str(self.root),
                "device": self.options.device.upper(),
                "status": "PREPARING",
                "created_at": iso(),
                "index_pending": None,
                "events": [],
            },
        )

    def run(self) -> dict[str, Any]:
        self._prepare()
        assert self.transaction_dir is not None and self.stage is not None and self.journal is not None
        resolved: list[ResolvedOperation] = []
        changed: dict[str, str] = {}
        hash_checks: list[dict[str, str]] = []
        try:
            conflicts = conflict_files(self.root)
            if conflicts:
                raise IngestFailure("SYNC_CONFLICT_FILES", "Nutstore conflict files block Ingest", details=conflicts)

            self.lock_path, self.lock_owner = acquire_lock(
                self.root,
                self.options.device.upper(),
                self.plan["plan_id"],
                self.options.resume,
                self.transaction_dir,
            )
            self.journal.event("lock_acquired", owner=self.lock_owner)
            self.journal.data["status"] = "LOCKED_REVALIDATING"
            self.journal.write()

            base = verify_base_hashes(self.root, self.plan)
            materials = verify_materials(self.plan)
            registry_check = verify_registry_current(
                self.root,
                self.transaction_dir,
                self.governance_root,
                suffix="locked",
            )
            entries = parse_registry(self.root / "_registry.md")
            reservations = reserve_zone_ids(entries, self.plan["operations"])
            resolved, hash_checks = resolve_operations(self.root, self.plan, entries, reservations)
            self.journal.data.update({
                "base": base,
                "materials": materials,
                "reservations": reservations,
                "resolved_operations": [
                    {
                        "op_id": item.op["op_id"],
                        "action": item.action,
                        "canonical_directory": item.canonical_relative,
                        "target": item.target_relative,
                        "parent_moc": item.parent_relative,
                        "zone_id": item.zone_id,
                        "original_sha256": item.original_sha256,
                        "my_experience_before": item.original_my_experience,
                    }
                    for item in resolved
                ],
                "hash_checks": hash_checks,
                "locked_registry_check": registry_check,
                "status": "STAGING",
            })
            self.journal.write()

            copy_staging_vault(self.root, self.stage, self.governance_root)
            changed = apply_operations_to_stage(self.root, self.stage, resolved)
            staging_validation = validate_staging(self.stage, self.transaction_dir, resolved, changed, self.plan, materials)
            self.journal.data["staging_validation"] = staging_validation
            self.journal.data["status"] = "STAGING_VALIDATED"
            self.journal.write()
            self._hook(
                "after_staging",
                root=self.root,
                transaction_dir=self.transaction_dir,
                reservations=reservations,
                resolved=resolved,
            )

            verify_precommit(
                self.root,
                self.plan,
                self.transaction_dir,
                resolved,
                hash_checks,
                self.governance_root,
            )
            self.backup_manifest = create_backup(self.root, self.transaction_dir, changed.keys())
            self.journal.data["backup"] = self.backup_manifest
            self.journal.data["status"] = "COMMITTING"
            self.journal.write()
            self.commit_started = True

            committed_files: list[str] = []
            for relative in sorted(changed):
                source = self.stage / Path(relative)
                target = self.root / Path(relative)
                atomic_write_bytes(target, source.read_bytes())
                committed_files.append(relative)
                self.journal.event("file_committed", path=relative, sha256=sha256(target))
                self._hook("after_file_commit", root=self.root, path=relative, count=len(committed_files))
                if "commit_after_first" in self.options.failpoints and len(committed_files) == 1:
                    raise IngestFailure("TEST_COMMIT_INTERRUPTED", "injected failure after first committed file", exit_code=5)

            if "registry_rebuild" in self.options.failpoints:
                raise IngestFailure("REGISTRY_REBUILD_FAILED", "injected Registry rebuild failure", exit_code=5)
            registry_candidate = self.transaction_dir / "registry-production-candidate.md"
            registry_payload = run_registry_rebuild(self.root, registry_candidate, self.governance_root)
            if sha256(registry_candidate) != sha256(self.stage / "_registry.md"):
                raise IngestFailure(
                    "REGISTRY_TARGET_MISMATCH",
                    "production Registry rebuild differs from validated staging",
                    details={
                        "production": sha256(registry_candidate),
                        "staging": sha256(self.stage / "_registry.md"),
                    },
                    exit_code=5,
                )
            atomic_write_bytes(self.root / "_registry.md", registry_candidate.read_bytes())
            self.journal.event("registry_committed", sha256=sha256(self.root / "_registry.md"), payload=registry_payload)
            postcommit = verify_postcommit(
                self.root,
                self.transaction_dir,
                self.stage,
                changed,
                resolved,
                self.governance_root,
            )
            self.commit_complete = True
            self.journal.data.update({
                "status": "COMMITTED",
                "committed_files": committed_files,
                "postcommit": postcommit,
            })
            self.journal.write()

            self._release()
            if self.options.skip_index:
                index = {"ok": True, "index_pending": False, "skipped": True, "results": []}
            else:
                commands = self.options.index_commands or [
                    ["gbrain", "import", str(self.root / "wiki"), "--no-embed"],
                    ["gbrain", "embed", "--stale"],
                ]
                index = run_index_commands(self.work_root, commands)
            self.journal.data["index"] = index
            self.journal.data["index_pending"] = index["index_pending"]
            self.journal.data["status"] = "COMMITTED_INDEX_PENDING" if index["index_pending"] else "COMPLETE"
            self.journal.write()
            return {
                "ok": True,
                "status": self.journal.data["status"],
                "plan_id": self.plan["plan_id"],
                "journal": str(self.journal.path),
                "committed_files": committed_files,
                "reservations": reservations,
                "moc_capacity": staging_validation["moc_capacity"],
                "lossless_preservation": staging_validation["lossless_preservation"],
                "index_pending": index["index_pending"],
                "exit_code": 0,
            }
        except IngestFailure as failure:
            rollback: dict[str, Any] | None = None
            status = "FAILED_BEFORE_COMMIT"
            exit_code = failure.exit_code
            if self.commit_started and not self.commit_complete and self.backup_manifest is not None:
                rollback = rollback_from_backup(self.root, self.transaction_dir, self.backup_manifest)
                status = "ROLLED_BACK" if rollback["ok"] else "RECOVERY_REQUIRED"
                exit_code = 5 if rollback["ok"] else 6
            self.journal.data.update({
                "status": status,
                "failure": failure.as_dict(),
                "rollback": rollback,
                "index_pending": None,
            })
            self.journal.write()
            return {
                "ok": False,
                "status": status,
                "failure": failure.as_dict(),
                "rollback": rollback,
                "journal": str(self.journal.path),
                "exit_code": exit_code,
            }
        except BaseException as exc:
            rollback = None
            status = "FAILED_BEFORE_COMMIT"
            exit_code = 1
            if self.commit_started and not self.commit_complete and self.backup_manifest is not None:
                rollback = rollback_from_backup(self.root, self.transaction_dir, self.backup_manifest)
                status = "ROLLED_BACK" if rollback["ok"] else "RECOVERY_REQUIRED"
                exit_code = 5 if rollback["ok"] else 6
            failure = IngestFailure("INTERNAL_ERROR", str(exc), details={"type": type(exc).__name__}, exit_code=exit_code)
            self.journal.data.update({"status": status, "failure": failure.as_dict(), "rollback": rollback})
            self.journal.write()
            return {
                "ok": False,
                "status": status,
                "failure": failure.as_dict(),
                "rollback": rollback,
                "journal": str(self.journal.path),
                "exit_code": exit_code,
            }
        finally:
            self._release()


def execute_plan(options: ExecutionOptions) -> dict[str, Any]:
    try:
        return IngestExecutor(options).run()
    except IngestFailure as failure:
        return {
            "ok": False,
            "status": "REJECTED",
            "failure": failure.as_dict(),
            "journal": None,
            "exit_code": failure.exit_code,
        }
    except BaseException as exc:
        return {
            "ok": False,
            "status": "REJECTED",
            "failure": {"code": "INTERNAL_ERROR", "message": str(exc), "details": {"type": type(exc).__name__}},
            "journal": None,
            "exit_code": 1,
        }



def normalize_anchor_text(text: str) -> str:
    """Standard normalization for anchor extraction and matching.
    1. Standardize newlines (\r\n -> \n).
    2. Strip leading/trailing whitespace per line, collapse inline whitespace.
    3. Normalize markdown tables (strip padding spaces around | and ignore separator lines).
    4. Strip callout '>' markers.
    """
    if not text:
        return ""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = []
    for line in text.split('\n'):
        line_clean = line.strip()
        if line_clean.startswith('>'):
            line_clean = line_clean[1:].strip()
        if line_clean.startswith('|') and line_clean.endswith('|'):
            if re.match(r'^\|[\s:\|-]+\|$', line_clean):
                continue
            cells = [c.strip() for c in line_clean.split('|')]
            line_clean = '|' + '|'.join(cells[1:-1]) + '|'
        else:
            line_clean = re.sub(r'[ \t]+', ' ', line_clean)
        if line_clean:
            lines.append(line_clean)
    return '\n'.join(lines)


def extract_anchors(raw_text: str) -> list[dict[str, Any]]:
    """Extract code blocks, tables, and prompt templates from raw material."""
    anchors: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    # 1. Code blocks (```...```)
    code_pattern = re.compile(r'```[a-zA-Z0-9_-]*\n(.*?)```', re.DOTALL)
    for match in code_pattern.finditer(raw_text):
        raw_code = match.group(0)
        norm_code = normalize_anchor_text(raw_code)
        if norm_code and len(norm_code) > 15:
            code_hash = hashlib.sha256(norm_code.encode('utf-8')).hexdigest()
            if code_hash not in seen_hashes:
                seen_hashes.add(code_hash)
                anchors.append({
                    "anchor_type": "code_block",
                    "snippet_hash": code_hash,
                    "normalized_text": norm_code,
                    "preview": raw_code[:120].strip() + ("..." if len(raw_code) > 120 else "")
                })

    # 2. Markdown tables (|...|\n|...|)
    table_pattern = re.compile(r'(?:^\|.*\|\n?){2,}', re.MULTILINE)
    for match in table_pattern.finditer(raw_text):
        raw_table = match.group(0)
        norm_table = normalize_anchor_text(raw_table)
        if norm_table and len(norm_table) > 15:
            table_hash = hashlib.sha256(norm_table.encode('utf-8')).hexdigest()
            if table_hash not in seen_hashes:
                seen_hashes.add(table_hash)
                anchors.append({
                    "anchor_type": "table",
                    "snippet_hash": table_hash,
                    "normalized_text": norm_table,
                    "preview": raw_table[:120].strip() + ("..." if len(raw_table) > 120 else "")
                })

    # 3. Callout / Prompt templates (> ... or ```...``` prompt)
    prompt_pattern = re.compile(r'(?:^>.*(?:\n>.*)*)', re.MULTILINE)
    for match in prompt_pattern.finditer(raw_text):
        raw_prompt = match.group(0)
        norm_prompt = normalize_anchor_text(raw_prompt)
        folded_prompt = norm_prompt.casefold()
        if norm_prompt and len(norm_prompt) > 30 and any(hint in folded_prompt for hint in PROMPT_ANCHOR_HINTS):
            prompt_hash = hashlib.sha256(norm_prompt.encode('utf-8')).hexdigest()
            if prompt_hash not in seen_hashes:
                seen_hashes.add(prompt_hash)
                anchors.append({
                    "anchor_type": "prompt_template",
                    "snippet_hash": prompt_hash,
                    "normalized_text": norm_prompt,
                    "preview": raw_prompt[:120].strip() + ("..." if len(raw_prompt) > 120 else "")
                })

    return anchors


def validate_lossless_preservation(
    stage: Path,
    resolved: list[ResolvedOperation],
    plan: dict[str, Any],
    materials: list[Path],
) -> dict[str, Any]:
    """Verify that all raw material anchors appear as substrings in staged card bodies,
    or are explicitly exempted via omitted_anchors in the plan.
    """
    raw_anchors: list[dict[str, Any]] = []
    for mat_item in materials:
        if isinstance(mat_item, dict):
            loc = mat_item.get("path") or mat_item.get("local_path")
            mat = Path(loc) if loc else None
        elif isinstance(mat_item, (str, Path)):
            mat = Path(mat_item)
        else:
            mat = None
        if mat and mat.is_file():
            text = read_text(mat)
            raw_anchors.extend(extract_anchors(text))

    raw_identities = {
        (a["anchor_type"], a["snippet_hash"].lower())
        for a in raw_anchors
    }

    declared_identities: set[tuple[str, str]] = set()
    declared_omitted: dict[tuple[str, str], str] = {}

    for op in plan.get("operations", []):
        for omitted in op.get("omitted_anchors", []):
            identity = (
                omitted["anchor_type"],
                omitted["snippet_hash"].lower(),
            )

            if identity not in raw_identities:
                raise IngestFailure(
                    "OMITTED_ANCHOR_UNKNOWN",
                    f"omitted_anchor does not correspond to a raw-material anchor: {identity}",
                    exit_code=4,
                )

            if identity in declared_identities:
                raise IngestFailure(
                    "OMITTED_ANCHOR_DUPLICATE",
                    f"same anchor exemption declared more than once: {identity}",
                    exit_code=4,
                )

            declared_identities.add(identity)
            declared_omitted[identity] = omitted.get("rationale", "")

    if not raw_anchors:
        return {"ok": True, "anchors_total": 0, "exempted": 0, "missing_unexempted": 0}

    # Collect normalized staged card bodies
    staged_normalized_texts: list[str] = []
    for item in resolved:
        target_path = stage / Path(item.target_relative)
        if target_path.is_file():
            staged_text, _, _ = decode_document(target_path)
            _, staged_body = split_frontmatter(staged_text)
            staged_normalized_texts.append(normalize_anchor_text(staged_body))

    missing_anchors: list[dict[str, Any]] = []
    used_exemptions: set[tuple[str, str]] = set()

    for anchor in raw_anchors:
        norm_anchor = anchor["normalized_text"]
        identity = (
            anchor["anchor_type"],
            anchor["snippet_hash"].lower(),
        )

        # Substring inclusion match check across all staged card bodies
        matched = any(norm_anchor in norm_staged for norm_staged in staged_normalized_texts)
        if matched:
            continue

        # Check exemption
        if identity in declared_omitted:
            rationale = declared_omitted[identity]
            if len(rationale) < 10:
                raise IngestFailure(
                    "OMITTED_ANCHOR_RATIONALE_TOO_SHORT",
                    f"omitted_anchor rationale must be >= 10 chars for identity {identity}",
                    details={"identity": identity, "rationale": rationale},
                    exit_code=4,
                )
            used_exemptions.add(identity)
            continue

        missing_anchors.append(anchor)

    if missing_anchors:
        previews = [m["preview"] for m in missing_anchors]
        raise IngestFailure(
            "LOSSLESS_PRESERVATION_FAILED",
            f"Raw material anchors ({len(missing_anchors)}) missing from staging cards without omitted_anchors exemption: {previews!r}",
            details={"missing": missing_anchors},
            exit_code=4,
        )

    return {
        "ok": True,
        "anchors_total": len(raw_anchors),
        "exempted": len(used_exemptions),
        "missing_unexempted": 0,
    }

def snapshot_payload(root: Path, materials: list[Path], extract_anchors_flag: bool = False) -> dict[str, Any]:
    """Return plan base hashes plus a context-compact AI-facing anchor manifest.

    Full normalized anchor text is intentionally kept internal to deterministic
    lossless validation. The AI only needs identity + a short preview to decide
    whether a static asset should be preserved or explicitly omitted.
    """
    root = root.resolve()
    base = {
        "registry_sha256": sha256(root / "_registry.md"),
        "system_sha256": sha256(root / "_SYSTEM.md"),
        "materials": [],
    }
    anchors: list[dict[str, Any]] = []
    for path in materials:
        path = path.resolve()
        if not path.is_file():
            raise IngestFailure("MATERIAL_MISSING", f"raw material is missing: {path}")
        base["materials"].append({"uri": path.as_uri(), "local_path": str(path), "sha256": sha256(path)})
        if extract_anchors_flag:
            raw_text = read_text(path)
            for anchor in extract_anchors(raw_text):
                anchors.append({
                    "anchor_type": anchor["anchor_type"],
                    "snippet_hash": anchor["snippet_hash"],
                    "preview": anchor["preview"],
                })
    payload: dict[str, Any] = {"base": base}
    if extract_anchors_flag:
        payload["anchors"] = anchors
    return payload


def retry_index(profile: DeviceProfile, journal_path: Path) -> dict[str, Any]:
    journal_path = journal_path.resolve()
    if not is_within(profile.work_root, journal_path):
        raise IngestFailure("JOURNAL_OUTSIDE_WORK_ROOT", "index retry journal must be under the profile work_root")
    journal = load_json(journal_path)
    if journal.get("status") not in {"COMMITTED_INDEX_PENDING", "COMPLETE"}:
        raise IngestFailure("JOURNAL_NOT_COMMITTED", "index retry requires a committed journal")
    root = Path(str(journal["vault_root"])).resolve()
    if root != profile.vault_root:
        raise IngestFailure(
            "JOURNAL_PROFILE_MISMATCH",
            "journal Vault does not match the active device profile",
            details={"journal": str(root), "profile": str(profile.vault_root)},
        )
    index = run_index_commands(
        profile.work_root,
        [
            ["gbrain", "import", str(root / "wiki"), "--no-embed"],
            ["gbrain", "embed", "--stale"],
        ],
    )
    journal["index"] = index
    journal["index_pending"] = index["index_pending"]
    journal["status"] = "COMMITTED_INDEX_PENDING" if index["index_pending"] else "COMPLETE"
    journal.setdefault("events", []).append({"at": iso(), "event": "index_retry", "ok": index["ok"]})
    atomic_write_json(journal_path, journal)
    return {"ok": index["ok"], "status": journal["status"], "index_pending": index["index_pending"], "journal": str(journal_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic Leon's Wiki Ingest R3 executor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="capture plan base hashes without writing the Vault")
    snapshot_parser.add_argument("--profile", type=Path, required=True)
    snapshot_parser.add_argument("--material", type=Path, action="append", required=True)
    snapshot_parser.add_argument("--out", type=Path, required=True)
    snapshot_parser.add_argument("--anchors", action="store_true", help="Extract anchors snapshot")

    validate_parser = subparsers.add_parser("validate-plan", help="validate untrusted ingest_plan.json")
    validate_parser.add_argument("--profile", type=Path, required=True)
    validate_parser.add_argument("--plan", type=Path, required=True)

    execute_parser = subparsers.add_parser("execute", help="validate, stage, commit, and index one Ingest plan")
    execute_parser.add_argument("--profile", type=Path, required=True)
    execute_parser.add_argument("--plan", type=Path, required=True)
    execute_parser.add_argument("--sync-confirmed", action="store_true", required=True)
    execute_parser.add_argument("--resume", action="store_true")
    execute_parser.add_argument("--skip-index", action="store_true")

    retry_parser = subparsers.add_parser("retry-index", help="retry only gbrain import + stale embeddings")
    retry_parser.add_argument("--profile", type=Path, required=True)
    retry_parser.add_argument("--journal", type=Path, required=True)

    args = parser.parse_args()
    try:
        profile = load_device_profile(args.profile)
        if args.command == "snapshot":
            output = args.out.resolve()
            if not is_within(profile.work_root, output):
                raise IngestFailure("SNAPSHOT_OUTSIDE_WORK_ROOT", "snapshot output must be under the profile work_root")
            payload = snapshot_payload(
                profile.vault_root,
                args.material,
                extract_anchors_flag=getattr(args, "anchors", False),
            )
            result = {"ok": True, **payload}
            atomic_write_json(output, result)
            print(json.dumps({"ok": True, "out": str(output), "anchors": len(payload.get("anchors", []))}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate-plan":
            plan_path = args.plan.resolve()
            if not is_within(profile.work_root, plan_path):
                raise IngestFailure("PLAN_OUTSIDE_WORK_ROOT", "ingest plan must be stored under the profile work_root")
            plan = load_json(plan_path)
            validate_plan(plan)
            print(json.dumps({"ok": True, "plan_id": plan["plan_id"]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "retry-index":
            result = retry_index(profile, args.journal)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        result = execute_plan(options_from_profile(
            profile,
            args.plan,
            sync_confirmed=args.sync_confirmed,
            resume=args.resume,
            skip_index=args.skip_index,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result["exit_code"])
    except IngestFailure as failure:
        print(json.dumps({"ok": False, "failure": failure.as_dict()}, ensure_ascii=False, indent=2))
        return failure.exit_code
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(json.dumps({"ok": False, "failure": {"code": "RUNTIME_ERROR", "message": str(exc)}}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
