"""Persistent, incremental and explainable repository context index.

The index deliberately uses a small JSON document instead of a vector store or
downloaded model.  It is a cache, never an authority: every search result is
revalidated against the workspace before its bytes are returned.  The cache is
kept under ``.forgecode`` by default and can be deleted/rebuilt at any time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ast
import warnings
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable
import unicodedata

from ..context_policy import is_ignored_context_path, is_sensitive_context_path, may_contain_negated_context_path
from ..security.redaction import redact_text
from ..security.json import bounded_json_loads
from ..security.workspace import WorkspaceGuard, WorkspaceViolation, assert_no_path_alias


INDEX_SCHEMA_VERSION = 1
DEFAULT_INDEX_PATH = Path(".forgecode") / "context-index.json"
MAX_INDEX_BYTES = 32_000_000
MAX_INDEX_FILES = 20_000
MAX_FILE_BYTES = 2_000_000
MAX_QUERY_CHARS = 512
MAX_FILTER_CHARS = 512
MAX_RESULTS = 200
MAX_EXCLUSION_RECORDS = 1_000
MAX_REGEX_COMPLEXITY = 256

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


def _extract_symbols(text: str, suffix: str) -> tuple[str, ...]:
    """Extract bounded source symbols without executing project code.

    Python gets an AST pass so nested, async and decorated definitions are
    handled correctly.  Other languages use conservative line-oriented
    patterns; malformed Python falls back to the same bounded regex strategy.
    The result is ordered by source position and de-duplicated to make cache
    output deterministic.
    """
    if suffix == ".py":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
            found: list[tuple[int, str]] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((getattr(node, "lineno", 0), node.name))
            found.sort(key=lambda pair: (pair[0], pair[1]))
            result: list[str] = []
            for _, name in found:
                if name not in result:
                    result.append(name)
                if len(result) >= 100:
                    break
            return tuple(result)
        except (SyntaxError, ValueError, MemoryError):
            # A partial/invalid file still receives useful, non-executing
            # fallback symbols.  It is never imported or evaluated.
            pass
    pattern = _SYMBOL_PATTERNS.get(suffix)
    if pattern is None:
        return ()
    result: list[str] = []
    for name in pattern.findall(text):
        if name not in result:
            result.append(name)
        if len(result) >= 100:
            break
    return tuple(result)


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
    exclusion_reason: str | None = None

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
        reason = values.get("exclusion_reason")
        if reason is not None and (not isinstance(reason, str) or not reason or len(reason) > 128):
            raise ContextIndexError("index file exclusion_reason is invalid")
        return cls(
            path=values["path"], size=values["size"], mtime_ns=values["mtime_ns"], digest=values["digest"],
            language=values["language"], lines=values["lines"], readable=values["readable"],
            sensitive=values["sensitive"], ignored=values["ignored"], binary=values["binary"],
            symbols=tuple(values["symbols"][:100]),
            exclusion_reason=reason,
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
        self.last_search_diagnostics: list[dict[str, Any]] = []
        # Explainability is intentionally kept in memory.  Runtime cache
        # files contain only indexed entries and never raw ignored content.
        self.last_exclusions: list[dict[str, str]] = []
        self.last_build_errors: list[str] = []

    def _load(self) -> tuple[dict[str, IndexedFile], str | None, bool]:
        if not self.path.exists():
            self.last_exclusions = []
            return {}, None, False
        try:
            assert_no_path_alias(self.path)
            if not self.path.is_file() or self.path.stat().st_size > MAX_INDEX_BYTES:
                raise ContextIndexError("context index is not a regular file or exceeds the size limit")
            raw = bounded_json_loads(self.path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite_json)
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
            raw_exclusions = raw.get("exclusions", [])
            if raw_exclusions is None:
                raw_exclusions = []
            if not isinstance(raw_exclusions, list) or len(raw_exclusions) > MAX_EXCLUSION_RECORDS:
                raise ContextIndexError("context index exclusions are invalid")
            exclusions: list[dict[str, str]] = []
            for item in raw_exclusions:
                if not isinstance(item, dict) or set(item) - {"path", "reason"}:
                    raise ContextIndexError("context index exclusion entry is invalid")
                relative, reason = item.get("path"), item.get("reason")
                if not isinstance(relative, str) or not relative or len(relative) > 4_000 or not isinstance(reason, str) or not reason or len(reason) > 128:
                    raise ContextIndexError("context index exclusion entry is invalid")
                normalized = relative.replace("\\", "/")
                if Path(normalized).is_absolute() or any(part in {"", ".", ".."} for part in normalized.split("/")):
                    raise ContextIndexError("context index exclusion path is unsafe")
                exclusions.append({"path": normalized, "reason": reason})
            self.last_exclusions = self._dedupe_exclusions(exclusions)
            return by_path, stored_fp, False
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, ContextIndexError) as exc:
            raise ContextIndexError(f"cannot read context index: {type(exc).__name__}: {exc}") from exc

    def _candidates(self) -> tuple[list[Path], int, list[str]]:
        candidates: list[Path] = []
        errors: list[str] = []
        omitted = 0
        exclusions: list[dict[str, str]] = []
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
                        elif ignored:
                            try:
                                exclusions.append({"path": self.guard.relative(candidate), "reason": "ignored directory"})
                            except (OSError, ValueError):
                                pass
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
                            try:
                                relative = self.guard.relative(candidate)
                                reason = "sensitive" if is_sensitive_context_path(relative) else "ignored by policy"
                                exclusions.append({"path": relative, "reason": reason})
                            except (OSError, ValueError):
                                pass
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
        self.last_exclusions = self._dedupe_exclusions(exclusions)
        return candidates, omitted, errors

    @staticmethod
    def _dedupe_exclusions(items: Iterable[dict[str, str]]) -> list[dict[str, str]]:
        """Normalize and deterministically bound exclusion explanations."""
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            path, reason = item.get("path"), item.get("reason")
            if not isinstance(path, str) or not isinstance(reason, str) or not path or not reason:
                continue
            key = (path.replace("\\", "/"), reason[:128])
            if key in seen:
                continue
            seen.add(key)
            result.append({"path": key[0], "reason": key[1]})
            if len(result) >= MAX_EXCLUSION_RECORDS:
                break
        result.sort(key=lambda item: (item["path"].lower(), item["reason"]))
        return result

    def _index_file(self, path: Path) -> IndexedFile | None:
        assert_no_path_alias(path)
        relative = self.guard.relative(path)
        sensitive = is_sensitive_context_path(relative)
        if sensitive:
            # Sensitive files normally never reach this method (the candidate
            # scanner filters them), but retain a defensive explanation if a
            # caller invokes the helper directly.
            self.last_exclusions.append({"path": relative, "reason": "sensitive"})
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
            assert_no_path_alias(path)
            if (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0)) != (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0)):
                raise OSError("file changed while it was read")
            reason = "oversized" if stat_before.st_size > self.max_file_bytes else "binary"
            return IndexedFile(
                relative,
                stat_before.st_size,
                stat_before.st_mtime_ns,
                digest_builder.hexdigest(),
                language,
                0,
                readable=False,
                binary=reason == "binary",
                exclusion_reason=reason,
            )
        raw = path.read_bytes()
        stat_after = path.stat()
        assert_no_path_alias(path)
        if (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0)) != (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0)):
            raise OSError("file changed while it was read")
        digest = hashlib.sha256(raw).hexdigest()
        if b"\x00" in raw:
            return IndexedFile(relative, len(raw), stat_before.st_mtime_ns, digest, language, 0, readable=False, binary=True, exclusion_reason="binary")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return IndexedFile(relative, len(raw), stat_before.st_mtime_ns, digest, language, 0, readable=False, binary=False, exclusion_reason="non_utf8")
        symbols = _extract_symbols(text, suffix)
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
        self.last_build_errors = list(errors[:100])
        removed = len(set(old) - set(current))
        fp = _fingerprint(current.values())
        exclusion_records = list(self.last_exclusions)
        exclusion_records.extend(
            {"path": item.path, "reason": item.exclusion_reason}
            for item in current.values()
            if item.exclusion_reason
        )
        self.last_exclusions = self._dedupe_exclusions(exclusion_records)
        payload = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "workspace": ".",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fingerprint": fp,
            "exclusions": self.last_exclusions[:MAX_EXCLUSION_RECORDS],
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
        current, _ = self._read_current_checked(item)
        return current

    def _read_current_checked(self, item: IndexedFile) -> tuple[tuple[str, str] | None, dict[str, Any] | None]:
        """Read one indexed file and return a structured stale diagnostic.

        The old ``_read_current`` API intentionally remains a compact wrapper
        for callers that only need a nullable result.  Search and diagnostics
        use this richer form so an operator can distinguish a missing file,
        an in-flight replacement and a digest mismatch without exposing file
        contents.
        """
        try:
            path = self.guard.resolve(item.path, must_exist=True)
            if not path.is_file() or is_ignored_context_path(self.guard, path):
                return None, {"code": "stale_missing", "path": item.path, "message": "file is missing or excluded", "expected_digest": item.digest}
            assert_no_path_alias(path)
            stat_before = path.stat()
            raw = path.read_bytes()
            stat_after = path.stat()
            assert_no_path_alias(path)
            identity_before = (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0))
            identity_after = (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0))
            if identity_before != identity_after:
                return None, {"code": "stale_changed_during_read", "path": item.path, "message": "file changed while it was read", "expected_digest": item.digest}
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item.digest:
                return None, {"code": "stale_digest", "path": item.path, "message": "file digest changed after indexing", "expected_digest": item.digest, "observed_digest": digest}
            try:
                return (raw.decode("utf-8"), digest), None
            except UnicodeDecodeError:
                return None, {"code": "stale_non_utf8", "path": item.path, "message": "file is no longer valid UTF-8", "expected_digest": item.digest, "observed_digest": digest}
        except FileNotFoundError:
            return None, {"code": "stale_missing", "path": item.path, "message": "file is missing or renamed", "expected_digest": item.digest}
        except (OSError, UnicodeError, ValueError, WorkspaceViolation):
            return None, {"code": "stale_unsafe", "path": item.path, "message": "path is no longer safe or readable", "expected_digest": item.digest}

    def search(
        self,
        query: str = "",
        *,
        glob: str | None = None,
        regex: str | None = None,
        symbol: str | None = None,
        path: str | None = None,
        language: str | None = None,
        line_range: tuple[int, int] | list[int] | str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        max_results: int = MAX_RESULTS,
        context_lines: int = 1,
    ) -> tuple[ContextSearchResult, ...]:
        self.last_search_issues = []
        self.last_search_diagnostics = []
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            raise ValueError("query must be bounded text")
        # Filters are fed into fnmatch/string operations for every indexed
        # entry.  Validate their type, size and NUL content up front so a
        # malformed integration call cannot raise an unhandled TypeError (or
        # spend unbounded time processing an attacker-sized pattern).
        for field_name, value in (("glob", glob), ("symbol", symbol), ("path", path)):
            if value is not None and (not isinstance(value, str) or len(value) > MAX_FILTER_CHARS or "\x00" in value):
                raise ValueError(f"{field_name} must be bounded text")
        if regex is not None and (not isinstance(regex, str) or len(regex) > MAX_QUERY_CHARS):
            raise ValueError("regex must be bounded text")
        if language is not None and (not isinstance(language, str) or not language.strip() or len(language) > 64):
            raise ValueError("language must be bounded text")
        if line_range is not None and (line_start is not None or line_end is not None):
            raise ValueError("line_range cannot be combined with line_start/line_end")
        if isinstance(line_range, str):
            match = re.fullmatch(r"\s*(\d+)\s*(?::|-|\.\.)\s*(\d+)\s*", line_range)
            if not match:
                raise ValueError("line_range must use START:END")
            line_range = (int(match.group(1)), int(match.group(2)))
        if line_range is None and (line_start is not None or line_end is not None):
            line_range = (1 if line_start is None else line_start, 10_000_000 if line_end is None else line_end)
        if line_range is not None:
            if (not isinstance(line_range, (tuple, list)) or len(line_range) != 2 or
                    any(isinstance(value, bool) or not isinstance(value, int) for value in line_range)):
                raise ValueError("line_range must be a pair of positive integers")
            line_start, line_end = int(line_range[0]), int(line_range[1])
            if line_start < 1 or line_end < line_start or line_end > 10_000_000:
                raise ValueError("line_range must be an ordered positive range")
        else:
            line_start, line_end = 1, 10_000_000
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1 or max_results > MAX_RESULTS:
            raise ValueError("max_results must be between 1 and 200")
        if isinstance(context_lines, bool) or not isinstance(context_lines, int) or not 0 <= context_lines <= 5:
            raise ValueError("context_lines must be between 0 and 5")
        compiled = None
        if regex:
            complexity = sum(1 for char in regex if char in "*+?{|()[]\\")
            # ``re`` has no portable execution deadline.  Reject the common
            # nested-quantifier/back-reference forms that can trigger
            # catastrophic backtracking, plus unusually operator-dense input.
            if complexity > MAX_REGEX_COMPLEXITY or re.search(r"(?:\([^()]{0,256}[+*][^()]{{0,256}}\))[+*]", regex) or re.search(r"\\[1-9]", regex):
                raise ValueError("regex is too complex for bounded search")
            try:
                compiled = re.compile(regex)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        query_lower = unicodedata.normalize("NFC", query).casefold()
        requested_language = None
        if language is not None:
            requested_language = language.strip().lower()
            if requested_language.startswith("."):
                requested_language = requested_language[1:]
            aliases = {
                "py": "python", "python3": "python", "js": "javascript", "ts": "typescript",
                "md": "markdown", "yml": "yaml", "c++": "c++",
            }
            requested_language = aliases.get(requested_language, requested_language)
        results: list[ContextSearchResult] = []
        for item in self.entries():
            if item.binary or not item.readable or item.sensitive or item.ignored:
                continue
            if requested_language is not None:
                actual_language = (item.language or "").lower()
                if requested_language != actual_language and not actual_language.startswith(requested_language + "/"):
                    continue
            if glob and not _safe_glob(item.path, glob):
                continue
            if path and path.replace("\\", "/").lower() not in item.path.lower():
                continue
            if symbol and symbol.lower() not in {value.lower() for value in item.symbols}:
                continue
            current, issue = self._read_current_checked(item)
            if current is None:
                if issue is None:
                    issue = {"code": "stale", "path": item.path, "message": "file could not be revalidated"}
                self.last_search_diagnostics.append(issue)
                message = str(issue.get("message", issue.get("code", "stale")))
                # Retain the historical phrase used by integrations while
                # exposing the richer machine diagnostic alongside it.
                if issue.get("code") == "stale_digest" and "stale digest" not in message.lower():
                    message = f"stale digest: {message}"
                self.last_search_issues.append(f"{item.path}: {message}")
                continue
            text, digest = current
            lines = text.splitlines()
            for number, line in enumerate(lines, 1):
                if number < line_start or number > line_end:
                    continue
                matched = True
                reason_parts: list[str] = []
                if query:
                    matched = query_lower in unicodedata.normalize("NFC", line).casefold()
                    if matched:
                        reason_parts.append(f"keyword:{query}")
                if compiled:
                    regex_match = compiled.search(line)
                    matched = matched and bool(regex_match)
                    if regex_match:
                        reason_parts.append("regex")
                if symbol:
                    if symbol.lower() in {value.lower() for value in item.symbols}:
                        reason_parts.append(f"symbol:{symbol}")
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

    def complete(self, prefix: str = "", *, max_results: int = 50) -> tuple[dict[str, Any], ...]:
        """Return deterministic, read-only path suggestions.

        Suggestions are advisory only: callers must still pass the selected
        path through ``WorkspaceGuard`` and the relevant tool policy.
        Sensitive, binary and ignored entries are represented with an
        exclusion reason rather than being silently treated as authorized.
        """
        if not isinstance(prefix, str) or len(prefix) > MAX_FILTER_CHARS or "\x00" in prefix:
            raise ValueError("prefix must be bounded text")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= MAX_RESULTS:
            raise ValueError("max_results must be between 1 and 200")
        # Normalize only an explicit leading ``./``.  Do not use ``lstrip``
        # here: it would turn ``../secret`` and absolute paths into broad,
        # seemingly safe prefixes and could also hide dot-files.
        normalized = prefix.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
            raise ValueError("prefix must be workspace-relative")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if any(part == ".." for part in normalized.split("/")):
            raise ValueError("prefix must not contain parent traversal")
        suggestions: list[dict[str, Any]] = []
        try:
            entries = self.entries()
        except (ContextIndexError, OSError, ValueError):
            entries = ()
        for item in entries:
            if normalized and not item.path.lower().startswith(normalized.lower()):
                continue
            reason = item.exclusion_reason
            if not reason:
                if item.sensitive:
                    reason = "sensitive"
                elif item.binary:
                    reason = "binary"
                elif item.ignored:
                    reason = "ignored"
            suggestions.append({"path": item.path, "type": "file", "excluded": bool(reason), "exclusion_reason": reason, "truncated": False})
        for item in self.last_exclusions:
            path = str(item.get("path", ""))
            if path and (not normalized or path.lower().startswith(normalized.lower())) and not any(entry["path"] == path for entry in suggestions):
                suggestions.append({"path": path, "type": "file", "excluded": True, "exclusion_reason": str(item.get("reason", "excluded"))[:64], "truncated": False})
        suggestions.sort(key=lambda entry: (entry["path"], entry["excluded"], entry.get("exclusion_reason") or ""))
        truncated = len(suggestions) > max_results
        values = suggestions[:max_results]
        if values and truncated:
            values[-1] = {**values[-1], "truncated": True}
        return tuple(values)

    def diagnostics(self) -> dict[str, Any]:
        """Return bounded, digest-aware diagnostics for the current index.

        Unlike ``search``, this method does not return source text.  It
        revalidates every indexed file and reports missing, changed or unsafe
        entries so callers can distinguish a stale cache from a true no-match.
        """
        stale: list[dict[str, str]] = []
        try:
            # This command is observational: unlike ``ensure``/``entries`` it
            # must not silently rebuild a corrupt cache while claiming to
            # diagnose it.
            entries, _, _ = self._load()
        except (ContextIndexError, OSError, ValueError) as exc:
            return {
                "schema_version": INDEX_SCHEMA_VERSION,
                "stale": [],
                "errors": [f"index: {type(exc).__name__}"],
                "exclusions": list(self.last_exclusions[:100]),
            }
        for item in entries.values():
            if item.binary or not item.readable or item.sensitive or item.ignored:
                continue
            try:
                current = self.guard.resolve(item.path, must_exist=True)
                if not current.is_file():
                    stale.append({"path": item.path, "reason": "missing"})
                    continue
                assert_no_path_alias(current)
                stat_before = current.stat()
                raw = current.read_bytes()
                stat_after = current.stat()
                assert_no_path_alias(current)
                if (stat_before.st_size, stat_before.st_mtime_ns, getattr(stat_before, "st_ino", 0)) != (stat_after.st_size, stat_after.st_mtime_ns, getattr(stat_after, "st_ino", 0)):
                    stale.append({"path": item.path, "reason": "changed_during_read", "expected_digest": item.digest})
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if digest != item.digest:
                    stale.append({"path": item.path, "reason": "digest_changed", "expected_digest": item.digest, "observed_digest": digest})
            except FileNotFoundError:
                stale.append({"path": item.path, "reason": "missing"})
            except (OSError, UnicodeError, ValueError):
                stale.append({"path": item.path, "reason": "unsafe_or_unreadable"})
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "stale": stale[:100],
            "errors": list(self.last_build_errors[:100]),
            "exclusions": list(self.last_exclusions[:100]),
        }

    def explain(self) -> dict[str, Any]:
        """Explain indexed and omitted files without exposing file content."""
        # A newly-created process may load an existing cache without having
        # performed the scan that generated its exclusion diagnostics.  Run a
        # bounded read-only candidate pass so ``explain`` remains useful and
        # deterministic across process boundaries.
        try:
            self._candidates()
        except (OSError, ValueError):
            pass
        try:
            entries = self.entries()
        except (ContextIndexError, OSError, ValueError) as exc:
            return {
                "schema_version": INDEX_SCHEMA_VERSION,
                "included": 0,
                "files": [],
                "excluded": list(self.last_exclusions[:100]),
                "errors": [f"index: {type(exc).__name__}"],
            }
        files = [
            {"path": item.path, "language": item.language, "symbols": list(item.symbols), "readable": item.readable, "binary": item.binary}
            for item in entries
        ]
        excluded = list(self.last_exclusions[:100])
        known_excluded = {item.get("path") for item in excluded}
        for item in entries:
            if item.path in known_excluded:
                continue
            if item.exclusion_reason:
                excluded.append({"path": item.path, "reason": item.exclusion_reason})
            elif item.binary:
                excluded.append({"path": item.path, "reason": "binary"})
            elif not item.readable:
                excluded.append({"path": item.path, "reason": "unreadable"})
            elif item.size > self.max_file_bytes:
                excluded.append({"path": item.path, "reason": "oversized"})
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "included": len(files),
            "files": files,
            "excluded": excluded[:100],
            "errors": list(self.last_build_errors[:100]),
        }

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
