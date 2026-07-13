#!/usr/bin/env python3
"""Deterministic project state operations for AI Project Studio."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import stat
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
DELIVERABLE_CONTRACT_VERSION = 1
STUDIO_DIR = "studio"
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
PROFILE_DIR = ASSET_DIR / "profiles"
TEMPLATE_DIR = ASSET_DIR / "project-templates"
CORE_REQUIRED_FILES = [
    "PROJECT.md",
    "ROADMAP.md",
    "DECISIONS.md",
    "BACKLOG.md",
    "specs/README.md",
    "reviews/README.md",
]
VALID_GATE_STATES = {"pending", "complete"}
VALID_WORK_STATES = {
    "draft",
    "proposed",
    "approved",
    "in_progress",
    "review",
    "done",
    "rejected",
    "cancelled",
}
VALID_FIDELITIES = {
    "exploratory",
    "placeholder",
    "prototype",
    "vertical-slice",
    "production",
    "final-in-context",
}
IN_FLIGHT_WORK_STATES = {"in_progress", "review"}
DELIVERABLE_CONTRACT_MARKER = "<!-- studio:deliverable-contract -->"
WORK_TRANSITIONS = {
    "draft": {"proposed", "cancelled"},
    "proposed": {"approved", "rejected", "cancelled"},
    "approved": {"proposed", "in_progress", "cancelled"},
    "in_progress": {"proposed", "review", "cancelled"},
    "review": {"proposed", "in_progress", "done", "cancelled"},
    "rejected": {"draft", "cancelled"},
    "done": set(),
    "cancelled": set(),
}
MANAGED_START = "<!-- ai-project-studio:start -->"
MANAGED_END = "<!-- ai-project-studio:end -->"
AGENTS_BLOCK = f"""{MANAGED_START}
## AI Project Studio

- 回答问题时只回答，不因问题中出现可执行想法而自动修改项目。
- 任何项目判断或变更前使用 `$manage-project-studio` 中的 `scripts/studio.py validate` 和 `brief`，再按 brief 指向渐进读取；项目事实以仓库为准，不依赖聊天或 Subagent 记忆。
- AI 是制作人入口；用户确认产品方向、目标用户、成功标准、审美、范围、成本和发布，AI 自行决定已批准范围内低风险、可逆的实现细节。
- 重要信息不明确时只问当前最关键的问题；发现用户误解时明确纠正；更优方案会改变已批准方向时先说明影响并等待确认。
- 推荐工作前说明当前阶段、事实、未知、当前阶段盲区，以及交付物的完成度、用途、能证明和不能证明的内容。
- 只实现当前 Phase Run 中状态为 approved 或 in_progress、且规格哈希仍有效的工作项；聊天中的批准不能替代持久状态。
- Subagent 是临时专项执行者；委派时必须提供项目根目录和工作项 ID，并要求其使用该 Skill 运行 `validate`、`brief --item`、读取规格后再行动；制作人负责最终审查和 Studio 状态变更。
- 一次问题不得触发新增流程、文档、角色或检查表；实现后先验证并提供证据，再更新状态和推荐唯一下一步。
{MANAGED_END}
"""


class StudioError(RuntimeError):
    pass


def require_text(value: str | None, label: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise StudioError(f"{label} must not be empty")
    return text


def require_optional_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def target_from_args(args: argparse.Namespace) -> Path:
    target = args.target
    return target if isinstance(target, Path) else Path(target).expanduser().resolve()


@contextmanager
def project_lock(target: Path):
    if os.name == "posix":
        lock_root = Path("/tmp") / f"ai-project-studio-{os.getuid()}-locks"
    else:
        lock_root = Path(tempfile.gettempdir()) / "ai-project-studio-locks"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if lock_root.is_symlink():
        raise StudioError(f"Lock directory must not be a symbolic link: {lock_root}")
    if os.name == "posix" and lock_root.stat().st_uid != os.getuid():
        raise StudioError(f"Lock directory is owned by another user: {lock_root}")
    identity = unicodedata.normalize("NFC", str(target))
    if os.name == "nt" or sys.platform == "darwin":
        identity = identity.casefold()
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    lock_path = lock_root / f"{key}.lock"
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_regular_bytes(path: Path) -> bytes:
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise StudioError(f"Missing file: {path}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise StudioError(f"Refusing to read symbolic link: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise StudioError(f"Refusing to read non-regular file: {path}")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or not os.path.samestat(before, after):
            raise StudioError(f"File changed while opening: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            return handle.read()
    except OSError as exc:
        raise StudioError(f"Cannot safely read {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_text_exact(path: Path) -> str:
    return read_regular_bytes(path).decode("utf-8")


def detect_newline(content: str) -> str:
    return "\r\n" if content.count("\r\n") > content.count("\n") - content.count("\r\n") else "\n"


def default_file_mode() -> int:
    current_umask = os.umask(0o077)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def atomic_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise StudioError(f"Refusing to replace symbolic link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else default_file_mode()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as handle:
            handle.write(content)
            temporary_name = handle.name
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_user_text(path: Path, content: str) -> None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        atomic_write(path, content)
        return
    if stat.S_ISLNK(before.st_mode):
        raise StudioError(f"Refusing to update symbolic link: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise StudioError(f"Refusing to update non-regular user file: {path}")
    if before.st_nlink > 1:
        raise StudioError(f"Refusing to update hard-linked user file: {path}")

    replacement = content.encode("utf-8")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or not os.path.samestat(before, after):
            raise StudioError(f"User file changed while opening: {path}")
        if after.st_nlink > 1:
            raise StudioError(f"Refusing to update hard-linked user file: {path}")
        with os.fdopen(descriptor, "r+b", buffering=0) as handle:
            descriptor = None
            original = handle.read()

            def overwrite(data: bytes) -> None:
                handle.seek(0)
                view = memoryview(data)
                while view:
                    written = handle.write(view)
                    if not written:
                        raise OSError(f"Could not finish writing {path}")
                    view = view[written:]
                handle.truncate()
                os.fsync(handle.fileno())

            try:
                overwrite(replacement)
            except Exception:
                try:
                    overwrite(original)
                except Exception:
                    pass
                raise
    except OSError as exc:
        raise StudioError(f"Cannot safely update {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def reject_json_constant(value: str) -> None:
    raise StudioError(f"Invalid non-finite JSON number: {value}")


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StudioError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StudioError(f"JSON number is outside the finite float range: {value}")
    return parsed


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(
            read_text_exact(path),
            parse_constant=reject_json_constant,
            parse_float=parse_json_float,
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except StudioError:
        raise
    except UnicodeDecodeError as exc:
        raise StudioError(f"Invalid UTF-8 in {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudioError(f"Invalid JSON in {path}: {exc}") from exc
    except RecursionError as exc:
        raise StudioError(f"JSON nesting is too deep in {path}") from exc
    if not isinstance(data, dict):
        raise StudioError(f"Expected a JSON object in {path}")
    return data


def parse_json_object(content: str, label: str) -> dict[str, Any]:
    try:
        data = json.loads(
            content,
            parse_constant=reject_json_constant,
            parse_float=parse_json_float,
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except StudioError:
        raise
    except json.JSONDecodeError as exc:
        raise StudioError(f"Invalid JSON in {label}: {exc}") from exc
    except RecursionError as exc:
        raise StudioError(f"JSON nesting is too deep in {label}") from exc
    if not isinstance(data, dict):
        raise StudioError(f"Expected a JSON object in {label}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError, RecursionError) as exc:
        raise StudioError(f"Cannot serialize valid JSON for {path}: {exc}") from exc
    atomic_write(path, content)


def has_supported_schema_version(data: dict[str, Any]) -> bool:
    version = data.get("schema_version")
    return isinstance(version, int) and not isinstance(version, bool) and version == SCHEMA_VERSION


def available_profile_ids() -> list[str]:
    return sorted(
        path.stem for path in PROFILE_DIR.glob("*.json") if path.stem != "common"
    )


def load_profile(profile_id: str) -> dict[str, Any]:
    if profile_id not in available_profile_ids():
        choices = ", ".join(available_profile_ids())
        raise StudioError(f"Unknown profile '{profile_id}'. Available: {choices}")

    common = load_json(PROFILE_DIR / "common.json")
    overlay = load_json(PROFILE_DIR / f"{profile_id}.json")
    phases = copy.deepcopy(common.get("phases", []))
    extra_gates = overlay.get("phase_gates", {})
    for phase in phases:
        phase["gates"].extend(copy.deepcopy(extra_gates.get(phase["id"], [])))
        gate_ids = [gate["id"] for gate in phase["gates"]]
        if len(gate_ids) != len(set(gate_ids)):
            raise StudioError(f"Duplicate gate id in profile {profile_id}:{phase['id']}")

    return {
        "id": overlay["id"],
        "display_name": overlay["display_name"],
        "template_directory": overlay["template_directory"],
        "required_files": CORE_REQUIRED_FILES + overlay.get("required_files", []),
        "phases": phases,
    }


def phase_by_id(profile: dict[str, Any], phase_id: str) -> dict[str, Any]:
    for phase in profile["phases"]:
        if phase["id"] == phase_id:
            return phase
    raise StudioError(f"Unknown phase '{phase_id}' for profile {profile['id']}")


def project_paths(target: Path) -> tuple[Path, Path, Path]:
    studio = target / STUDIO_DIR
    return studio, studio / "project.json", studio / "work-items.json"


def render_template(content: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", value)
    return content


def copy_template_tree(source_root: Path, destination: Path, values: dict[str, str]) -> None:
    for source in sorted(source_root.rglob("*")):
        relative = source.relative_to(source_root)
        target = destination / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            raise StudioError(f"Refusing to overwrite existing template target: {target}")
        content = source.read_text(encoding="utf-8")
        atomic_write(target, render_template(content, values))


def prepare_agents_content(target: Path) -> str:
    agents_path = target / "AGENTS.md"
    existing = read_text_exact(agents_path) if agents_path.exists() else ""
    start_count = existing.count(MANAGED_START)
    end_count = existing.count(MANAGED_END)
    if start_count != end_count or start_count > 1:
        raise StudioError("AGENTS.md contains invalid AI Project Studio markers")
    if start_count == 1:
        start = existing.index(MANAGED_START)
        end = existing.index(MANAGED_END)
        if start >= end:
            raise StudioError("AGENTS.md contains reversed AI Project Studio markers")
        end += len(MANAGED_END)
        newline = detect_newline(existing[start:end])
        block = AGENTS_BLOCK.rstrip("\n").replace("\n", newline)
        return existing[:start] + block + existing[end:]
    newline = detect_newline(existing)
    block = AGENTS_BLOCK.rstrip("\n").replace("\n", newline)
    prefix = existing.rstrip("\r\n")
    return f"{prefix}{newline}{newline}{block}{newline}" if prefix else f"{block}{newline}"


def ensure_agents_block(target: Path) -> None:
    agents_path = target / "AGENTS.md"
    content = prepare_agents_content(target)
    if not agents_path.exists() or read_text_exact(agents_path) != content:
        write_user_text(agents_path, content)


def initial_phase_gate_progress(
    profile: dict[str, Any], phase_id: str
) -> dict[str, Any]:
    phase = phase_by_id(profile, phase_id)
    return {
        gate["id"]: {
            "status": "pending",
            "evidence": None,
            "by": None,
            "completed_at": None,
        }
        for gate in phase["gates"]
    }


def initial_gate_progress(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        phase["id"]: initial_phase_gate_progress(profile, phase["id"])
        for phase in profile["phases"]
    }


def load_project(target: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _, project_path, work_path = project_paths(target)
    project = load_json(project_path)
    work = load_json(work_path)
    if not has_supported_schema_version(project):
        raise StudioError("Unsupported project.json schema_version; run migration before mutation")
    if not has_supported_schema_version(work):
        raise StudioError("Unsupported work-items.json schema_version; run migration before mutation")
    project_info = project.get("project")
    if not isinstance(project_info, dict):
        raise StudioError("project.json project must be an object")
    profile_id = project_info.get("profile")
    if not isinstance(profile_id, str):
        raise StudioError("project.json is missing project.profile")
    return project, work, load_profile(profile_id)


def build_status_content(
    project: dict[str, Any], work: dict[str, Any], profile: dict[str, Any]
) -> str:
    phase_id = project["lifecycle"]["phase"]
    updated_at = max(project["updated_at"], work.get("updated_at", project["updated_at"]))
    phase = phase_by_id(profile, phase_id)
    progress = project["gate_progress"][phase_id]
    gate_lines = []
    for gate in phase["gates"]:
        entry = progress[gate["id"]]
        marker = "x" if entry["status"] == "complete" else " "
        suffix = f" — {entry['evidence']}" if entry.get("evidence") else ""
        gate_lines.append(f"- [{marker}] {gate['label']}{suffix}")

    active_states = {"draft", "proposed", "approved", "in_progress", "review"}
    active_items = [item for item in work.get("items", []) if item.get("status") in active_states]
    work_lines = []
    for item in active_items:
        contract = item.get("deliverable_contract")
        suffix = ""
        if isinstance(contract, dict):
            suffix = f" — {contract.get('fidelity', '?')} / {contract.get('type', '?')}"
        work_lines.append(f"- {item['id']} [{item['status']}] {item['title']}{suffix}")
    if not work_lines:
        work_lines = ["- None"]
    history_lines = [
        f"- {event.get('at', '?')} {event.get('type', 'event')}: {event.get('summary', '')}"
        for event in project.get("history", [])[-5:]
    ] or ["- None"]

    content = "\n".join(
        [
            "# Project Status",
            "",
            "<!-- Generated by studio.py. Do not edit manually. -->",
            "",
            f"- Project: {project['project']['name']}",
            f"- Profile: {profile['display_name']} ({profile['id']})",
            f"- Current phase: {phase['title']} ({phase_id})",
            f"- Phase run: {project['lifecycle']['active_run']}",
            f"- Phase goal: {phase['goal']}",
            f"- Updated: {updated_at}",
            "",
            "## Current Phase Gates",
            "",
            *gate_lines,
            "",
            "## Active Work",
            "",
            *work_lines,
            "",
            "## Recent Lifecycle History",
            "",
            *history_lines,
            "",
        ]
    )
    return content


def render_status(
    target: Path, project: dict[str, Any], work: dict[str, Any], profile: dict[str, Any]
) -> None:
    atomic_write(
        target / STUDIO_DIR / "STATUS.md", build_status_content(project, work, profile)
    )


def validate_gate_entries(
    errors: list[str],
    phase: dict[str, Any],
    entries: Any,
    label: str,
    require_complete: bool = False,
) -> None:
    if not isinstance(entries, dict):
        errors.append(f"{label} must be an object")
        return
    expected_ids = {gate["id"] for gate in phase["gates"]}
    actual_ids = set(entries)
    for missing in sorted(expected_ids - actual_ids):
        errors.append(f"Missing gate {label}:{missing}")
    for unknown in sorted(actual_ids - expected_ids):
        errors.append(f"Unknown gate {label}:{unknown}")
    for gate_id, entry in entries.items():
        if not isinstance(entry, dict):
            errors.append(f"Invalid gate entry {label}:{gate_id}")
            continue
        state = entry.get("status")
        if not isinstance(state, str) or state not in VALID_GATE_STATES:
            errors.append(f"Invalid gate state {label}:{gate_id}")
            continue
        if require_complete and state != "complete":
            errors.append(f"Archived phase run has incomplete gate {label}:{gate_id}")
        if state == "complete" and not all(
            isinstance(entry.get(field), str) and entry[field].strip()
            for field in ("evidence", "by", "completed_at")
        ):
            errors.append(f"Completed gate lacks evidence metadata {label}:{gate_id}")
        if state == "pending" and any(
            entry.get(field) is not None for field in ("evidence", "by", "completed_at")
        ):
            errors.append(f"Pending gate retains completion metadata {label}:{gate_id}")


def validate_gate_history(
    errors: list[str],
    project: dict[str, Any],
    profile: dict[str, Any],
    phase_runs: list[Any],
    history: list[Any],
) -> None:
    profile_phase_ids = {phase["id"] for phase in profile["phases"]}
    run_lookup = {
        run["id"]: run
        for run in phase_runs
        if isinstance(run, dict) and isinstance(run.get("id"), str)
        and run.get("phase") in profile_phase_ids
    }
    gate_events = [
        event
        for event in history
        if isinstance(event, dict)
        and event.get("type") in {"gate_completed", "gate_reopened"}
    ]
    valid_events: list[dict[str, Any]] = []
    for index, event in enumerate(gate_events):
        run = run_lookup.get(event.get("run_id"))
        if not run:
            errors.append(f"Gate history event {index} references an unknown phase run")
            continue
        run_phase = run.get("phase")
        if event.get("phase") != run_phase:
            errors.append(f"Gate history event {index} phase does not match its run")
            continue
        phase = phase_by_id(profile, run_phase)
        gate_ids = {gate["id"] for gate in phase["gates"]}
        if event.get("gate_id") not in gate_ids:
            errors.append(f"Gate history event {index} references an unknown gate")
            continue
        if event["type"] == "gate_completed" and not require_optional_text(
            event.get("evidence")
        ):
            errors.append(f"Gate history event {index} has no evidence")
            continue
        if event["type"] == "gate_reopened" and not require_optional_text(
            event.get("reason")
        ):
            errors.append(f"Gate history event {index} has no reopen reason")
            continue
        valid_events.append(event)

    progress = project.get("gate_progress")
    for run in run_lookup.values():
        run_id = run["id"]
        run_phase = run.get("phase")
        phase = phase_by_id(profile, run_phase)
        entries = (
            run.get("gates")
            if run.get("completed_at") is not None
            else progress.get(run_phase) if isinstance(progress, dict) else None
        )
        if not isinstance(entries, dict):
            continue
        for gate in phase["gates"]:
            gate_id = gate["id"]
            events = [
                event
                for event in valid_events
                if event.get("run_id") == run_id and event.get("gate_id") == gate_id
            ]
            simulated = "pending"
            last_completion: dict[str, Any] | None = None
            for event in events:
                if event["type"] == "gate_completed":
                    if simulated == "complete":
                        errors.append(f"Duplicate gate completion {run_id}:{gate_id}")
                    simulated = "complete"
                    last_completion = event
                else:
                    if simulated == "pending":
                        errors.append(f"Gate reopened while pending {run_id}:{gate_id}")
                    expected_previous = (
                        {
                            "status": "complete",
                            "evidence": last_completion.get("evidence"),
                            "by": last_completion.get("by"),
                            "completed_at": last_completion.get("at"),
                        }
                        if last_completion
                        else None
                    )
                    if event.get("previous_completion") != expected_previous:
                        errors.append(f"Gate reopen snapshot mismatch {run_id}:{gate_id}")
                    simulated = "pending"
                    last_completion = None
            entry = entries.get(gate_id)
            if not isinstance(entry, dict):
                continue
            if entry.get("status") != simulated:
                errors.append(f"Gate history does not match state {run_id}:{gate_id}")
            if simulated == "complete" and last_completion and (
                entry.get("evidence") != last_completion.get("evidence")
                or entry.get("by") != last_completion.get("by")
                or entry.get("completed_at") != last_completion.get("at")
            ):
                errors.append(f"Gate completion metadata mismatch {run_id}:{gate_id}")


def validate_required_project_file(errors: list[str], studio: Path, relative: str) -> None:
    current = studio
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            errors.append(f"Missing required file: studio/{relative}")
            return
        if stat.S_ISLNK(state.st_mode):
            errors.append(f"Required path must not be a symbolic link: studio/{relative}")
            return
        if index < len(parts) - 1:
            if not stat.S_ISDIR(state.st_mode):
                errors.append(f"Required path parent is not a directory: studio/{relative}")
                return
        elif not stat.S_ISREG(state.st_mode):
            errors.append(f"Required project file must be regular: studio/{relative}")
            return
        elif state.st_nlink > 1:
            errors.append(f"Required project file must not be hard-linked: studio/{relative}")


def validate_target(target: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    studio, project_path, work_path = project_paths(target)
    if not studio.is_dir():
        return [f"Missing directory: {studio}"], warnings
    if studio.is_symlink():
        return [f"Studio directory must not be a symbolic link: {studio}"], warnings
    specs_root = studio / "specs"
    if specs_root.is_symlink():
        errors.append("studio/specs must not be a symbolic link")
    unsafe_state_file = False
    for state_path, label in (
        (project_path, "studio/project.json"),
        (work_path, "studio/work-items.json"),
    ):
        if state_path.is_symlink():
            errors.append(f"{label} must not be a symbolic link")
            unsafe_state_file = True
        elif state_path.exists() and not stat.S_ISREG(state_path.lstat().st_mode):
            errors.append(f"{label} must be a regular file")
            unsafe_state_file = True
    if unsafe_state_file:
        return errors, warnings

    try:
        project = load_json(project_path)
        work = load_json(work_path)
    except StudioError as exc:
        return [str(exc)], warnings

    if not has_supported_schema_version(project):
        errors.append("Unsupported project.json schema_version")
    if not has_supported_schema_version(work):
        errors.append("Unsupported work-items.json schema_version")
    if not require_optional_text(work.get("created_at")):
        errors.append("work-items.json is missing created_at")
    if not require_optional_text(work.get("updated_at")):
        errors.append("work-items.json is missing updated_at")

    project_info = project.get("project")
    if not isinstance(project_info, dict):
        errors.append("project.json project must be an object")
        project_info = {}
    if not isinstance(project_info.get("name"), str) or not project_info.get("name", "").strip():
        errors.append("project.json is missing a valid project.name")
    owner = project_info.get("owner")
    if owner is None:
        warnings.append("Legacy project has no enforced user owner")
    elif not require_optional_text(owner):
        errors.append("project.json has an invalid project.owner")
    profile_id = project_info.get("profile")
    try:
        if not isinstance(profile_id, str):
            raise StudioError("project.json is missing a valid project.profile")
        profile = load_profile(profile_id)
    except StudioError as exc:
        errors.append(str(exc))
        profile = None

    lifecycle = project.get("lifecycle")
    if not isinstance(lifecycle, dict):
        errors.append("project.json lifecycle must be an object")
        lifecycle = {}
    phase_id = lifecycle.get("phase")
    active_run_id = lifecycle.get("active_run")
    next_run_id = lifecycle.get("next_run_id")

    if profile:
        phase_ids = {phase["id"] for phase in profile["phases"]}
        if not isinstance(phase_id, str) or phase_id not in phase_ids:
            errors.append(f"Invalid lifecycle phase: {phase_id}")

        progress = project.get("gate_progress")
        if not isinstance(progress, dict):
            errors.append("project.json gate_progress must be an object")
        else:
            for phase in profile["phases"]:
                validate_gate_entries(
                    errors,
                    phase,
                    progress.get(phase["id"]),
                    phase["id"],
                )

        phase_runs = project.get("phase_runs")
        if not isinstance(phase_runs, list) or not phase_runs:
            errors.append("project.json phase_runs must be a non-empty array")
            phase_runs = []
        run_ids: set[str] = set()
        active_runs: list[dict[str, Any]] = []
        for index, run in enumerate(phase_runs):
            if not isinstance(run, dict):
                errors.append(f"Phase run {index} must be an object")
                continue
            run_id = run.get("id")
            run_phase = run.get("phase")
            if not require_optional_text(run.get("started_at")):
                errors.append(f"Phase run {run_id} has no start timestamp")
            if not require_optional_text(run.get("started_by")):
                errors.append(f"Phase run {run_id} has no starter")
            if not isinstance(run_id, str) or not run_id:
                errors.append(f"Phase run {index} has no valid id")
            elif run_id in run_ids:
                errors.append(f"Duplicate phase run id: {run_id}")
            else:
                run_ids.add(run_id)
            if not isinstance(run_phase, str) or run_phase not in phase_ids:
                errors.append(f"Phase run {run_id} has invalid phase: {run_phase}")
                continue
            if index == 0 and run_phase != profile["phases"][0]["id"]:
                errors.append("The first phase run must start at discovery")
            if index > 0 and isinstance(phase_runs[index - 1], dict):
                previous_phase = phase_runs[index - 1].get("phase")
                if isinstance(previous_phase, str) and previous_phase in phase_ids:
                    allowed = phase_by_id(profile, previous_phase).get("next", [])
                    if run_phase not in allowed:
                        errors.append(f"Illegal archived phase transition: {previous_phase} -> {run_phase}")
            completed_at = run.get("completed_at")
            if completed_at is None:
                active_runs.append(run)
                if run.get("gates") is not None:
                    errors.append(f"Active phase run {run_id} must not archive gates")
            else:
                if not require_optional_text(completed_at):
                    errors.append(f"Phase run {run_id} has invalid completed_at")
                if not require_optional_text(run.get("approved_by")):
                    errors.append(f"Phase run {run_id} has no approver")
                if not require_optional_text(run.get("reason")):
                    errors.append(f"Phase run {run_id} has no transition reason")
                validate_gate_entries(
                    errors,
                    phase_by_id(profile, run_phase),
                    run.get("gates"),
                    f"run {run_id}",
                    require_complete=True,
                )

        if len(active_runs) != 1:
            errors.append("Exactly one phase run must be active")
        elif active_runs[0].get("id") != active_run_id or active_runs[0].get("phase") != phase_id:
            errors.append("Active phase run does not match lifecycle state")
        elif not phase_runs or active_runs[0] is not phase_runs[-1]:
            errors.append("The active phase run must be the final phase run")
        if not isinstance(next_run_id, int) or isinstance(next_run_id, bool) or next_run_id < 2:
            errors.append("lifecycle.next_run_id must be an integer greater than one")
        else:
            used_run_numbers = [
                int(run_id[2:])
                for run_id in run_ids
                if run_id.startswith("R-") and run_id[2:].isdigit()
            ]
            if used_run_numbers and next_run_id <= max(used_run_numbers):
                errors.append("lifecycle.next_run_id would reuse an existing id")

        history = project.get("history")
        if not isinstance(history, list) or not history:
            errors.append("project.json history must be a non-empty array")
        else:
            for index, event in enumerate(history):
                if not isinstance(event, dict):
                    errors.append(f"Project history entry {index} must be an object")
                    continue
                if event.get("type") not in {
                    "project_initialized",
                    "owner_set",
                    "gate_completed",
                    "gate_reopened",
                    "phase_advanced",
                }:
                    errors.append(f"Project history entry {index} has invalid type")
                if not require_optional_text(event.get("at")):
                    errors.append(f"Project history entry {index} has no timestamp")
                if not require_optional_text(event.get("by")):
                    errors.append(f"Project history entry {index} has no actor")
                if require_optional_text(owner) and (
                    event.get("type") in {"owner_set", "phase_advanced"}
                    or (
                        event.get("type") == "gate_completed"
                        and event.get("gate_id") == "release-approval"
                    )
                ) and event.get("by") != owner:
                    errors.append(
                        f"Project history entry {index} requires approval by owner {owner}"
                    )
            if not isinstance(history[0], dict) or history[0].get("type") != "project_initialized":
                errors.append("Project history must start with project_initialized")
            elif phase_runs and isinstance(phase_runs[0], dict) and (
                history[0].get("phase") != phase_runs[0].get("phase")
                or history[0].get("run_id") != phase_runs[0].get("id")
                or history[0].get("at") != phase_runs[0].get("started_at")
                or history[0].get("by") != phase_runs[0].get("started_by")
            ):
                errors.append("Project initialization history does not match first phase run")
            phase_events = [
                event for event in history
                if isinstance(event, dict) and event.get("type") == "phase_advanced"
            ]
            if len(phase_events) != max(0, len(phase_runs) - 1):
                errors.append("Project history does not match phase runs")
            else:
                for index, event in enumerate(phase_events):
                    before = phase_runs[index] if isinstance(phase_runs[index], dict) else {}
                    after = phase_runs[index + 1] if isinstance(phase_runs[index + 1], dict) else {}
                    if (
                        event.get("from") != before.get("phase")
                        or event.get("to") != after.get("phase")
                        or event.get("completed_run_id") != before.get("id")
                        or event.get("started_run_id") != after.get("id")
                        or event.get("at") != before.get("completed_at")
                        or event.get("at") != after.get("started_at")
                        or event.get("by") != before.get("approved_by")
                        or event.get("by") != after.get("started_by")
                        or event.get("reason") != before.get("reason")
                    ):
                        errors.append(f"Phase history event {index} does not match phase runs")
            validate_gate_history(errors, project, profile, phase_runs, history)

        for relative in profile["required_files"]:
            validate_required_project_file(errors, studio, relative)

    phase_run_records = project.get("phase_runs")
    if not isinstance(phase_run_records, list):
        phase_run_records = []
    items = work.get("items")
    if not isinstance(items, list):
        errors.append("work-items.json items must be an array")
    else:
        seen_ids: set[str] = set()
        in_flight_ids = [
            item.get("id")
            for item in items
            if isinstance(item, dict) and item.get("status") in IN_FLIGHT_WORK_STATES
        ]
        if len(in_flight_ids) > 1:
            errors.append(
                "Only one work item may be in progress or review at a time: "
                + ", ".join(str(item_id) for item_id in in_flight_ids)
            )
        for item in items:
            if not isinstance(item, dict):
                errors.append("Each work item must be an object")
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                errors.append("A work item has no valid id")
            elif item_id in seen_ids:
                errors.append(f"Duplicate work item id: {item_id}")
            else:
                seen_ids.add(item_id)
            status = item.get("status")
            status_is_valid = isinstance(status, str) and status in VALID_WORK_STATES
            if not status_is_valid:
                errors.append(f"Invalid work state for {item_id}: {status}")
            item_phase = item.get("phase")
            item_phase_run = item.get("phase_run")
            if not isinstance(item_phase, str) or not profile or item_phase not in {
                phase["id"] for phase in profile["phases"]
            }:
                errors.append(f"Work item {item_id} has invalid phase: {item_phase}")
            matching_runs = [
                run
                for run in phase_run_records
                if isinstance(run, dict) and run.get("id") == item_phase_run
            ]
            if (
                not isinstance(item_phase_run, str)
                or len(matching_runs) != 1
                or matching_runs[0].get("phase") != item_phase
            ):
                errors.append(f"Work item {item_id} has invalid phase run: {item_phase_run}")
            active_work_states = {"draft", "proposed", "approved", "in_progress", "review"}
            if (
                status_is_valid
                and status in active_work_states
                and (item_phase != phase_id or item_phase_run != active_run_id)
            ):
                errors.append(
                    f"Active work item {item_id} belongs to archived phase run {item_phase_run}"
                )
            for field in ("title", "summary", "kind"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(f"Work item {item_id} is missing {field}")

            checkpoint = item.get("checkpoint")
            if checkpoint is not None:
                if not isinstance(checkpoint, dict):
                    errors.append(f"Work item {item_id} checkpoint must be an object")
                else:
                    expected_checkpoint_fields = {
                        "summary",
                        "next_action",
                        "blockers",
                        "by",
                        "updated_at",
                    }
                    if set(checkpoint) != expected_checkpoint_fields:
                        errors.append(f"Work item {item_id} checkpoint has invalid fields")
                    for field in ("summary", "next_action", "by", "updated_at"):
                        if not require_optional_text(checkpoint.get(field)):
                            errors.append(
                                f"Work item {item_id} checkpoint is missing {field}"
                            )
                    blockers = checkpoint.get("blockers")
                    if not isinstance(blockers, list) or any(
                        not require_optional_text(blocker) for blocker in blockers
                    ):
                        errors.append(
                            f"Work item {item_id} checkpoint blockers must be text entries"
                        )

            spec = item.get("spec")
            normalized_spec: str | None = None
            if spec is not None:
                if not isinstance(spec, str) or not spec.strip():
                    errors.append(f"Work item {item_id} has an invalid spec path")
                elif not stored_spec_path_is_canonical(spec):
                    errors.append(
                        f"Work item {item_id} spec path is not canonical: {spec}"
                    )
                else:
                    try:
                        normalized_spec = normalized_spec_path(target, spec)
                    except StudioError as exc:
                        if status_is_valid and status == "done" and str(exc).startswith(
                            "Spec file does not exist:"
                        ):
                            warnings.append(
                                f"Completed work item {item_id} no longer has its approved spec file"
                            )
                        else:
                            errors.append(f"Work item {item_id}: {exc}")
                    else:
                        if spec != normalized_spec:
                            errors.append(
                                f"Work item {item_id} spec path is not canonical: {spec}"
                            )
            approved_states = {"approved", "in_progress", "review", "done"}
            if status_is_valid and status in approved_states and not spec:
                errors.append(f"Work item {item_id} is {status} without a spec")
            if status_is_valid and status in approved_states:
                approved_hash = item.get("approved_spec_sha256")
                if not isinstance(approved_hash, str) or len(approved_hash) != 64:
                    errors.append(f"Work item {item_id} has no valid approved spec hash")
                elif normalized_spec and hash_spec(target, normalized_spec) != approved_hash:
                    if status == "done":
                        warnings.append(
                            f"Completed work item {item_id} spec differs from its approved hash; "
                            "create a new work item for follow-up scope"
                        )
                    else:
                        errors.append(f"Work item {item_id} spec changed after approval")

            contract_version = item.get("contract_version")
            if contract_version is None:
                if status_is_valid and status in {
                    "draft",
                    "proposed",
                    "approved",
                    "in_progress",
                    "review",
                }:
                    warnings.append(
                        f"Legacy work item {item_id} has no enforced deliverable contract"
                    )
            elif contract_version != DELIVERABLE_CONTRACT_VERSION:
                errors.append(
                    f"Work item {item_id} has unsupported contract_version: {contract_version}"
                )
            else:
                contract_required = status_is_valid and status in {
                    "proposed",
                    "approved",
                    "in_progress",
                    "review",
                    "done",
                }
                stored_contract = item.get("deliverable_contract")
                if contract_required and not spec:
                    errors.append(
                        f"Work item {item_id} requires a spec with a deliverable contract"
                    )
                elif spec and normalized_spec:
                    try:
                        current_contract = load_deliverable_contract(target, normalized_spec)
                    except StudioError as exc:
                        if contract_required:
                            errors.append(f"Work item {item_id}: {exc}")
                    else:
                        if contract_required and stored_contract != current_contract:
                            errors.append(
                                f"Work item {item_id} deliverable contract does not match its spec"
                            )
                if contract_required and not isinstance(stored_contract, dict):
                    errors.append(
                        f"Work item {item_id} has no stored deliverable contract"
                    )

            history = item.get("history")
            if not isinstance(history, list) or not history:
                errors.append(f"Work item {item_id} has no transition history")
                continue
            history_state: str | None = None
            for index, event in enumerate(history):
                if not isinstance(event, dict):
                    errors.append(f"Work item {item_id} history entry {index} is invalid")
                    break
                if not require_optional_text(event.get("at")):
                    errors.append(f"Work item {item_id} history entry {index} has no timestamp")
                if not require_optional_text(event.get("by")):
                    errors.append(f"Work item {item_id} history entry {index} has no actor")
                source = event.get("from")
                destination = event.get("to")
                if index == 0:
                    if source is not None or destination != "draft":
                        errors.append(f"Work item {item_id} history must start at draft")
                        break
                elif (
                    not isinstance(source, str)
                    or not isinstance(destination, str)
                    or source != history_state
                    or destination not in WORK_TRANSITIONS.get(source, set())
                ):
                    errors.append(
                        f"Work item {item_id} has illegal history transition {source} -> {destination}"
                    )
                    break
                if destination == "approved" and not require_optional_text(event.get("by")):
                    errors.append(f"Work item {item_id} approval has no approver")
                if destination == "approved" and not require_optional_text(event.get("spec_sha256")):
                    errors.append(f"Work item {item_id} approval has no spec hash")
                if destination in {"review", "done"} and not require_optional_text(event.get("evidence")):
                    errors.append(f"Work item {item_id} transition to {destination} has no evidence")
                if (
                    destination in {"approved", "done"}
                    and require_optional_text(owner)
                    and event.get("by") != owner
                ):
                    errors.append(
                        f"Work item {item_id} transition to {destination} requires owner {owner}"
                    )
                history_state = destination
            if history_state != status:
                errors.append(f"Work item {item_id} status does not match its history")
            if status_is_valid and status in approved_states:
                if not require_optional_text(item.get("approved_by")):
                    errors.append(f"Work item {item_id} has no approval actor")
                if not require_optional_text(item.get("approved_at")):
                    errors.append(f"Work item {item_id} has no approval timestamp")
                approval_events = [
                    event
                    for event in history
                    if isinstance(event, dict) and event.get("to") == "approved"
                ]
                if (
                    not approval_events
                    or approval_events[-1].get("spec_sha256")
                    != item.get("approved_spec_sha256")
                ):
                    errors.append(f"Work item {item_id} approval hash does not match history")
                elif (
                    item.get("approved_by") != approval_events[-1].get("by")
                    or item.get("approved_at") != approval_events[-1].get("at")
                ):
                    errors.append(f"Work item {item_id} approval metadata does not match history")

    next_id = work.get("next_id")
    if not isinstance(next_id, int) or isinstance(next_id, bool) or next_id < 1:
        errors.append("work-items.json next_id must be a positive integer")
    else:
        used_numbers = [
            int(item_id[2:])
            for item_id in seen_ids if item_id.startswith("W-") and item_id[2:].isdigit()
        ] if isinstance(items, list) else []
        if used_numbers and next_id <= max(used_numbers):
            errors.append("work-items.json next_id would reuse an existing id")

    agents_path = target / "AGENTS.md"
    if agents_path.is_symlink():
        errors.append("AGENTS.md must not be a symbolic link")
    elif agents_path.exists() and agents_path.stat().st_nlink > 1:
        errors.append("AGENTS.md must not be a hard link")
    try:
        agents_text = read_text_exact(agents_path) if agents_path.exists() else ""
    except (OSError, UnicodeDecodeError, StudioError) as exc:
        errors.append(f"Cannot read AGENTS.md: {exc}")
        agents_text = ""
    if agents_text.count(MANAGED_START) != 1 or agents_text.count(MANAGED_END) != 1:
        errors.append("AGENTS.md has missing or duplicate AI Project Studio markers")
    else:
        start = agents_text.index(MANAGED_START)
        end = agents_text.index(MANAGED_END)
        if start >= end:
            errors.append("AGENTS.md has reversed AI Project Studio markers")
        else:
            end += len(MANAGED_END)
            actual_block = agents_text[start:end].replace("\r\n", "\n").replace("\r", "\n")
            expected_block = AGENTS_BLOCK.rstrip("\n")
            if actual_block != expected_block:
                errors.append("AGENTS.md AI Project Studio managed block was modified")
    status_path = studio / "STATUS.md"
    if not status_path.exists():
        errors.append("Missing generated studio/STATUS.md")
    elif status_path.is_symlink():
        errors.append("studio/STATUS.md must not be a symbolic link")
    elif profile:
        try:
            expected_status = build_status_content(project, work, profile)
        except (AttributeError, KeyError, TypeError, IndexError, StudioError) as exc:
            errors.append(f"Unable to render studio/STATUS.md from project state: {exc}")
        else:
            try:
                actual_status = read_text_exact(status_path)
            except (OSError, UnicodeDecodeError, StudioError) as exc:
                errors.append(f"Cannot read studio/STATUS.md: {exc}")
                actual_status = None
            if actual_status is not None and (
                actual_status.replace("\r\n", "\n").replace("\r", "\n")
                != expected_status.replace("\r\n", "\n").replace("\r", "\n")
            ):
                errors.append("studio/STATUS.md does not match project state")
    return errors, warnings


def require_valid_target(target: Path, ignored_fragments: tuple[str, ...] = ()) -> None:
    errors, _ = validate_target(target)
    errors = [error for error in errors if not any(part in error for part in ignored_fragments)]
    if errors:
        raise StudioError("Refusing to mutate invalid Studio state:\n- " + "\n- ".join(errors))


def command_profiles(_: argparse.Namespace) -> int:
    for profile_id in available_profile_ids():
        profile = load_profile(profile_id)
        print(f"{profile_id}\t{profile['display_name']}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    target.mkdir(parents=True, exist_ok=True)
    target_state = target.lstat()
    if stat.S_ISLNK(target_state.st_mode) or not stat.S_ISDIR(target_state.st_mode):
        raise StudioError(f"Initialization target must be a real directory: {target}")
    studio, project_path, work_path = project_paths(target)
    profile = load_profile(args.profile)
    project_name = require_text(args.name, "Project name")
    initialized_by = require_text(args.initialized_by, "Initializer")
    owner = require_text(args.owner, "Project owner")
    project_brief = (
        args.idea.strip()
        if isinstance(args.idea, str) and args.idea.strip()
        else "待确认：用户尚未提供初始项目简述。"
    )
    desired_agents_content = prepare_agents_content(target)
    if studio.is_symlink():
        raise StudioError(f"Refusing to initialize symbolic-link Studio directory: {studio}")

    if project_path.exists():
        existing = load_json(project_path)
        current = existing.get("project", {})
        if current.get("profile") != args.profile or current.get("name") != project_name:
            raise StudioError("Project is already initialized with different name or profile")
        project, work, profile = load_project(target)
        errors, _ = validate_target(target)
        repairable = (
            "AGENTS.md has missing or duplicate AI Project Studio markers",
            "AGENTS.md AI Project Studio managed block was modified",
            "Missing generated studio/STATUS.md",
            "studio/STATUS.md does not match project state",
        )
        blocking_errors = [error for error in errors if error not in repairable]
        if blocking_errors:
            raise StudioError(
                "Existing project is invalid:\n- " + "\n- ".join(blocking_errors)
            )
        agents_path = target / "AGENTS.md"
        status_path = studio / "STATUS.md"
        agents_existed = agents_path.exists()
        status_existed = status_path.exists()
        original_agents = read_text_exact(agents_path) if agents_existed else ""
        original_status = read_text_exact(status_path) if status_existed else ""
        status_written = False
        try:
            ensure_agents_block(target)
            render_status(target, project, work, profile)
            status_written = True
            errors, _ = validate_target(target)
            if errors:
                raise StudioError("Existing project is invalid:\n- " + "\n- ".join(errors))
        except Exception:
            try:
                if agents_existed:
                    write_user_text(agents_path, original_agents)
                elif agents_path.exists():
                    agents_path.unlink()
                if status_written:
                    if status_existed:
                        atomic_write(status_path, original_status)
                    elif status_path.exists():
                        status_path.unlink()
            except Exception:
                pass
            raise
        print(f"Already initialized: {target}")
        return 0
    if studio.exists():
        raise StudioError(f"Refusing to replace pre-existing uninitialized directory: {studio}")

    values = {
        "PROJECT_NAME": project_name,
        "PROFILE_NAME": profile["display_name"],
        "CREATED_DATE": datetime.now().date().isoformat(),
        "PROJECT_BRIEF": project_brief,
    }
    timestamp = utc_now()
    initial_phase = profile["phases"][0]["id"]
    project = {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "name": project_name,
            "profile": profile["id"],
            "owner": owner,
            "created_at": timestamp,
        },
        "lifecycle": {"phase": initial_phase, "active_run": "R-0001", "next_run_id": 2},
        "gate_progress": initial_gate_progress(profile),
        "phase_runs": [
            {
                "id": "R-0001",
                "phase": initial_phase,
                "started_at": timestamp,
                "started_by": initialized_by,
                "completed_at": None,
                "approved_by": None,
                "reason": None,
                "gates": None,
            }
        ],
        "created_at": timestamp,
        "updated_at": timestamp,
        "history": [
            {
                "type": "project_initialized",
                "at": timestamp,
                "by": initialized_by,
                "summary": f"Initialized with {profile['id']} profile",
                "phase": initial_phase,
                "run_id": "R-0001",
            }
        ],
    }
    work = {
        "schema_version": SCHEMA_VERSION,
        "next_id": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "items": [],
    }

    with tempfile.TemporaryDirectory(prefix=".ai-project-studio-", dir=target) as temporary:
        staging_root = Path(temporary)
        staging_studio = staging_root / STUDIO_DIR
        staging_studio.mkdir()
        copy_template_tree(TEMPLATE_DIR / "core", staging_studio, values)
        copy_template_tree(
            TEMPLATE_DIR / profile["template_directory"], staging_studio, values
        )
        write_json(staging_studio / "project.json", project)
        write_json(staging_studio / "work-items.json", work)
        atomic_write(staging_root / "AGENTS.md", desired_agents_content)
        render_status(staging_root, project, work, profile)
        errors, _ = validate_target(staging_root)
        if errors:
            raise StudioError("Initialization staging failed:\n- " + "\n- ".join(errors))

        agents_path = target / "AGENTS.md"
        agents_existed = agents_path.exists()
        original_agents = read_text_exact(agents_path) if agents_existed else ""
        write_user_text(agents_path, desired_agents_content)
        try:
            os.replace(staging_studio, studio)
        except Exception:
            try:
                if agents_existed:
                    write_user_text(agents_path, original_agents)
                elif agents_path.exists():
                    agents_path.unlink()
            except Exception:
                pass
            raise

    errors, _ = validate_target(target)
    if errors:
        raise StudioError("Initialization failed validation:\n- " + "\n- ".join(errors))
    print(f"Initialized {project_name} at {target} with profile {profile['id']}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    errors, warnings = validate_target(target)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"Valid AI Project Studio project: {target}")
    return 0


def command_repair(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    project, work, profile = load_project(target)
    errors, _ = validate_target(target)
    repairable_fragments = (
        "AGENTS.md has missing or duplicate AI Project Studio markers",
        "AGENTS.md AI Project Studio managed block was modified",
        "Missing generated studio/STATUS.md",
        "studio/STATUS.md does not match project state",
    )
    agents_path = target / "AGENTS.md"
    agents_text = read_text_exact(agents_path) if agents_path.exists() else ""
    marker_counts_are_safely_missing = (
        agents_text.count(MANAGED_START) == 0 and agents_text.count(MANAGED_END) == 0
    )
    blocking = [
        error
        for error in errors
        if not any(fragment in error for fragment in repairable_fragments)
        or (
            "AGENTS.md has missing or duplicate AI Project Studio markers" in error
            and not marker_counts_are_safely_missing
        )
    ]
    if blocking:
        raise StudioError(
            "Repair only refreshes managed instructions and generated status; "
            "fix these state errors first:\n- " + "\n- ".join(blocking)
        )

    status_path = target / STUDIO_DIR / "STATUS.md"
    agents_existed = agents_path.exists()
    status_existed = status_path.exists()
    original_agents = read_text_exact(agents_path) if agents_existed else ""
    original_status = read_text_exact(status_path) if status_existed else ""
    try:
        ensure_agents_block(target)
        render_status(target, project, work, profile)
        remaining, _ = validate_target(target)
        if remaining:
            raise StudioError("Repair did not restore valid state:\n- " + "\n- ".join(remaining))
    except Exception:
        try:
            if agents_existed:
                write_user_text(agents_path, original_agents)
            elif agents_path.exists():
                agents_path.unlink()
            if status_existed:
                atomic_write(status_path, original_status)
            elif status_path.exists():
                status_path.unlink()
        except Exception:
            pass
        raise
    print(f"Repaired managed Studio instructions and status: {target}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(
        target,
        ("Missing generated studio/STATUS.md", "studio/STATUS.md does not match project state"),
    )
    project, work, profile = load_project(target)
    if args.json:
        print(
            json.dumps(
                {"project": project, "work_items": work},
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )
    else:
        render_status(target, project, work, profile)
        print(read_text_exact(target / STUDIO_DIR / "STATUS.md"), end="")
    return 0


def command_project_set_owner(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    current = project.get("project", {}).get("owner")
    if require_optional_text(current):
        raise StudioError(
            f"Project owner is already '{current}'; changing ownership requires a migration"
        )
    owner = require_text(args.owner, "Project owner")
    original_project = copy.deepcopy(project)
    timestamp = utc_now()
    project["project"]["owner"] = owner
    project["updated_at"] = timestamp
    project["history"].append(
        {
            "type": "owner_set",
            "at": timestamp,
            "by": owner,
            "summary": f"Set project owner to {owner}",
        }
    )
    _, project_path, _ = project_paths(target)
    write_json(project_path, project)
    render_status(target, project, work, profile)
    errors, _ = validate_target(target)
    if errors:
        write_json(project_path, original_project)
        render_status(target, original_project, work, profile)
        raise StudioError("Setting owner produced invalid state:\n- " + "\n- ".join(errors))
    print(f"Set project owner: {owner}")
    return 0


def select_brief_item(
    work: dict[str, Any], requested_id: str | None
) -> tuple[dict[str, Any] | None, list[str]]:
    if requested_id:
        return find_work_item(work, requested_id), []
    priority_groups = (
        {"in_progress", "review"},
        {"approved"},
        {"proposed"},
        {"draft"},
    )
    for states in priority_groups:
        candidates = [
            item for item in work.get("items", []) if item.get("status") in states
        ]
        if len(candidates) == 1:
            return candidates[0], []
        if len(candidates) > 1:
            return None, [item["id"] for item in candidates]
    return None, []


def build_brief_data(
    target: Path,
    project: dict[str, Any],
    work: dict[str, Any],
    profile: dict[str, Any],
    requested_item: str | None,
) -> dict[str, Any]:
    phase_id = project["lifecycle"]["phase"]
    phase = phase_by_id(profile, phase_id)
    progress = project["gate_progress"][phase_id]
    focus, focus_candidates = select_brief_item(work, requested_item)
    active_states = {"draft", "proposed", "approved", "in_progress", "review"}
    active_items = [
        {
            "id": item["id"],
            "title": item["title"],
            "status": item["status"],
            "kind": item["kind"],
        }
        for item in work.get("items", [])
        if item.get("status") in active_states
    ]
    required_reads = ["studio/STATUS.md", "studio/PROJECT.md"]
    focus_data: dict[str, Any] | None = None
    if focus:
        execution_allowed = focus.get("status") in {"approved", "in_progress"}
        next_transition = {
            "draft": "proposed",
            "proposed": "approved",
            "approved": "in_progress",
            "in_progress": "review",
            "review": "done or in_progress",
        }.get(focus.get("status"))
        focus_data = {
            "id": focus["id"],
            "title": focus["title"],
            "status": focus["status"],
            "kind": focus["kind"],
            "summary": focus["summary"],
            "phase": focus["phase"],
            "phase_run": focus["phase_run"],
            "spec": focus.get("spec"),
            "deliverable_contract": focus.get("deliverable_contract"),
            "checkpoint": focus.get("checkpoint"),
            "execution_allowed": execution_allowed,
            "next_transition": next_transition,
            "state_owner": "producer",
            "subagent_state_changes_allowed": False,
        }
        if focus.get("spec"):
            required_reads.append(focus["spec"])
    return {
        "project": {
            "name": project["project"]["name"],
            "profile": profile["id"],
            "root": str(target),
            "owner": project["project"].get("owner"),
        },
        "phase": {
            "id": phase_id,
            "title": phase["title"],
            "goal": phase["goal"],
            "run_id": project["lifecycle"]["active_run"],
            "pending_gates": [
                {"id": gate["id"], "label": gate["label"]}
                for gate in phase["gates"]
                if progress[gate["id"]]["status"] != "complete"
            ],
        },
        "focus_item": focus_data,
        "focus_candidates": focus_candidates,
        "active_items": active_items,
        "required_reads": required_reads,
        "profile_reference": f"references/{profile['id']}.md",
        "operating_boundaries": [
            "Ask before product, scope, risk, cost, release, or irreversible decisions.",
            "Implement only approved or in_progress work with a current spec and contract.",
            "Treat subagents as temporary executors; pass the project root and work item id.",
            "Subagents must not approve work, advance phases, or mutate Studio state.",
            "Verify with inspectable evidence before changing state or recommending the next step.",
        ],
    }


def render_brief_content(data: dict[str, Any]) -> str:
    phase = data["phase"]
    focus = data["focus_item"]
    pending = phase["pending_gates"]
    lines = [
        "# Studio Brief",
        "",
        f"- Project: {data['project']['name']}",
        f"- Root: {data['project']['root']}",
        f"- Profile: {data['project']['profile']}",
        f"- Owner: {data['project'].get('owner') or 'Legacy project: not enforced'}",
        f"- Phase: {phase['title']} ({phase['id']})",
        f"- Phase run: {phase['run_id']}",
        f"- Phase goal: {phase['goal']}",
        "",
        "## Pending Gates",
        "",
        *([f"- {gate['id']}: {gate['label']}" for gate in pending] or ["- None"]),
        "",
        "## Focus Work Item",
        "",
    ]
    if focus:
        lines.extend(
            [
                f"- ID: {focus['id']}",
                f"- Status: {focus['status']}",
                f"- Title: {focus['title']}",
                f"- Summary: {focus['summary']}",
                f"- Spec: {focus.get('spec') or 'None'}",
            ]
        )
        contract = focus.get("deliverable_contract")
        if isinstance(contract, dict):
            lines.extend(
                [
                    f"- Deliverable: {contract.get('fidelity')} / {contract.get('type')}",
                    f"- Purpose: {contract.get('purpose')}",
                    "- Does not prove: " + "; ".join(contract.get("does_not_prove", [])),
                ]
            )
    else:
        lines.append("- None")
        if data["focus_candidates"]:
            lines.append(
                "- Ambiguous candidates; rerun with --item: "
                + ", ".join(data["focus_candidates"])
            )
    if focus:
        lines.extend(
            [
                f"- Execution allowed: {'yes' if focus['execution_allowed'] else 'no'}",
                f"- Next transition: {focus.get('next_transition') or 'None'}",
                "- Studio state owner: producer",
                "- Subagent state changes allowed: no",
            ]
        )
        checkpoint = focus.get("checkpoint")
        if isinstance(checkpoint, dict):
            lines.extend(
                [
                    f"- Checkpoint: {checkpoint.get('summary')}",
                    f"- Checkpoint next action: {checkpoint.get('next_action')}",
                    "- Checkpoint blockers: "
                    + ("; ".join(checkpoint.get("blockers", [])) or "None"),
                ]
            )
    lines.extend(
        [
            "",
            "## Required Reads",
            "",
            *[f"- {path}" for path in data["required_reads"]],
            f"- Skill profile: {data['profile_reference']}",
            "",
            "## Operating Boundaries",
            "",
            *[f"- {rule}" for rule in data["operating_boundaries"]],
            "",
        ]
    )
    return "\n".join(lines)


def command_brief(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    data = build_brief_data(target, project, work, profile, args.item)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(render_brief_content(data), end="")
    return 0


def command_gate_list(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, _, profile = load_project(target)
    phase_id = project["lifecycle"]["phase"]
    phase = phase_by_id(profile, phase_id)
    progress = project["gate_progress"][phase_id]
    for gate in phase["gates"]:
        entry = progress[gate["id"]]
        print(f"{gate['id']}\t{entry['status']}\t{gate['label']}")
    return 0


def require_user_actor(project: dict[str, Any], value: str | None, label: str) -> str:
    actor = require_text(value, label)
    owner = project.get("project", {}).get("owner")
    if require_optional_text(owner) and actor != owner:
        raise StudioError(f"{label} must match the project owner '{owner}'")
    return actor


def command_gate_complete(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    phase_id = project["lifecycle"]["phase"]
    phase = phase_by_id(profile, phase_id)
    valid_gate_ids = {gate["id"] for gate in phase["gates"]}
    if args.gate_id not in valid_gate_ids:
        raise StudioError(f"Gate '{args.gate_id}' is not part of current phase {phase_id}")
    evidence = require_text(args.evidence, "Gate evidence")
    actor = (
        require_user_actor(project, args.by, "Release approval actor")
        if args.gate_id == "release-approval"
        else require_text(args.by, "Gate completer")
    )
    entry = project["gate_progress"][phase_id][args.gate_id]
    if entry["status"] == "complete":
        print(f"Gate already complete: {args.gate_id}")
        return 0
    timestamp = utc_now()
    entry.update(
        {
            "status": "complete",
            "evidence": evidence,
            "by": actor,
            "completed_at": timestamp,
        }
    )
    project["updated_at"] = timestamp
    project["history"].append(
        {
            "type": "gate_completed",
            "at": timestamp,
            "by": actor,
            "summary": f"{phase_id}:{args.gate_id} — {evidence}",
            "phase": phase_id,
            "run_id": project["lifecycle"]["active_run"],
            "gate_id": args.gate_id,
            "evidence": evidence,
        }
    )
    _, project_path, _ = project_paths(target)
    write_json(project_path, project)
    render_status(target, project, work, profile)
    print(f"Completed gate {phase_id}:{args.gate_id}")
    return 0


def command_gate_reopen(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    phase_id = project["lifecycle"]["phase"]
    phase = phase_by_id(profile, phase_id)
    valid_gate_ids = {gate["id"] for gate in phase["gates"]}
    if args.gate_id not in valid_gate_ids:
        raise StudioError(f"Gate '{args.gate_id}' is not part of current phase {phase_id}")
    entry = project["gate_progress"][phase_id][args.gate_id]
    if entry["status"] != "complete":
        raise StudioError(f"Gate is not complete: {phase_id}:{args.gate_id}")
    actor = require_text(args.by, "Gate reopener")
    reason = require_text(args.reason, "Gate reopen reason")
    previous = copy.deepcopy(entry)
    timestamp = utc_now()
    entry.update(
        {"status": "pending", "evidence": None, "by": None, "completed_at": None}
    )
    project["updated_at"] = timestamp
    project["history"].append(
        {
            "type": "gate_reopened",
            "at": timestamp,
            "by": actor,
            "summary": f"{phase_id}:{args.gate_id} reopened — {reason}",
            "phase": phase_id,
            "run_id": project["lifecycle"]["active_run"],
            "gate_id": args.gate_id,
            "reason": reason,
            "previous_completion": previous,
        }
    )
    _, project_path, _ = project_paths(target)
    write_json(project_path, project)
    render_status(target, project, work, profile)
    print(f"Reopened gate {phase_id}:{args.gate_id}")
    return 0


def command_phase_advance(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    current_id = project["lifecycle"]["phase"]
    current = phase_by_id(profile, current_id)
    incomplete = [
        gate["id"]
        for gate in current["gates"]
        if project["gate_progress"][current_id][gate["id"]]["status"] != "complete"
    ]
    if incomplete:
        raise StudioError("Cannot advance; incomplete gates: " + ", ".join(incomplete))
    blocking_items = [
        item["id"]
        for item in work.get("items", [])
        if item.get("phase_run") == project["lifecycle"]["active_run"]
        and item.get("status") not in {"done", "rejected", "cancelled"}
    ]
    if blocking_items:
        raise StudioError(
            "Cannot advance; unfinished work items in current phase: "
            + ", ".join(blocking_items)
        )
    allowed = current.get("next", [])
    destination = args.to or (allowed[0] if len(allowed) == 1 else None)
    if not destination:
        raise StudioError("Specify --to because the current phase has multiple next phases")
    if destination not in allowed:
        raise StudioError(f"Illegal phase transition: {current_id} -> {destination}")
    approved_by = require_user_actor(project, args.approved_by, "Phase approver")
    reason = require_text(args.reason, "Phase transition reason")

    timestamp = utc_now()
    active_run_id = project["lifecycle"]["active_run"]
    active_runs = [run for run in project["phase_runs"] if run.get("id") == active_run_id]
    if len(active_runs) != 1 or active_runs[0].get("phase") != current_id:
        raise StudioError("Active phase run does not match lifecycle state")
    completed_run = active_runs[0]
    completed_run.update(
        {
            "completed_at": timestamp,
            "approved_by": approved_by,
            "reason": reason,
            "gates": copy.deepcopy(project["gate_progress"][current_id]),
        }
    )
    next_run_number = project["lifecycle"]["next_run_id"]
    if not isinstance(next_run_number, int) or isinstance(next_run_number, bool):
        raise StudioError("Invalid lifecycle.next_run_id")
    started_run_id = f"R-{next_run_number:04d}"
    project["phase_runs"].append(
        {
            "id": started_run_id,
            "phase": destination,
            "started_at": timestamp,
            "started_by": approved_by,
            "completed_at": None,
            "approved_by": None,
            "reason": None,
            "gates": None,
        }
    )
    project["gate_progress"][destination] = initial_phase_gate_progress(
        profile, destination
    )
    project["lifecycle"].update(
        {
            "phase": destination,
            "active_run": started_run_id,
            "next_run_id": next_run_number + 1,
        }
    )
    project["updated_at"] = timestamp
    project["history"].append(
        {
            "type": "phase_advanced",
            "at": timestamp,
            "by": approved_by,
            "summary": f"{current_id} -> {destination}: {reason}",
            "reason": reason,
            "from": current_id,
            "to": destination,
            "completed_run_id": active_run_id,
            "started_run_id": started_run_id,
        }
    )
    _, project_path, _ = project_paths(target)
    write_json(project_path, project)
    render_status(target, project, work, profile)
    print(f"Advanced phase {current_id} -> {destination}")
    return 0


def find_work_item(work: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in work.get("items", []):
        if item.get("id") == item_id:
            return item
    raise StudioError(f"Unknown work item: {item_id}")


def normalized_spec_path(target: Path, value: str) -> str:
    candidate = Path(value).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (target / candidate).resolve()
    if not resolved.is_file():
        raise StudioError(f"Spec file does not exist: {resolved}")
    specs_root = target / STUDIO_DIR / "specs"
    if specs_root.is_symlink() or not specs_root.is_dir():
        raise StudioError("studio/specs must be a real directory")
    try:
        resolved.relative_to(specs_root.resolve())
    except ValueError:
        raise StudioError("Spec file must be inside studio/specs")
    canonical = resolved.relative_to(target).as_posix()
    if not canonical.startswith("studio/specs/"):
        raise StudioError("Canonical spec path must start with studio/specs/")
    return canonical


def stored_spec_path_is_canonical(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and len(path.parts) > 2
        and path.parts[:2] == (STUDIO_DIR, "specs")
        and path.as_posix() == value
    )


def hash_spec(target: Path, stored_path: str) -> str:
    normalized = normalized_spec_path(target, stored_path)
    content = read_regular_bytes(target / Path(normalized))
    canonical_content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical_content).hexdigest()


def require_text_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise StudioError(f"Deliverable contract field '{label}' must be an array")
    cleaned: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise StudioError(
                f"Deliverable contract field '{label}' entry {index} must be non-empty text"
            )
        cleaned.append(entry.strip())
    if not cleaned and not allow_empty:
        raise StudioError(f"Deliverable contract field '{label}' must not be empty")
    if len(cleaned) != len(set(cleaned)):
        raise StudioError(f"Deliverable contract field '{label}' contains duplicates")
    return cleaned


def validate_deliverable_contract(data: dict[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "version",
        "type",
        "fidelity",
        "purpose",
        "proves",
        "does_not_prove",
        "remaining_steps",
        "evidence",
        "unknowns",
    }
    missing = expected_fields - set(data)
    unknown = set(data) - expected_fields
    if missing:
        raise StudioError(
            "Deliverable contract is missing fields: " + ", ".join(sorted(missing))
        )
    if unknown:
        raise StudioError(
            "Deliverable contract has unknown fields: " + ", ".join(sorted(unknown))
        )
    if data.get("version") != DELIVERABLE_CONTRACT_VERSION:
        raise StudioError(
            f"Deliverable contract version must be {DELIVERABLE_CONTRACT_VERSION}"
        )
    deliverable_type = require_text(data.get("type"), "Deliverable type")
    purpose = require_text(data.get("purpose"), "Deliverable purpose")
    fidelity = data.get("fidelity")
    if not isinstance(fidelity, str) or fidelity not in VALID_FIDELITIES:
        raise StudioError(
            "Deliverable contract fidelity must be one of: "
            + ", ".join(sorted(VALID_FIDELITIES))
        )
    proves = require_text_list(data.get("proves"), "proves")
    does_not_prove = require_text_list(data.get("does_not_prove"), "does_not_prove")
    remaining_steps = require_text_list(
        data.get("remaining_steps"), "remaining_steps", allow_empty=True
    )
    evidence = require_text_list(data.get("evidence"), "evidence")
    unknowns = require_text_list(data.get("unknowns"), "unknowns", allow_empty=True)
    if fidelity in {"exploratory", "placeholder", "prototype"} and not remaining_steps:
        raise StudioError(
            f"Deliverable contract fidelity '{fidelity}' requires remaining_steps"
        )
    return {
        "version": DELIVERABLE_CONTRACT_VERSION,
        "type": deliverable_type,
        "fidelity": fidelity,
        "purpose": purpose,
        "proves": proves,
        "does_not_prove": does_not_prove,
        "remaining_steps": remaining_steps,
        "evidence": evidence,
        "unknowns": unknowns,
    }


def load_deliverable_contract(target: Path, stored_path: str) -> dict[str, Any]:
    normalized = normalized_spec_path(target, stored_path)
    try:
        content = read_text_exact(target / Path(normalized)).replace("\r\n", "\n").replace(
            "\r", "\n"
        )
    except UnicodeDecodeError as exc:
        raise StudioError(f"Spec must be UTF-8: {normalized}") from exc
    if content.count(DELIVERABLE_CONTRACT_MARKER) != 1:
        raise StudioError(
            f"Spec must contain exactly one {DELIVERABLE_CONTRACT_MARKER} marker"
        )
    after_marker = content.split(DELIVERABLE_CONTRACT_MARKER, 1)[1].lstrip()
    if not after_marker.startswith("```json\n"):
        raise StudioError("Deliverable contract marker must be followed by a JSON code block")
    json_start = len("```json\n")
    json_end = after_marker.find("\n```", json_start)
    if json_end < 0:
        raise StudioError("Deliverable contract JSON code block is not closed")
    payload = parse_json_object(
        after_marker[json_start:json_end], f"deliverable contract in {normalized}"
    )
    return validate_deliverable_contract(payload)


def require_current_approved_spec(target: Path, item: dict[str, Any]) -> str:
    spec = item.get("spec")
    approved_hash = item.get("approved_spec_sha256")
    if not isinstance(spec, str) or not spec:
        raise StudioError("Approved work item has no attached spec")
    if not isinstance(approved_hash, str) or not approved_hash:
        raise StudioError("Work item has no approved spec hash")
    current_hash = hash_spec(target, spec)
    if current_hash != approved_hash:
        raise StudioError(
            "Spec changed after approval; move the item back to proposed and approve it again"
        )
    return current_hash


RECOVERABLE_SPEC_ERRORS = (
    "spec changed after approval",
    ": Spec file does not exist",
    ": Spec file must be inside studio/specs",
    "has an invalid spec path",
    "spec path is not canonical",
    "without a spec",
    "has no valid approved spec hash",
    "has no approval actor",
    "has no approval timestamp",
    "approval hash does not match history",
    "approval metadata does not match history",
)
RECOVERABLE_CONTRACT_ERRORS = (
    "requires a spec with a deliverable contract",
    "Deliverable contract",
    "deliverable contract",
    "has no stored deliverable contract",
)
RECOVERABLE_STATUS_ERRORS = (
    "Missing generated studio/STATUS.md",
    "studio/STATUS.md does not match project state",
)


def item_spec_needs_repair(target: Path, item: dict[str, Any]) -> bool:
    status = item.get("status")
    approved_state = isinstance(status, str) and status in {
        "approved",
        "in_progress",
        "review",
        "done",
    }
    spec = item.get("spec")
    if spec is None:
        return approved_state
    if not isinstance(spec, str) or not spec.strip():
        return True
    try:
        normalized = normalized_spec_path(target, spec)
    except StudioError:
        return True
    if normalized != spec:
        return True
    if approved_state:
        approved_hash = item.get("approved_spec_sha256")
        return not isinstance(approved_hash, str) or hash_spec(target, spec) != approved_hash
    return False


def item_approval_metadata_needs_repair(item: dict[str, Any]) -> bool:
    status = item.get("status")
    if not isinstance(status, str) or status not in {
        "approved",
        "in_progress",
        "review",
        "done",
    }:
        return False
    approvals = [
        event
        for event in item.get("history", [])
        if isinstance(event, dict) and event.get("to") == "approved"
    ]
    if not approvals:
        return False
    latest = approvals[-1]
    return (
        not require_optional_text(item.get("approved_by"))
        or not require_optional_text(item.get("approved_at"))
        or item.get("approved_by") != latest.get("by")
        or item.get("approved_at") != latest.get("at")
        or item.get("approved_spec_sha256") != latest.get("spec_sha256")
    )


def item_contract_needs_repair(target: Path, item: dict[str, Any]) -> bool:
    if item.get("contract_version") != DELIVERABLE_CONTRACT_VERSION:
        return False
    if item.get("status") not in {"proposed", "approved", "in_progress", "review", "done"}:
        return False
    spec = item.get("spec")
    if not isinstance(spec, str) or not spec:
        return True
    try:
        current = load_deliverable_contract(target, spec)
    except StudioError:
        return True
    return item.get("deliverable_contract") != current


def command_work_list(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    _, work, _ = load_project(target)
    items = work.get("items", [])
    if not items:
        print("No work items")
        return 0
    for item in items:
        print(f"{item['id']}\t{item['status']}\t{item['kind']}\t{item['title']}")
    return 0


def command_work_add(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    number = work.get("next_id")
    if not isinstance(number, int) or number < 1:
        raise StudioError("work-items.json next_id must be a positive integer")
    title = require_text(args.title, "Work title")
    summary = require_text(args.summary, "Work summary")
    actor = require_text(args.by, "Work creator")
    timestamp = utc_now()
    item_id = f"W-{number:04d}"
    spec = normalized_spec_path(target, args.spec) if args.spec else None
    item = {
        "id": item_id,
        "title": title,
        "kind": args.kind,
        "summary": summary,
        "status": "draft",
        "phase": project["lifecycle"]["phase"],
        "phase_run": project["lifecycle"]["active_run"],
        "spec": spec,
        "contract_version": DELIVERABLE_CONTRACT_VERSION,
        "deliverable_contract": None,
        "approved_spec_sha256": None,
        "approved_at": None,
        "approved_by": None,
        "checkpoint": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "history": [
            {
                "from": None,
                "to": "draft",
                "at": timestamp,
                "by": actor,
                "reason": "Work item created",
                "evidence": None,
            }
        ],
    }
    work["items"].append(item)
    work["next_id"] = number + 1
    work["updated_at"] = timestamp
    _, _, work_path = project_paths(target)
    write_json(work_path, work)
    render_status(target, project, work, profile)
    print(f"Created {item_id}: {item['title']}")
    return 0


def command_work_attach_spec(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    project, work, profile = load_project(target)
    item = find_work_item(work, args.item_id)
    if item["status"] not in {"draft", "proposed"}:
        raise StudioError("A spec can only be attached to a draft or proposed work item")
    ignored = (
        RECOVERABLE_SPEC_ERRORS + RECOVERABLE_STATUS_ERRORS
        if item_spec_needs_repair(target, item)
        else ()
    )
    require_valid_target(target, ignored)
    item["spec"] = normalized_spec_path(target, args.spec)
    try:
        contract = load_deliverable_contract(target, item["spec"])
    except StudioError:
        if item["status"] == "proposed":
            raise
        contract = None
    item["deliverable_contract"] = contract
    timestamp = utc_now()
    item["updated_at"] = timestamp
    work["updated_at"] = timestamp
    _, _, work_path = project_paths(target)
    write_json(work_path, work)
    render_status(target, project, work, profile)
    print(f"Attached spec to {item['id']}: {item['spec']}")
    return 0


def command_work_checkpoint(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    require_valid_target(target)
    project, work, profile = load_project(target)
    item = find_work_item(work, args.item_id)
    if item.get("status") not in IN_FLIGHT_WORK_STATES:
        raise StudioError("Checkpoints are only allowed for in_progress or review work")
    summary = require_text(args.summary, "Checkpoint summary")
    next_action = require_text(args.next_action, "Checkpoint next action")
    actor = require_text(args.by, "Checkpoint author")
    blockers = [require_text(value, "Checkpoint blocker") for value in (args.blocker or [])]
    timestamp = utc_now()
    item["checkpoint"] = {
        "summary": summary,
        "next_action": next_action,
        "blockers": blockers,
        "by": actor,
        "updated_at": timestamp,
    }
    item["updated_at"] = timestamp
    work["updated_at"] = timestamp
    _, _, work_path = project_paths(target)
    write_json(work_path, work)
    render_status(target, project, work, profile)
    print(f"Checkpointed {item['id']}: {next_action}")
    return 0


def command_work_move(args: argparse.Namespace) -> int:
    target = target_from_args(args)
    project, work, profile = load_project(target)
    item = find_work_item(work, args.item_id)
    source = item["status"]
    destination = args.state
    spec_repair_needed = item_spec_needs_repair(target, item)
    approval_repair_needed = item_approval_metadata_needs_repair(item)
    contract_repair_needed = item_contract_needs_repair(target, item)
    is_work_recovery = (
        destination in {"draft", "proposed"}
        and source in {"approved", "in_progress", "review", "rejected"}
        and (spec_repair_needed or approval_repair_needed or contract_repair_needed)
    )
    require_valid_target(
        target,
        RECOVERABLE_SPEC_ERRORS
        + RECOVERABLE_CONTRACT_ERRORS
        + RECOVERABLE_STATUS_ERRORS
        if is_work_recovery
        else (),
    )
    if source not in WORK_TRANSITIONS or destination not in WORK_TRANSITIONS[source]:
        raise StudioError(f"Illegal work transition: {source} -> {destination}")
    if (
        destination in {"draft", "proposed", "approved", "in_progress", "review"}
        and (
            item.get("phase") != project["lifecycle"]["phase"]
            or item.get("phase_run") != project["lifecycle"]["active_run"]
        )
    ):
        raise StudioError(
            "Cannot reactivate work from an archived phase run; create a new current-run item"
        )

    approved_hash: str | None = None
    detached_spec: str | None = None
    current_contract: dict[str, Any] | None = None
    if item.get("contract_version") == DELIVERABLE_CONTRACT_VERSION:
        if destination in {"proposed", "approved", "in_progress", "review", "done"}:
            if not item.get("spec"):
                raise StudioError(
                    f"Moving to {destination} requires a spec with a deliverable contract"
                )
            current_contract = load_deliverable_contract(target, item["spec"])
        if destination in {"in_progress", "review", "done"} and (
            item.get("deliverable_contract") != current_contract
        ):
            raise StudioError(
                "Deliverable contract changed after proposal; move the item back to proposed"
            )
    if destination in IN_FLIGHT_WORK_STATES:
        conflicting = [
            candidate["id"]
            for candidate in work.get("items", [])
            if candidate.get("id") != item.get("id")
            and candidate.get("status") in IN_FLIGHT_WORK_STATES
        ]
        if conflicting:
            raise StudioError(
                "Finish or pause the current in-flight work item before starting another: "
                + ", ".join(conflicting)
            )
    if destination == "approved":
        if not args.approved_by:
            raise StudioError("Moving to approved requires --approved-by")
        if not item.get("spec"):
            raise StudioError("Moving to approved requires an attached spec")
        approved_hash = hash_spec(target, item["spec"])
    elif destination == "done":
        if not args.approved_by:
            raise StudioError("Moving to done requires user acceptance via --approved-by")
        require_current_approved_spec(target, item)
    elif destination in {"in_progress", "review", "done"}:
        require_current_approved_spec(target, item)

    evidence = require_text(args.evidence, "Review evidence") if destination in {"review", "done"} else None
    if args.evidence and evidence is None:
        evidence = require_text(args.evidence, "Evidence")

    actor = (
        require_user_actor(project, args.approved_by, "Work approver")
        if destination in {"approved", "done"}
        else require_text(args.by, "Transition actor")
    )
    reason = args.reason.strip() if args.reason and args.reason.strip() else None
    if destination == "proposed" and source in {"approved", "in_progress", "review"}:
        reason = require_text(args.reason, "Scope change reason")
    if is_work_recovery:
        reason = require_text(args.reason, "Work recovery reason")
        if spec_repair_needed and item.get("spec"):
            try:
                recovered_spec = normalized_spec_path(target, item["spec"])
            except (StudioError, TypeError):
                detached_spec = str(item["spec"])
                item["spec"] = None
            else:
                if recovered_spec != item["spec"]:
                    detached_spec = str(item["spec"])
                    item["spec"] = None
    timestamp = utc_now()
    item["status"] = destination
    item["updated_at"] = timestamp
    if current_contract is not None and destination in {"proposed", "approved"}:
        item["deliverable_contract"] = current_contract
    if destination == "approved":
        item["approved_spec_sha256"] = approved_hash
        item["approved_at"] = timestamp
        item["approved_by"] = actor
    elif destination == "proposed":
        item["approved_spec_sha256"] = None
        item["approved_at"] = None
        item["approved_by"] = None
        item["checkpoint"] = None
    elif destination == "draft":
        item["deliverable_contract"] = None
        item["checkpoint"] = None
    item["history"].append(
        {
            "from": source,
            "to": destination,
            "at": timestamp,
            "by": actor,
            "reason": reason,
            "evidence": evidence,
            "spec_sha256": approved_hash,
            "detached_spec": detached_spec,
        }
    )
    work["updated_at"] = timestamp
    _, _, work_path = project_paths(target)
    write_json(work_path, work)
    render_status(target, project, work, profile)
    print(f"Moved {item['id']}: {source} -> {destination}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project Studio state manager")
    commands = parser.add_subparsers(dest="command", required=True)

    profiles = commands.add_parser("profiles", help="List available project profiles")
    profiles.set_defaults(func=command_profiles)

    init = commands.add_parser("init", help="Initialize project-local Studio state")
    init.add_argument("target", nargs="?", default=".")
    init.add_argument("--profile", required=True, choices=available_profile_ids())
    init.add_argument("--name", required=True)
    init.add_argument("--owner", default="User")
    init.add_argument("--idea")
    init.add_argument("--initialized-by", default="Codex")
    init.set_defaults(func=command_init)

    validate = commands.add_parser("validate", help="Validate Studio state")
    validate.add_argument("target", nargs="?", default=".")
    validate.set_defaults(func=command_validate)

    repair = commands.add_parser(
        "repair", help="Refresh managed instructions and generated status"
    )
    repair.add_argument("target", nargs="?", default=".")
    repair.set_defaults(func=command_repair)

    status = commands.add_parser("status", help="Render and print project status")
    status.add_argument("target", nargs="?", default=".")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    brief = commands.add_parser(
        "brief", help="Print a compact recovery or subagent handoff brief"
    )
    brief.add_argument("target", nargs="?", default=".")
    brief.add_argument("--item")
    brief.add_argument("--json", action="store_true")
    brief.set_defaults(func=command_brief)

    project = commands.add_parser("project", help="Manage project governance metadata")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_owner = project_commands.add_parser(
        "set-owner", help="Set the owner on a legacy project"
    )
    project_owner.add_argument("target", nargs="?", default=".")
    project_owner.add_argument("--owner", required=True)
    project_owner.set_defaults(func=command_project_set_owner)

    gate = commands.add_parser("gate", help="Inspect or complete phase gates")
    gate_commands = gate.add_subparsers(dest="gate_command", required=True)
    gate_list = gate_commands.add_parser("list")
    gate_list.add_argument("target", nargs="?", default=".")
    gate_list.set_defaults(func=command_gate_list)
    gate_complete = gate_commands.add_parser("complete")
    gate_complete.add_argument("target")
    gate_complete.add_argument("gate_id")
    gate_complete.add_argument("--evidence", required=True)
    gate_complete.add_argument("--by", required=True)
    gate_complete.set_defaults(func=command_gate_complete)
    gate_reopen = gate_commands.add_parser("reopen")
    gate_reopen.add_argument("target")
    gate_reopen.add_argument("gate_id")
    gate_reopen.add_argument("--reason", required=True)
    gate_reopen.add_argument("--by", required=True)
    gate_reopen.set_defaults(func=command_gate_reopen)

    phase = commands.add_parser("phase", help="Advance the project lifecycle")
    phase_commands = phase.add_subparsers(dest="phase_command", required=True)
    phase_advance = phase_commands.add_parser("advance")
    phase_advance.add_argument("target", nargs="?", default=".")
    phase_advance.add_argument("--to")
    phase_advance.add_argument("--approved-by", required=True)
    phase_advance.add_argument("--reason", required=True)
    phase_advance.set_defaults(func=command_phase_advance)

    work = commands.add_parser("work", help="Manage bounded work items")
    work_commands = work.add_subparsers(dest="work_command", required=True)
    work_list = work_commands.add_parser("list")
    work_list.add_argument("target", nargs="?", default=".")
    work_list.set_defaults(func=command_work_list)
    work_add = work_commands.add_parser("add")
    work_add.add_argument("target")
    work_add.add_argument("--title", required=True)
    work_add.add_argument("--kind", choices=["feature", "bug", "experiment", "chore"], required=True)
    work_add.add_argument("--summary", required=True)
    work_add.add_argument("--spec")
    work_add.add_argument("--by", default="Codex")
    work_add.set_defaults(func=command_work_add)
    work_spec = work_commands.add_parser("attach-spec")
    work_spec.add_argument("target")
    work_spec.add_argument("item_id")
    work_spec.add_argument("--spec", required=True)
    work_spec.set_defaults(func=command_work_attach_spec)
    work_checkpoint = work_commands.add_parser(
        "checkpoint", help="Persist progress for recovery or handoff"
    )
    work_checkpoint.add_argument("target")
    work_checkpoint.add_argument("item_id")
    work_checkpoint.add_argument("--summary", required=True)
    work_checkpoint.add_argument("--next", dest="next_action", required=True)
    work_checkpoint.add_argument("--blocker", action="append")
    work_checkpoint.add_argument("--by", default="Codex")
    work_checkpoint.set_defaults(func=command_work_checkpoint)
    work_move = work_commands.add_parser("move")
    work_move.add_argument("target")
    work_move.add_argument("item_id")
    work_move.add_argument("state", choices=sorted(VALID_WORK_STATES))
    work_move.add_argument("--by", default="Codex")
    work_move.add_argument("--approved-by")
    work_move.add_argument("--reason")
    work_move.add_argument("--evidence")
    work_move.set_defaults(func=command_work_move)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if hasattr(args, "target"):
            target = Path(args.target).expanduser().resolve()
            args.target = target
            try:
                target_before_lock = target.lstat()
            except FileNotFoundError:
                target_before_lock = None
            with project_lock(target):
                try:
                    target_after_lock = target.lstat()
                except FileNotFoundError:
                    target_after_lock = None
                if target_before_lock is None:
                    if target_after_lock is not None:
                        raise StudioError(
                            f"Target appeared or changed while waiting for its lock: {target}"
                        )
                elif (
                    target_after_lock is None
                    or stat.S_ISLNK(target_after_lock.st_mode)
                    or not stat.S_ISDIR(target_after_lock.st_mode)
                    or not os.path.samestat(target_before_lock, target_after_lock)
                ):
                    raise StudioError(
                        f"Target appeared or changed while waiting for its lock: {target}"
                    )
                return int(args.func(args))
        return int(args.func(args))
    except StudioError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (
        AttributeError,
        KeyError,
        TypeError,
        IndexError,
        OSError,
        ValueError,
        RuntimeError,
        RecursionError,
    ) as exc:
        print(f"ERROR: Invalid or inaccessible Studio state: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
