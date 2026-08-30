"""Bounded, read-only Git inspection tools."""
from __future__ import annotations

import subprocess
import re
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .base import ToolContext, ToolDefinition, ToolResult


_WORKTREE_STATE = Path(".forgecode") / "worktrees.json"
_MAX_WORKTREE_RECORDS = 64
_MAX_WORKTREE_STATE_BYTES = 256_000
_WORKTREE_STATE_LOCK = threading.RLock()


def _worktree_records(guard) -> dict[str, dict[str, str]]:
    """Read bounded, non-sensitive worktree ownership metadata."""
    path = guard.resolve(_WORKTREE_STATE)
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > _MAX_WORKTREE_STATE_BYTES:
            raise ValueError("ownership metadata exceeds size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid worktree ownership metadata") from exc
    records = value.get("worktrees") if isinstance(value, dict) else None
    if not isinstance(records, dict):
        raise ValueError("worktree ownership records must be an object")
    if len(records) > _MAX_WORKTREE_RECORDS:
        return {}
    cleaned = {}
    for key, item in records.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", key) or not isinstance(item, dict):
            raise ValueError("invalid worktree ownership record")
        fields = {str(field): value for field, value in item.items() if field in {"run_id", "branch", "path"}}
        if any(not isinstance(value, str) or len(value) > 256 or any(ch in value for ch in "\r\n") for value in fields.values()):
            raise ValueError("invalid worktree ownership field")
        path_value = fields.get("path")
        if path_value is not None:
            path_parts = path_value.replace("\\", "/").split("/")
            if Path(path_value).is_absolute() or any(part in {"", ".", ".."} for part in path_parts):
                raise ValueError("invalid worktree ownership path")
        cleaned[key] = fields
    return cleaned


def _save_worktree_records(guard, records: dict[str, dict[str, str]]) -> None:
    path = guard.resolve(_WORKTREE_STATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "worktrees": dict(list(records.items())[:_MAX_WORKTREE_RECORDS])}
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    temporary = None
    with _WORKTREE_STATE_LOCK:
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix="worktrees.", suffix=".tmp", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass


class GitStatusTool:
    definition = ToolDefinition("git_status", "Show concise tracked/untracked changes for the workspace.", {"type": "object", "properties": {"porcelain": {"type": "boolean"}}, "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        porcelain = arguments.get("porcelain", True)
        if not isinstance(porcelain, bool):
            raise ValueError("porcelain must be a boolean")
        command = ["git", "status", "--short"] if porcelain else ["git", "status"]
        if context.remaining_seconds(15.0) <= 0:
            return ToolResult(False, "git status skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        try:
            result = subprocess.run(command, cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git status failed: {type(exc).__name__}", {"error": "git_status_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000] or "not a Git repository", {"exit_code": result.returncode})
        return ToolResult(True, result.stdout[:4_000] or "working tree clean", {"exit_code": 0})


class GitDiffTool:
    definition = ToolDefinition("git_diff", "Show a bounded Git diff for review before or after edits.", {"type": "object", "properties": {"staged": {"type": "boolean"}, "path": {"type": "string"}}, "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        command = ["git", "diff", "--no-ext-diff", "--unified=3"]
        staged = arguments.get("staged", False)
        if not isinstance(staged, bool):
            raise ValueError("staged must be a boolean")
        if staged:
            command.append("--cached")
        path = arguments.get("path")
        if path:
            if not isinstance(path, str):
                raise ValueError("path must be a string")
            command.extend(["--", str(context.guard.relative(context.guard.resolve(path)))])
        if context.remaining_seconds(20.0) <= 0:
            return ToolResult(False, "git diff skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        try:
            result = subprocess.run(command, cwd=context.guard.root, capture_output=True, text=True, timeout=min(20.0, context.remaining_seconds(20.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git diff failed: {type(exc).__name__}", {"error": "git_diff_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000], {"exit_code": result.returncode})
        return ToolResult(True, result.stdout[:20_000] or "no differences", {"exit_code": 0})


class GitLogTool:
    definition = ToolDefinition("git_log", "Show recent commit history with hash, subject, author, and date.", {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if context.remaining_seconds(15.0) <= 0:
            return ToolResult(False, "git log skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        try:
            result = subprocess.run(["git", "log", f"-{limit}", "--date=short", "--pretty=format:%h %ad %an %s"], cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git log failed: {type(exc).__name__}", {"error": "git_log_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000], {"exit_code": result.returncode})
        return ToolResult(True, result.stdout[:8_000] or "no commits", {"count": len(result.stdout.splitlines()), "exit_code": 0})


class GitWorktreeListTool:
    definition = ToolDefinition("git_worktrees", "List Git worktrees without creating, switching, or mutating them.", {"type": "object", "additionalProperties": False})

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        if context.cancelled:
            return ToolResult(False, "git worktree listing cancelled before execution", {"error": "cancelled"})
        if context.remaining_seconds(15.0) <= 0:
            return ToolResult(False, "git worktree listing skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        try:
            records = _worktree_records(context.guard)
        except (OSError, ValueError):
            return ToolResult(False, "managed worktree metadata is unavailable", {"error": "worktree_metadata_unavailable"})
        try:
            result = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git worktree list failed: {type(exc).__name__}", {"error": "git_worktree_list_failed"})
        if result.returncode != 0:
            return ToolResult(False, result.stderr.strip()[:4_000] or "not a Git repository", {"exit_code": result.returncode})
        rows = []
        current = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current: rows.append(current)
                raw = line[9:].strip()
                try: path = context.guard.relative(context.guard.resolve(raw))
                except Exception: path = "<outside-workspace>"
                current = {"path": path}
            elif current is not None and line.startswith("HEAD "):
                current["head"] = line[5:].strip()[:80]
            elif current is not None and line == "bare":
                current["bare"] = True
            elif current is not None and line == "detached":
                current["detached"] = True
            elif current is not None and line.startswith("branch "):
                current["branch"] = line[7:].removeprefix("refs/heads/")[:160]
        if current: rows.append(current)
        for row in rows:
            for name, record in records.items():
                if record.get("path") == row.get("path"):
                    row["name"] = name
                    if record.get("run_id"):
                        row["run_id"] = record["run_id"]
                    break
        rows = rows[:64]
        return ToolResult(True, "\n".join(f"{row.get('path')} {row.get('branch', 'detached')}" for row in rows) or "no worktrees", {"worktrees": rows, "count": len(rows), "exit_code": 0})


class GitWorktreeReconcileTool:
    """Compare Git worktrees with ForgeCode ownership records without mutation."""
    definition = ToolDefinition(
        "git_worktree_reconcile",
        "Reconcile Git worktree paths with ForgeCode session ownership metadata without changing either.",
        {"type": "object", "additionalProperties": False},
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        listed = GitWorktreeListTool(self.guard).execute({}, context)
        if not listed.ok:
            return listed
        try:
            records = _worktree_records(self.guard)
        except (OSError, ValueError):
            return ToolResult(False, "managed worktree metadata is unavailable", {"error": "worktree_metadata_unavailable"})
        rows = listed.metadata.get("worktrees", [])
        seen = set()
        findings = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = row.get("path")
            name = row.get("name")
            if isinstance(name, str) and name in records:
                seen.add(name)
                status = "healthy" if records[name].get("path") == path and records[name].get("run_id") else ("owner_missing" if not records[name].get("run_id") else "path_mismatch")
                findings.append({"name": name, "path": path, "status": status, "run_id": records[name].get("run_id")})
            elif isinstance(path, str):
                findings.append({"path": path, "status": "unmanaged"})
        for name, record in records.items():
            if name not in seen:
                findings.append({"name": name, "path": record.get("path"), "status": "missing_path", "run_id": record.get("run_id")})
        healthy = sum(item.get("status") == "healthy" for item in findings)
        text = "\n".join(f"{item.get('name', item.get('path'))}: {item['status']}" for item in findings) or "no worktrees"
        return ToolResult(True, text, {"worktrees": findings[:64], "count": len(findings[:64]), "healthy_count": healthy, "consistent": all(item.get("status") == "healthy" for item in findings)})


class GitWorktreeCreateTool:
    """Create an explicitly approved, workspace-local worktree."""
    definition = ToolDefinition(
        "git_worktree_create",
        "Create an approved Git worktree under .forgecode/worktrees for isolated edits.",
        {"type": "object", "properties": {"name": {"type": "string"}, "branch": {"type": "string"}, "start_point": {"type": "string"}}, "required": ["name", "branch"], "additionalProperties": False},
        side_effecting=True,
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        name, branch = arguments.get("name"), arguments.get("branch")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            raise ValueError("name must be 1-64 safe filename characters")
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch) or branch.startswith(("-", ".")):
            raise ValueError("branch must be a valid bounded branch name")
        start = arguments.get("start_point")
        if start is not None and (not isinstance(start, str) or not start.strip() or len(start) > 160 or start.startswith("-") or any(ch in start for ch in "\r\n")):
            raise ValueError("start_point must be a bounded Git ref")
        target = self.guard.resolve(str(Path(".forgecode") / "worktrees" / name))
        if target.exists():
            return ToolResult(False, "worktree target already exists", {"error": "worktree_exists"})
        if not context.request_approval(self.definition.name, {"name": name, "branch": branch}):
            return ToolResult(False, "git_worktree_create denied by approval policy", {"error": "approval_denied"})
        if context.cancelled:
            return ToolResult(False, "git_worktree_create cancelled before execution", {"error": "cancelled"})
        if context.remaining_seconds(45.0) <= 0:
            return ToolResult(False, "git_worktree_create skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        target.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "worktree", "add", "-b", branch, str(target), start or "HEAD"]
        try:
            result = subprocess.run(command, cwd=context.guard.root, capture_output=True, text=True, timeout=min(45.0, context.remaining_seconds(45.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            try:
                if target.is_dir() and not any(target.iterdir()):
                    target.rmdir()
            except OSError:
                pass
            return ToolResult(False, f"git worktree create failed: {type(exc).__name__}", {"error": "git_worktree_create_failed"})
        if result.returncode != 0:
            try:
                if target.is_dir() and not any(target.iterdir()):
                    target.rmdir()
            except OSError:
                pass
            return ToolResult(False, (result.stderr.strip() or "git worktree create failed")[:4_000], {"error": "git_worktree_create_failed", "exit_code": result.returncode})
        relative = self.guard.relative(target)
        metadata = {"run_id": context.run_id[:128] if isinstance(context.run_id, str) else "", "branch": branch, "path": relative}
        try:
            with _WORKTREE_STATE_LOCK:
                records = _worktree_records(self.guard)
                records[name] = metadata
                _save_worktree_records(self.guard, records)
        except (OSError, ValueError, TypeError) as exc:
            return ToolResult(False, "worktree created but ownership metadata could not be saved", {"error": "worktree_metadata_failed", "path": relative, "detail": type(exc).__name__})
        return ToolResult(True, f"created worktree {relative} on {branch}", {"path": relative, "branch": branch, "run_id": metadata["run_id"], "exit_code": 0})


class GitWorktreeRemoveTool:
    """Remove only worktrees created in the ForgeCode-managed directory."""
    definition = ToolDefinition(
        "git_worktree_remove",
        "Remove an approved ForgeCode-managed worktree under .forgecode/worktrees.",
        {"type": "object", "properties": {"name": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["name"], "additionalProperties": False},
        side_effecting=True,
    )

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        name = arguments.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            raise ValueError("name must be 1-64 safe filename characters")
        force = arguments.get("force", False)
        if not isinstance(force, bool):
            raise ValueError("force must be a boolean")
        target = self.guard.resolve(str(Path(".forgecode") / "worktrees" / name))
        if not target.exists():
            return ToolResult(False, "managed worktree does not exist", {"error": "worktree_missing"})
        try:
            records = _worktree_records(self.guard)
        except (OSError, ValueError):
            return ToolResult(False, "managed worktree metadata is unavailable", {"error": "worktree_metadata_unavailable"})
        if name not in records:
            return ToolResult(False, "worktree is not ForgeCode-managed", {"error": "worktree_unmanaged"})
        owner = records.get(name, {}).get("run_id")
        if owner and context.run_id and owner != context.run_id:
            return ToolResult(False, "worktree belongs to another session", {"error": "worktree_owner_mismatch"})
        if not context.request_approval(self.definition.name, {"name": name, "force": force}):
            return ToolResult(False, "git_worktree_remove denied by approval policy", {"error": "approval_denied"})
        if context.cancelled:
            return ToolResult(False, "git_worktree_remove cancelled before execution", {"error": "cancelled"})
        if context.remaining_seconds(45.0) <= 0:
            return ToolResult(False, "git_worktree_remove skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        command = ["git", "worktree", "remove"] + (["--force"] if force else []) + [str(target)]
        try:
            result = subprocess.run(command, cwd=context.guard.root, capture_output=True, text=True, timeout=min(45.0, context.remaining_seconds(45.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git worktree remove failed: {type(exc).__name__}", {"error": "git_worktree_remove_failed"})
        output = (result.stderr.strip() or result.stdout.strip() or "worktree removed")[:4_000]
        if result.returncode == 0 and name in records:
            records.pop(name, None)
            try:
                with _WORKTREE_STATE_LOCK:
                    records = _worktree_records(self.guard)
                    records.pop(name, None)
                    _save_worktree_records(self.guard, records)
            except (OSError, ValueError, TypeError):
                return ToolResult(False, "worktree removed but ownership metadata could not be updated", {"error": "worktree_metadata_failed", "name": name, "exit_code": 0})
        return ToolResult(result.returncode == 0, output, {"name": name, "exit_code": result.returncode})


class GitCommitTool:
    definition = ToolDefinition("git_commit", "Create a Git commit for current changes after explicit approval.", {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False}, side_effecting=True)

    def __init__(self, guard):
        self.guard = guard

    def execute(self, arguments, context):
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        denied = context.deny_if_plan(self.definition.name)
        if denied:
            return denied
        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 200:
            raise ValueError("commit message must be 1-200 characters")
        if not context.request_approval(self.definition.name, {"message": message}):
            return ToolResult(False, "git_commit denied by approval policy", {"error": "approval_denied"})
        if context.cancelled:
            return ToolResult(False, "git_commit cancelled before execution", {"error": "cancelled"})
        if context.remaining_seconds(30.0) <= 0:
            return ToolResult(False, "git_commit skipped because the run deadline has expired", {"error": "deadline_exceeded"})
        try:
            result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=context.guard.root, capture_output=True, text=True, timeout=min(15.0, context.remaining_seconds(15.0)), check=False)
            if result.returncode == 0:
                return ToolResult(False, "no staged changes; stage files before git_commit", {"error": "nothing_staged", "exit_code": 0})
            result = subprocess.run(["git", "commit", "-m", message], cwd=context.guard.root, capture_output=True, text=True, timeout=min(30.0, context.remaining_seconds(30.0)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(False, f"git commit failed: {type(exc).__name__}", {"error": "git_commit_failed"})
        output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()[:8_000]
        return ToolResult(result.returncode == 0, output or ("commit created" if result.returncode == 0 else "commit failed"), {"exit_code": result.returncode})
