"""Bounded, scoped project-rule discovery.

Rules are context only.  They never alter the execution policy and are
recorded by digest rather than copying private prose into audit events.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable

from .security.workspace import WorkspaceGuard, WorkspaceViolation
from .security.redaction import redact_text
from .context_policy import is_ignored_context_path, is_sensitive_context_path

MAX_RULE_FILES = 64
MAX_RULE_FILE_CHARS = 40_000
MAX_RULE_TOTAL_CHARS = 120_000
MAX_RULE_DEPTH = 32


@dataclass(frozen=True)
class RuleSource:
    path: str
    scope: str
    priority: int
    digest: str
    chars: int
    truncated: bool = False
    omitted_reason: str | None = None
    kind: str = "AGENTS.md"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RuleDiagnostic:
    code: str
    path: str | None
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RuleSet:
    sources: tuple[RuleSource, ...] = ()
    diagnostics: tuple[RuleDiagnostic, ...] = ()
    text: str = ""
    fingerprint: str = ""

    @property
    def conflicts(self) -> tuple[RuleDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.code == "conflict")

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)

    def to_dict(self, *, include_text: bool = False) -> dict:
        payload = {
            "sources": [source.to_dict() for source in self.sources],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "fingerprint": self.fingerprint,
            "chars": len(self.text),
        }
        if include_text:
            payload["text"] = self.text
        return payload

    def render(self, max_chars: int = MAX_RULE_TOTAL_CHARS) -> str:
        if max_chars < 1:
            return ""
        return self.text[:max_chars] + ("\n[rules truncated]" if len(self.text) > max_chars else "")


def _safe_rule_name(path: Path) -> bool:
    name = path.name.lower()
    return name in {"agents.md", "claude.md"} or (".cursor" in path.parts and path.suffix.lower() == ".md") or (".continue" in path.parts and path.suffix.lower() == ".md")


def _sensitive(path: Path, guard: WorkspaceGuard) -> bool:
    return is_sensitive_context_path(guard.relative(path))


class RuleEngine:
    """Discover root and target-scoped rules with deterministic precedence."""

    def __init__(self, guard: WorkspaceGuard, *, max_files: int = MAX_RULE_FILES, max_file_chars: int = MAX_RULE_FILE_CHARS, max_total_chars: int = MAX_RULE_TOTAL_CHARS, max_depth: int = MAX_RULE_DEPTH, compatible: bool = False):
        for value, low in ((max_files, 1), (max_file_chars, 128), (max_total_chars, 256), (max_depth, 1)):
            if isinstance(value, bool) or not isinstance(value, int) or value < low:
                raise ValueError("rule limits must be positive integers")
        self.guard = guard
        self.max_files = max_files
        self.max_file_chars = max_file_chars
        self.max_total_chars = max_total_chars
        self.max_depth = max_depth
        self.compatible = compatible

    def discover(self, targets: Iterable[str | Path] = ()) -> RuleSet:
        diagnostics: list[RuleDiagnostic] = []
        candidates: list[tuple[Path, str, int, str]] = []
        seen: set[str] = set()

        def add(path: Path, scope: Path, priority: int, kind: str) -> None:
            key = path.as_posix().lower()
            if key in seen:
                return
            seen.add(key)
            try:
                resolved = self.guard.resolve(path)
                if resolved != path.absolute() or not resolved.is_relative_to(self.guard.root):
                    diagnostics.append(RuleDiagnostic("unsafe_path", None, "rule path is outside workspace or symlink escaped"))
                    return
            except (OSError, ValueError, WorkspaceViolation) as exc:
                diagnostics.append(RuleDiagnostic("unsafe_path", None, f"rule path rejected: {type(exc).__name__}"))
                return
            if _sensitive(resolved, self.guard):
                diagnostics.append(RuleDiagnostic("sensitive_omitted", self._relative_safe(resolved), "sensitive rule path omitted"))
                return
            if is_ignored_context_path(self.guard, resolved):
                diagnostics.append(RuleDiagnostic("ignored_omitted", self._relative_safe(resolved), "ignored rule path omitted"))
                return
            if not resolved.is_file():
                return
            candidates.append((resolved, self._relative_safe(scope), priority, kind))

        root = self.guard.root
        add(root / "AGENTS.md", root, 0, "AGENTS.md")
        if self.compatible:
            add(root / "CLAUDE.md", root, 0, "CLAUDE.md")
            for directory_name in (".cursor/rules", ".continue/rules"):
                directory = root / directory_name
                if directory.is_dir():
                    try:
                        for child in sorted(directory.glob("*.md"), key=lambda p: p.as_posix().lower()):
                            add(child, root, 0, child.name)
                    except OSError:
                        diagnostics.append(RuleDiagnostic("scan_error", directory_name, "compatible rule directory could not be scanned"))

        target_paths = list(targets)
        if not target_paths:
            target_paths = [root]
        for target in target_paths:
            try:
                resolved_target = self.guard.resolve(target)
                lexical_target = target if isinstance(target, Path) else Path(target)
                if not lexical_target.is_absolute():
                    lexical_target = root / lexical_target
                if resolved_target != lexical_target.absolute():
                    diagnostics.append(RuleDiagnostic("target_rejected", str(target), "target is a symlink or junction alias"))
                    continue
            except (OSError, ValueError, WorkspaceViolation):
                diagnostics.append(RuleDiagnostic("target_rejected", str(target), "target is outside workspace"))
                continue
            if resolved_target.is_file():
                resolved_target = resolved_target.parent
            try:
                relative_parts = resolved_target.relative_to(root).parts
            except ValueError:
                continue
            if len(relative_parts) > self.max_depth:
                diagnostics.append(RuleDiagnostic("depth_limit", self._relative_safe(resolved_target), "nested rule search exceeded depth limit"))
                continue
            ancestors = [root]
            current = root
            for part in relative_parts:
                current = current / part
                ancestors.append(current)
            for depth, directory in enumerate(ancestors):
                add(directory / "AGENTS.md", directory, depth, "AGENTS.md")

        # Deep scopes have higher priority.  Stable path ordering resolves no
        # conflict silently: a diagnostic is emitted when prose contains
        # contradictory operational directives.
        candidates.sort(key=lambda item: (item[2], item[0].as_posix().lower()))
        sources: list[RuleSource] = []
        chunks: list[str] = []
        total = 0
        for index, (path, scope, priority, kind) in enumerate(candidates):
            if index >= self.max_files:
                diagnostics.append(RuleDiagnostic("file_limit", None, f"omitted rules after {self.max_files} files"))
                break
            relative = self._relative_safe(path)
            try:
                before_stat = path.stat()
                before_resolved = self.guard.resolve(path)
                if before_resolved != path or not path.is_file():
                    diagnostics.append(RuleDiagnostic("unsafe_path", relative, "rule source changed into an unsafe path"))
                    continue
                raw = path.read_bytes()
                after_stat = path.stat()
                after_resolved = self.guard.resolve(path)
                if after_resolved != path or not path.is_file():
                    diagnostics.append(RuleDiagnostic("unsafe_path", relative, "rule source changed while it was read"))
                    continue
                if (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0)) != (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0)):
                    diagnostics.append(RuleDiagnostic("read_changed", relative, "rule source changed while it was read"))
                    continue
                if b"\x00" in raw:
                    diagnostics.append(RuleDiagnostic("binary_omitted", relative, "binary rule source omitted"))
                    continue
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                diagnostics.append(RuleDiagnostic("encoding_error", relative, "rule source is not UTF-8"))
                continue
            except OSError as exc:
                diagnostics.append(RuleDiagnostic("read_error", relative, f"could not read rule source: {type(exc).__name__}"))
                continue
            digest = hashlib.sha256(raw).hexdigest()
            original_chars = len(decoded)
            available = max(0, min(self.max_file_chars, self.max_total_chars - total))
            truncated = original_chars > available
            content = decoded[:available]
            if truncated:
                diagnostics.append(RuleDiagnostic("truncated", relative, "rule source exceeded configured character limit"))
            if not content and original_chars:
                diagnostics.append(RuleDiagnostic("total_limit", relative, "rule source omitted after total limit"))
                continue
            source = RuleSource(relative, scope, priority, digest, original_chars, truncated, "character_limit" if truncated else None, kind)
            sources.append(source)
            chunks.append(f"[Rule source: {relative}; scope={scope}; priority={priority}; digest={digest[:16]}]\n{redact_text(content)}")
            total += len(content)
            if total >= self.max_total_chars:
                break

        texts = ["\n".join(chunk.splitlines()) for chunk in chunks]
        # Detect conflicting imperative markers without interpreting rules as
        # executable policy.
        combined = "\n\n".join(texts)
        lowered = combined.lower()
        for left, right, label in (("always run tests", "never run tests", "test execution"), ("auto approve", "require approval", "approval"), ("do not modify", "must modify", "modification")):
            if left in lowered and right in lowered:
                diagnostics.append(RuleDiagnostic("conflict", None, f"conflicting rule directives detected for {label}"))
        fatal_codes = {"unsafe_path", "target_rejected", "encoding_error", "read_error", "read_changed", "scan_error"}
        diagnostics = [RuleDiagnostic(item.code, item.path, item.message, "error" if item.code in fatal_codes else item.severity) for item in diagnostics]
        fingerprint = hashlib.sha256("|".join(f"{source.path}:{source.scope}:{source.digest}" for source in sources).encode("utf-8")).hexdigest()
        return RuleSet(tuple(sources), tuple(diagnostics), combined, fingerprint)

    def _relative_safe(self, path: Path) -> str:
        try:
            return self.guard.relative(path)
        except (OSError, ValueError):
            return "."

    def check(self, targets: Iterable[str | Path] = ()) -> RuleSet:
        return self.discover(targets)


def discover_rules(guard: WorkspaceGuard, targets: Iterable[str | Path] = (), **kwargs: object) -> RuleSet:
    return RuleEngine(guard, **kwargs).discover(targets)


__all__ = ["RuleDiagnostic", "RuleEngine", "RuleSet", "RuleSource", "discover_rules"]
