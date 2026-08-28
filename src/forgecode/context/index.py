"""Persistent, incremental and explainable repository context index.

The index deliberately uses a small JSON document instead of a vector store or
downloaded model.  It is a cache, never an authority: every search result is
revalidated against the workspace before its bytes are returned.  The cache is
kept under ``.forgecode`` by default and can be deleted/rebuilt at any time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable

from ..context_policy import is_ignored_context_path, is_sensitive_context_path, may_contain_negated_context_path
from ..security.redaction import redact_text
from ..security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias


INDEX_SCHEMA_VERSION = 1
DEFAULT_INDEX_PATH = Path(".forgecode") / "context-index.json"
MAX_INDEX_BYTES = 32_000_000
MAX_INDEX_FILES = 20_000
MAX_FILE_BYTES = 2_000_000
MAX_QUERY_CHARS = 512
MAX_RESULTS = 200

_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".db", ".sqlite",
    ".pyc", ".dll", ".exe", ".so", ".class", ".woff", ".woff2", ".bin", ".dat",
}
_LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React",
    ".jsx": "JavaScript/React", ".go": "Go", ".rs": "Rust", ".java": "Java", ".c": "C",
    ".cpp": "C++", ".h": "C/C++ header", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
    ".kt": "Kotlin", ".swift": "Swift", ".md": "Markdown", ".toml": "TOML",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".txt": "Text",
}
_SYMBOL_PATTERNS = {
    ".py": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M),
    ".js": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)", re.M),
    ".ts": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_$][\w$]*)", re.M),
    ".go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
    ".rs": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)", re.M),
    ".java": re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)", re.M),
}


class ContextIndexError(ValueError):
    """The index is malformed, unsafe or cannot be persisted."""


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


@dataclass(frozen=True)
class IndexedFile:
    path: str
    size: int
    mtime_ns: int
    digest: str
    language: str | None
    lines: int
    readable: bool = True
    sensitive: bool = False
    ignored: bool = False
    binary: bool = False
    symbols: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Any) -> "IndexedFile":
        if not isinstance(raw, dict):
            raise ContextIndexError("index file entry must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise ContextIndexError("index file entry contains unknown fields")
        values = {name: raw.get(name) for name in allowed}
        if not isinstance(values["path"], str) or not values["path"] or len(values["path"]) > 4_000:
            raise ContextIndexError("index file path is invalid")
        if Path(values["path"]).is_absolute() or any(part in {"", ".", ".."} for part in values["path"].replace("\\", "/").split("/")):
            raise ContextIndexError("index file path is unsafe")
        for field in ("size", "mtime_ns", "lines"):
            value = values[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContextIndexError(f"index file {field} is invalid")
        if not isinstance(values["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", values["digest"]):
            raise ContextIndexError("index file digest is invalid")
        if values["language"] is not None and not isinstance(values["language"], str):
            raise ContextIndexError("index file language is invalid")
        if not isinstance(values["symbols"], (list, tuple)) or any(not isinstance(item, str) for item in values["symbols"]):
            raise ContextIndexError("index file symbols are invalid")
        for field in ("readable", "sensitive", "ignored", "binary"):
            if not isinstance(values[field], bool):
                raise ContextIndexError(f"index file {field} is invalid")
        return cls(
            path=values["path"], size=values["size"], mtime_ns=values["mtime_ns"], digest=values["digest"],
            language=values["language"], lines=values["lines"], readable=values["readable"],
            sensitive=values["sensitive"], ignored=values["ignored"], binary=values["binary"],
            symbols=tuple(values["symbols"][:100]),
        )


@dataclass(frozen=True)
class ContextSearchResult:
    path: str
    line: int
    snippet: str
    digest: str
    reason: str
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexReport:
    path: str
    files: int
    added: int
    updated: int
    removed: int
    omitted: int
    errors: tuple[str, ...]
    rebuilt: bool
    fingerprint: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fingerprint(files: Iterable[IndexedFile]) -> str:
    payload = "\n".join(f"{item.path}\0{item.size}\0{item.mtime_ns}\0{item.digest}" for item in sorted(files, key=lambda item: item.path))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path.lower(), pattern.lower())


class ContextIndex:
    """A safe local index with deterministic incremental rebuilds."""

    def __init__(self, guard: WorkspaceGuard, path: Path | None = None, *, max_files: int = MAX_INDEX_FILES, max_file_bytes: int = MAX_FILE_BYTES):
        if isinstance(max_files, bool) or not 1 <= max_files <= MAX_INDEX_FILES:
            raise ValueError("max_files must be between 1 and 20000")
        if isinstance(max_file_bytes, bool) or not 1 <= max_file_bytes <= MAX_FILE_BYTES:
            raise ValueError("max_file_bytes must be between 1 and 2000000")
        self.guard = guard
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.path = guard.resolve(path or DEFAULT_INDEX_PATH)
        self.last_search_issues: list[str] = []

    def _load(self) -> tuple[dict[str, IndexedFile], str | None, bool]:
        if not self.path.exists():
            return {}, None, False
        try:
            assert_no_path_alias(self.path)
            if not self.path.is_file() or self.path.stat().st_size > MAX_INDEX_BYTES:
                raise ContextIndexError("context index is not a regular file or exceeds the size limit")
            raw = json.loads(self.path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite_json)
            if not isinstance(raw, dict) or raw.get("schema_version") != INDEX_SCHEMA_VERSION:
                raise ContextIndexError("unsupported context index schema")
            entries = raw.get("files")
            if not isinstance(entries, list) or len(entries) > self.max_files:
                raise ContextIndexError("context index files are invalid")
            parsed = [IndexedFile.from_dict(item) for item in entries]
            by_path = {item.path: item for item in parsed}
            if len(by_path) != len(parsed):
                raise ContextIndexError("context index contains duplicate paths")
            stored_fp = raw.get("fingerprint")
            if stored_fp is not None and (not isinstance(stored_fp, str) or not re.fullmatch(r"[0-9a-f]{64}", stored_fp)):
                raise ContextIndexError("context index fingerprint is invalid")
            calculated_fp = _fingerprint(by_path.values())
            if stored_fp is not None and stored_fp != calculated_fp:
                raise ContextIndexError("context index fingerprint does not match entries")
            return by_path, stored_fp, False
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, ContextIndexError) as exc:
            raise ContextIndexError(f"cannot read context index: {type(exc).__name__}: {exc}") from exc

    def _candidates(self) -> tuple[list[Path], int, list[str]]:
        candidates: list[Path] = []
        errors: list[str] = []
        omitted = 0
        try:
            for directory, names, filenames in os.walk(self.guard.root, topdown=True, followlinks=False):
                directory_path = Path(directory)
                kept_names: list[str] = []
                for name in sorted(names, key=lambda value: value.lower()):
                    candidate = directory_path / name
                    try:
                        ignored = is_ignored_context_path(self.guard, candidate)
                        if (not ignored or may_contain_negated_context_path(self.guard, candidate)) and self.guard.resolve(candidate).is_relative_to(self.guard.root):
                            kept_names.append(name)
                    except (OSError, ValueError):
                        omitted += 1
                names[:] = kept_names
                for name in sorted(filenames, key=lambda value: value.lower()):
                    if len(candidates) >= self.max_files:
                        omitted += 1
                        names[:] = []
                        break
                    candidate = directory_path / name
                    try:
                        if is_ignored_context_path(self.guard, candidate):
                            continue
                        safe = self.guard.resolve(candidate, must_exist=True)
                        if safe.is_file() and safe == candidate.absolute():
                            candidates.append(safe)
                    except (OSError, ValueError) as exc:
                        errors.append(f"{name}: {type(exc).__name__}")
                if len(candidates) >= self.max_files:
                    break
        except OSError as exc:
            errors.append(f"scan: {type(exc).__name__}")
        candidates.sort(key=lambda item: item.as_posix().lower())
        return candidates, omitted, errors

    def _index_file(self, path: Path) -> IndexedFile | None:
        relative = self.guard.relative(path)
        sensitive = is_sensitive_context_path(relative)
        if sensitive:
            return None
        stat_before = path.stat()
        suffix = path.suffix.lower()
        language = _LANGUAGES.get(suffix)
        if stat_before.st_size > self.max_file_bytes or suffix in _BINARY_SUFFIXES:
            digest_builder = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_builder.update(chunk)
            stat_after = path.stat()
            if (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0)) != (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0)):
                raise OSError("file changed while it was read")
            return IndexedFile(relative, stat_before.st_size, stat_before.st_mtime_ns, digest_builder.hexdigest(), language, 0, readable=False, binary=True)
        raw = path.read_bytes()
        stat_after = path.stat()
        if (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0)) != (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0)):
            raise OSError("file changed while it was read")
        digest = hashlib.sha256(raw).hexdigest()
        if b"\x00" in raw:
            return IndexedFile(relative, len(raw), stat_before.st_mtime_ns, digest, language, 0, readable=False, binary=True)
        text = raw.decode("utf-8")
        symbols = tuple(_SYMBOL_PATTERNS.get(suffix, re.compile(r"$^" )).findall(text)[:100])
        return IndexedFile(relative, len(raw), stat_before.st_mtime_ns, digest, language, text.count("\n") + (1 if text else 0), symbols=symbols)

    def build(self, *, rebuild: bool = False) -> IndexReport:
        try:
            # Revalidate the cache path at the beginning and immediately
            # before replacement.  A runtime directory can be swapped for a
            # symlink/junction after construction; never let an index write
            # follow that alias outside the workspace.
            assert_no_path_alias(self.path)
            assert_no_path_alias(self.path.parent)
        except WorkspaceViolation as exc:
            raise ContextIndexError("context index path is a symlink or junction alias") from exc
        old: dict[str, IndexedFile] = {}
        old_fp: str | None = None
        corrupted = False
        if not rebuild:
            try:
                old, old_fp, _ = self._load()
            except ContextIndexError:
                corrupted = True
        candidates, omitted, errors = self._candidates()
        current: dict[str, IndexedFile] = {}
        added = updated = 0
        for path in candidates:
            relative = self.guard.relative(path)
            try:
                previous = old.get(relative)
                indexed = None
                if previous is not None:
                    stat = path.stat()
                    # mtime and size are the cheap invalidation fingerprint;
                    # files whose metadata is unchanged are reused without a
                    # second full read/hash.  Any metadata change falls back
                    # to a digest-checked read in `_index_file`.
                    if stat.st_size == previous.size and stat.st_mtime_ns == previous.mtime_ns:
                        indexed = previous
                if indexed is None:
                    indexed = self._index_file(path)
                if indexed is None:
                    continue
                current[relative] = indexed
                if previous is None:
                    added += 1
                elif previous != indexed:
                    updated += 1
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(f"{relative}: {type(exc).__name__}")
        removed = len(set(old) - set(current))
        fp = _fingerprint(current.values())
        payload = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "workspace": ".",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fingerprint": fp,
            "files": [asdict(item) for item in sorted(current.values(), key=lambda item: item.path)],
        }
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_INDEX_BYTES:
            raise ContextIndexError("context index exceeds the safety limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            assert_no_path_alias(self.path)
            assert_no_path_alias(self.path.parent)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".context-index.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
        except (OSError, WorkspaceViolation) as exc:
            raise ContextIndexError(f"cannot persist context index: {type(exc).__name__}") from exc
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
        return IndexReport(self.guard.relative(self.path), len(current), added, updated, removed, omitted, tuple(errors[:100]), corrupted, fp, payload["generated_at"])

    def ensure(self) -> IndexReport:
        """Build or incrementally refresh the index, recovering corruption."""
        return self.build()

    def entries(self) -> tuple[IndexedFile, ...]:
        try:
            entries, _, _ = self._load()
        except ContextIndexError:
            self.build(rebuild=True)
            entries, _, _ = self._load()
        return tuple(entries[path] for path in sorted(entries))

    def _read_current(self, item: IndexedFile) -> tuple[str, str] | None:
        try:
            path = self.guard.resolve(item.path, must_exist=True)
            if not path.is_file() or is_ignored_context_path(self.guard, path):
                return None
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item.digest:
                return None
            return raw.decode("utf-8"), digest
        except (OSError, UnicodeError, ValueError):
            return None

    def search(self, query: str = "", *, glob: str | None = None, regex: str | None = None, symbol: str | None = None, path: str | None = None, max_results: int = MAX_RESULTS, context_lines: int = 1) -> tuple[ContextSearchResult, ...]:
        self.last_search_issues = []
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            raise ValueError("query must be bounded text")
        if regex is not None and (not isinstance(regex, str) or len(regex) > MAX_QUERY_CHARS):
            raise ValueError("regex must be bounded text")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1 or max_results > MAX_RESULTS:
            raise ValueError("max_results must be between 1 and 200")
        if isinstance(context_lines, bool) or not isinstance(context_lines, int) or not 0 <= context_lines <= 5:
            raise ValueError("context_lines must be between 0 and 5")
        compiled = None
        if regex:
            try:
                compiled = re.compile(regex)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        query_lower = query.lower()
        results: list[ContextSearchResult] = []
        for item in self.entries():
            if item.binary or not item.readable or item.sensitive or item.ignored:
                continue
            if glob and not _safe_glob(item.path, glob):
                continue
            if path and path.replace("\\", "/").lower() not in item.path.lower():
                continue
            if symbol and symbol.lower() not in {value.lower() for value in item.symbols}:
                continue
            current = self._read_current(item)
            if current is None:
                try:
                    current_path = self.guard.resolve(item.path)
                    if not current_path.exists():
                        self.last_search_issues.append(f"{item.path}: stale (file missing or renamed)")
                    else:
                        self.last_search_issues.append(f"{item.path}: stale digest (file changed after indexing)")
                except (OSError, ValueError):
                    self.last_search_issues.append(f"{item.path}: stale (path is no longer safe)")
                continue
            text, digest = current
            lines = text.splitlines()
            for number, line in enumerate(lines, 1):
                matched = True
                reason_parts: list[str] = []
                if query:
                    matched = query_lower in line.lower()
                    if matched:
                        reason_parts.append(f"keyword:{query}")
                if compiled:
                    regex_match = compiled.search(line)
                    matched = bool(regex_match)
                    if regex_match:
                        reason_parts.append("regex")
                if symbol:
                    if symbol.lower() in {value.lower() for value in item.symbols}:
                        reason_parts.append(f"symbol:{symbol}")
                        matched = symbol.lower() in line.lower()
                    else:
                        matched = False
                if not (query or regex or symbol):
                    matched = True
                    reason_parts.append("path")
                if not matched:
                    continue
                start = max(0, number - 1 - context_lines)
                end = min(len(lines), number + context_lines)
                snippet = "\n".join(lines[start:end])[:4_000]
                results.append(ContextSearchResult(item.path, number, redact_text(snippet), digest, ",".join(reason_parts) or "match", item.language))
                if len(results) >= max_results:
                    return tuple(results)
        results.sort(key=lambda result: (result.path, result.line, result.snippet))
        return tuple(results[:max_results])

    def clear(self) -> bool:
        try:
            assert_no_path_alias(self.path)
            assert_no_path_alias(self.path.parent)
            if self.path.exists():
                self.path.unlink()
                return True
        except (OSError, WorkspaceViolation) as exc:
            raise ContextIndexError(f"cannot clear context index: {type(exc).__name__}") from exc
        return False

    def show(self) -> dict[str, Any]:
        entries = self.entries()
        return {
            "path": self.guard.relative(self.path),
            "schema_version": INDEX_SCHEMA_VERSION,
            "fingerprint": _fingerprint(entries),
            "files": [asdict(item) for item in entries],
            "counts": {"files": len(entries), "readable": sum(item.readable for item in entries), "binary": sum(item.binary for item in entries)},
        }


__all__ = ["ContextIndex", "ContextIndexError", "ContextSearchResult", "IndexedFile", "IndexReport", "DEFAULT_INDEX_PATH"]
