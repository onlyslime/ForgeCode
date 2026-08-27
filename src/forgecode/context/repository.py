"""Deterministic, bounded repository map and context selection.

This is intentionally a conservative standard-library scanner rather than a
full parser or vector database. It produces evidence the agent can explain:
files, language/build/test hints, symbols, omissions and selected context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable

from ..security.redaction import redact_text
from ..security.workspace import WorkspaceGuard
from ..context_policy import is_ignored_context_path

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".forgecode", "dist", "build", "tmp", "temp"}
_SKIP_NAMES = {".env", ".env.local", "credentials.json", "id_rsa"}


def _is_ignored(path: Path, guard: WorkspaceGuard) -> bool:
    return is_ignored_context_path(guard, path)


_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".db", ".sqlite", ".pyc", ".dll", ".exe", ".so", ".class", ".woff", ".woff2"}
_LANGUAGES = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React", ".jsx": "JavaScript/React", ".go": "Go", ".rs": "Rust", ".java": "Java", ".c": "C", ".cpp": "C++", ".h": "C/C++ header", ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".kt": "Kotlin", ".swift": "Swift"}
_SYMBOL_PATTERNS = {
    ".py": re.compile(r"^(?:\s*)(?:def|class)\s+([A-Za-z_]\w*)", re.M),
    ".js": re.compile(r"^(?:\s*)(?:export\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)", re.M),
    ".ts": re.compile(r"^(?:\s*)(?:export\s+)?(?:function|class|interface|type)\s+([A-Za-z_$][\w$]*)", re.M),
    ".go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
    ".rs": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)", re.M),
    ".java": re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)", re.M),
}


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    size: int
    mtime_ns: int
    language: str | None
    symbols: tuple[str, ...] = ()
    digest: str | None = None
    summary: str = ""


@dataclass(frozen=True)
class RepositorySnapshot:
    root: str
    workspace_identity: str
    captured_at: str
    files: tuple[RepositoryFile, ...]
    language_counts: dict[str, int]
    build_files: tuple[str, ...]
    test_files: tuple[str, ...]
    git_status: str
    omitted: int = 0
    errors: tuple[str, ...] = ()
    truncated: bool = False
    scan_limit: int = 2_000


@dataclass(frozen=True)
class ContextPlan:
    task: str
    sections: tuple[dict[str, str], ...]
    selected_paths: tuple[str, ...]
    omitted: int
    budget_chars: int

    def render(self) -> str:
        pieces = [section["content"] for section in self.sections]
        if self.omitted:
            pieces.append(f"[context omitted {self.omitted} files to stay within {self.budget_chars} characters]")
        return "\n\n".join(pieces)


@dataclass(frozen=True)
class RepositoryMap:
    snapshot: RepositorySnapshot

    def to_dict(self) -> dict:
        return asdict(self.snapshot)

    def plan_context(self, task: str, *, budget_chars: int = 20_000, max_file_chars: int = 4_000) -> ContextPlan:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        if isinstance(budget_chars, bool) or not isinstance(budget_chars, int) or budget_chars < 256:
            raise ValueError("budget_chars must be an integer >= 256")
        tokens = {token.lower() for token in re.findall(r"[\w.-]+", task) if len(token) > 2}
        ranked: list[tuple[int, RepositoryFile]] = []
        for item in self.snapshot.files:
            path_tokens = set(re.findall(r"[\w.-]+", item.path.lower()))
            symbol_tokens = {symbol.lower() for symbol in item.symbols}
            score = len(tokens & (path_tokens | symbol_tokens)) * 10
            if any(token in item.path.lower() for token in ("test", "spec")) and any(token in tokens for token in ("test", "bug", "failure", "verify")):
                score += 3
            if item.language and item.language.lower() in tokens:
                score += 2
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].path))
        sections: list[dict[str, str]] = [{"priority": "system", "content": f"Repository task: {redact_text(task)[:2_000]}"}]
        used = len(sections[0]["content"])
        selected: list[str] = []
        omitted = 0
        for score, item in ranked:
            content = f"File {item.path} ({item.language or 'text'}, {item.size} bytes) symbols: {', '.join(item.symbols) or 'none'}"
            if item.summary:
                content += f"\n{item.summary[:max_file_chars]}"
            if used + len(content) + 2 > budget_chars:
                omitted += 1
                continue
            sections.append({"priority": "relevant" if score else "repository", "content": content})
            selected.append(item.path)
            used += len(content) + 2
        omitted += self.snapshot.omitted
        return ContextPlan(task, tuple(sections), tuple(selected), omitted, budget_chars)


class RepositoryMapBuilder:
    def __init__(self, guard: WorkspaceGuard, *, max_files: int = 2_000, max_file_bytes: int = 256_000, include_symbols: bool = True):
        if isinstance(max_files, bool) or max_files < 1 or max_files > 20_000:
            raise ValueError("max_files must be between 1 and 20000")
        if isinstance(max_file_bytes, bool) or max_file_bytes < 1 or max_file_bytes > 2_000_000:
            raise ValueError("max_file_bytes must be between 1 and 2000000")
        self.guard = guard
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.include_symbols = include_symbols

    def build(self) -> RepositoryMap:
        import datetime
        files: list[RepositoryFile] = []
        errors: list[str] = []
        omitted = 0
        candidates: list[Path] = []
        try:
            reached_limit = False
            for directory, names, filenames in os.walk(self.guard.root, topdown=True, followlinks=False):
                directory_path = Path(directory)
                safe_names: list[str] = []
                for name in sorted(names, key=str.lower):
                    candidate = directory_path / name
                    try:
                        if not _is_ignored(candidate, self.guard) and self.guard.resolve(candidate).is_relative_to(self.guard.root):
                            safe_names.append(name)
                    except (OSError, ValueError):
                        omitted += 1
                names[:] = safe_names
                for name in sorted(filenames, key=str.lower):
                    path = directory_path / name
                    if _is_ignored(path, self.guard):
                        continue
                    if len(candidates) >= self.max_files:
                        omitted += 1
                        reached_limit = True
                        names[:] = []
                        break
                    try:
                        safe_path = self.guard.resolve(path)
                        if safe_path.is_file() and safe_path == path.absolute():
                            candidates.append(safe_path)
                    except (OSError, ValueError) as exc:
                        errors.append(f"{path.name}: {type(exc).__name__}")
                if reached_limit:
                    break
            truncated = reached_limit
        except OSError as exc:
            errors.append(f"scan: {type(exc).__name__}: {exc}")
            truncated = False
        candidates.sort(key=lambda item: item.as_posix().lower())
        language_counts: dict[str, int] = {}
        for path in candidates:
            try:
                stat = path.stat()
                resolved_before = self.guard.resolve(path, must_exist=True)
                if resolved_before != path.absolute():
                    raise OSError("repository file changed into an unsafe path")
                relative = self.guard.relative(path)
                language = _LANGUAGES.get(path.suffix.lower())
                if language:
                    language_counts[language] = language_counts.get(language, 0) + 1
                if stat.st_size > self.max_file_bytes or path.suffix.lower() in _BINARY_SUFFIXES:
                    omitted += 1
                    continue
                raw = path.read_bytes()
                after_stat = path.stat()
                resolved_after = self.guard.resolve(path, must_exist=True)
                before_identity = (stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0))
                after_identity = (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0))
                if resolved_after != path.absolute() or before_identity != after_identity:
                    raise OSError("repository file changed while it was read")
                if b"\x00" in raw:
                    omitted += 1
                    continue
                text = raw.decode("utf-8")
                symbols = tuple(_SYMBOL_PATTERNS.get(path.suffix.lower(), re.compile(r"$^" )).findall(text)[:100]) if self.include_symbols else ()
                summary_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith(("#", "//", "/*", "*"))][:5]
                digest = hashlib.sha256(raw).hexdigest()
                files.append(RepositoryFile(relative, stat.st_size, stat.st_mtime_ns, language, symbols, digest, redact_text("\n".join(summary_lines)[:1_000])))
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(f"{path.name}: {type(exc).__name__}")
        git_status = "not a Git repository"
        if (self.guard.root / ".git").exists():
            try:
                completed = subprocess.run(["git", "status", "--short"], cwd=self.guard.root, capture_output=True, text=True, timeout=5, check=False)
                git_status = completed.stdout.strip() if completed.returncode == 0 else "Git status unavailable"
                git_status = git_status or "clean"
            except (OSError, subprocess.TimeoutExpired):
                git_status = "Git status unavailable"
        build_names = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "Makefile")
        test_files = tuple(item.path for item in files if Path(item.path).name.lower().startswith(("test_", "test.")) or "/tests/" in f"/{item.path.lower()}/")[:200]
        snapshot = RepositorySnapshot(".", hashlib.sha256(str(self.guard.root).encode()).hexdigest()[:32], datetime.datetime.now(datetime.timezone.utc).isoformat(), tuple(sorted(files, key=lambda item: item.path)), dict(sorted(language_counts.items())), tuple(name for name in build_names if (self.guard.root / name).is_file()), test_files, redact_text(git_status)[:4_000], omitted, tuple(errors[:100]), truncated, self.max_files)
        return RepositoryMap(snapshot)


__all__ = ["ContextPlan", "RepositoryFile", "RepositoryMap", "RepositoryMapBuilder", "RepositorySnapshot"]
