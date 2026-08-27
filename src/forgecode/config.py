"""Typed, redacted configuration for ForgeCode.

The original :class:`Settings` API is intentionally kept compatible.  The
new loader is deliberately small and only reads TOML from the ignored
``.forgecode/config.toml`` file; configuration is data, never executable
Python.  Secrets are represented by an environment variable *name* and are
never loaded into the serialised effective configuration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import os
from pathlib import Path
import re
import stat
import tomllib
from typing import Any, Mapping
from urllib.parse import urlsplit

from .security.workspace import WorkspaceViolation, assert_no_path_alias


class ConfigError(ValueError):
    """A configuration file or override is invalid or unsafe."""


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str | None = None
    api_key_env: str = "FORGECODE_API_KEY"
    streaming: str = "auto"

    def validate(self) -> None:
        _bounded_text(self.name, "profile.name", 64)
        _bounded_text(self.provider, "profile.provider", 64)
        if self.provider not in {"openai-compatible"}:
            raise ConfigError("profile.provider is unsupported")
        _validate_base_url(self.base_url, "profile.base_url")
        if self.model is not None:
            _bounded_text(self.model, "profile.model", 256)
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", self.api_key_env):
            raise ConfigError("profile.api_key_env must be an environment variable name")
        if self.streaming not in {"auto", "on", "off", "required"}:
            raise ConfigError("profile.streaming must be auto, on, off, or required")


@dataclass(frozen=True)
class ToolPolicy:
    """A narrowing policy; it can never grant tools absent from the registry."""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def validate(self) -> None:
        if not isinstance(self.allow, tuple) or not isinstance(self.deny, tuple):
            raise ConfigError("tool_policy allow and deny must be arrays")
        for name in (*self.allow, *self.deny):
            _bounded_text(name, "tool_policy.name", 80)
        if set(self.allow) & set(self.deny):
            raise ConfigError("tool_policy allow and deny overlap")

    def permits(self, name: str, *, available: set[str] | None = None) -> bool:
        if name in self.deny:
            return False
        if self.allow and name not in self.allow:
            return False
        return available is None or name in available


@dataclass(frozen=True)
class EffectiveConfig:
    workspace: Path
    profile: str = "default"
    provider: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str | None = None
    api_key_env: str = "FORGECODE_API_KEY"
    default_mode: str = "act"
    approval: str = "interactive"
    streaming: str = "auto"
    max_steps: int = 12
    max_tool_calls: int = 512
    context_budget_chars: int = 60_000
    compact_threshold_chars: int = 48_000
    provider_timeout_seconds: float = 90.0
    run_timeout_seconds: float = 600.0
    verification_command: str | None = None
    repair_attempts: int = 2
    session_max_chars: int = 100_000
    transaction_max_bytes: int = 50_000_000
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    sources: tuple[str, ...] = ()

    def validate(self) -> None:
        if not isinstance(self.workspace, Path) or not self.workspace.is_dir():
            raise ConfigError("workspace must be an existing directory")
        _bounded_text(self.profile, "profile", 64)
        _bounded_text(self.provider, "provider", 64)
        if self.provider not in {"openai-compatible"}:
            raise ConfigError("provider is unsupported")
        _validate_base_url(self.base_url, "base_url")
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", self.api_key_env):
            raise ConfigError("api_key_env must be an environment variable name")
        if self.default_mode not in {"plan", "act"}:
            raise ConfigError("default_mode must be plan or act")
        if self.approval not in {"interactive", "auto", "deny"}:
            raise ConfigError("approval must be interactive, auto, or deny")
        if self.streaming not in {"auto", "on", "off", "required"}:
            raise ConfigError("streaming must be auto, on, off, or required")
        for name, value, lower, upper in (
            ("max_steps", self.max_steps, 1, 10_000),
            ("max_tool_calls", self.max_tool_calls, 1, 100_000),
            ("context_budget_chars", self.context_budget_chars, 256, 10_000_000),
            ("compact_threshold_chars", self.compact_threshold_chars, 256, 10_000_000),
            ("repair_attempts", self.repair_attempts, 0, 100),
            ("session_max_chars", self.session_max_chars, 128, 10_000_000),
            ("transaction_max_bytes", self.transaction_max_bytes, 1_024, 1_000_000_000),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ConfigError(f"{name} must be an integer between {lower} and {upper}")
        for name, value in (("provider_timeout_seconds", self.provider_timeout_seconds), ("run_timeout_seconds", self.run_timeout_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.1 <= value <= 86_400:
                raise ConfigError(f"{name} must be a finite number between 0.1 and 86400")
        if self.model is not None:
            _bounded_text(self.model, "model", 256)
        if self.verification_command is not None:
            _bounded_text(self.verification_command, "verification_command", 4_000)
        self.tool_policy.validate()
        known_tools = {"list_files", "read_file", "search", "write_file", "apply_patch", "workspace_summary", "repository_map", "run_command"}
        unknown_tools = (set(self.tool_policy.allow) | set(self.tool_policy.deny)) - known_tools
        if unknown_tools:
            raise ConfigError("tool_policy contains unknown tools: " + ", ".join(sorted(unknown_tools)))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["workspace"] = "."
        result["tool_policy"] = {"allow": list(self.tool_policy.allow), "deny": list(self.tool_policy.deny)}
        result["api_key"] = "<environment-only>" if self.api_key_env else None
        return result


@dataclass(frozen=True)
class Settings:
    workspace: Path
    model: str | None = None
    api_key_env: str = "FORGECODE_API_KEY"
    base_url: str = "https://api.openai.com/v1"
    profile: str = "default"
    effective: EffectiveConfig | None = None

    @classmethod
    def from_environment(cls, workspace: Path) -> "Settings":
        config = ConfigLoader(workspace).load()
        return cls(workspace=workspace, model=config.model, api_key_env=config.api_key_env, base_url=config.base_url, profile=config.profile, effective=config)


def _bounded_text(value: Any, field_name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ConfigError(f"{field_name} must be a non-empty string of at most {limit} characters")
    return value


def _validate_base_url(value: str, field_name: str) -> str:
    """Validate an endpoint without permitting credential-bearing URLs."""
    _bounded_text(value, field_name, 512)
    if any(character.isspace() for character in value):
        raise ConfigError(f"{field_name} must not contain whitespace")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{field_name} must be a valid http(s) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or hostname is None:
        raise ConfigError(f"{field_name} must be an http(s) URL")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ConfigError(f"{field_name} must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{field_name} must not contain a URL query or fragment")
    return value


def _env_overlay() -> dict[str, Any]:
    values: dict[str, Any] = {}
    mapping = {
        "FORGECODE_MODEL": "model", "FORGECODE_BASE_URL": "base_url", "FORGECODE_PROVIDER": "provider",
        "FORGECODE_MODE": "default_mode", "FORGECODE_STREAMING": "streaming", "FORGECODE_APPROVAL": "approval",
        "FORGECODE_VERIFICATION": "verification_command", "FORGECODE_PROFILE": "profile",
    }
    for env_name, key in mapping.items():
        if os.getenv(env_name):
            values[key] = os.environ[env_name]
    numeric = (
        ("FORGECODE_MAX_STEPS", "max_steps", int),
        ("FORGECODE_MAX_TOOL_CALLS", "max_tool_calls", int),
        ("FORGECODE_CONTEXT_BUDGET", "context_budget_chars", int),
        ("FORGECODE_COMPACT_THRESHOLD", "compact_threshold_chars", int),
        ("FORGECODE_PROVIDER_TIMEOUT", "provider_timeout_seconds", float),
        ("FORGECODE_RUN_TIMEOUT", "run_timeout_seconds", float),
        ("FORGECODE_REPAIR_ATTEMPTS", "repair_attempts", int),
        ("FORGECODE_SESSION_MAX_CHARS", "session_max_chars", int),
        ("FORGECODE_TRANSACTION_MAX_BYTES", "transaction_max_bytes", int),
    )
    for env_name, key, converter in numeric:
        raw = os.getenv(env_name)
        if raw:
            try:
                values[key] = converter(raw)
            except ValueError as exc:
                raise ConfigError(f"{env_name} has an invalid numeric value") from exc
    return values


class ConfigLoader:
    """Load effective config with CLI > TOML > environment > defaults."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.path = self.workspace / ".forgecode" / "config.toml"

    def _read_file(self) -> dict[str, Any]:
        # ``Path.exists`` follows links and returns False for a broken link.
        # Inspect the directory entry first so a symlink can never silently
        # become an empty/default configuration.
        try:
            exists = os.path.lexists(self.path)
        except OSError as exc:
            raise ConfigError("config.toml path could not be validated") from exc
        if not exists:
            return {}
        try:
            assert_no_path_alias(self.path, message="config.toml must be a regular workspace-local file, not a symlink or junction")
        except WorkspaceViolation as exc:
            raise ConfigError(str(exc)) from exc
        try:
            path_stat = self.path.lstat()
        except OSError as exc:
            raise ConfigError("config.toml path could not be validated") from exc
        if stat.S_ISLNK(path_stat.st_mode):
            raise ConfigError("config.toml must be a regular workspace-local file, not a symlink")
        if not stat.S_ISREG(path_stat.st_mode):
            raise ConfigError(".forgecode/config.toml is not a file")
        try:
            resolved = self.path.resolve(strict=True)
            if not resolved.is_relative_to(self.workspace) or resolved != self.path.absolute():
                raise ConfigError("config.toml must be a regular workspace-local file, not a symlink")
        except OSError as exc:
            raise ConfigError("config.toml path could not be validated") from exc
        try:
            before_stat = self.path.stat()
            with self.path.open("rb") as stream:
                data = tomllib.load(stream)
            after_stat = self.path.stat()
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"invalid config.toml: {type(exc).__name__}") from exc
        before_identity = (before_stat.st_size, before_stat.st_mtime_ns, getattr(before_stat, "st_ino", 0))
        after_identity = (after_stat.st_size, after_stat.st_mtime_ns, getattr(after_stat, "st_ino", 0))
        if before_identity != after_identity:
            raise ConfigError("config.toml changed while it was read")
        if not isinstance(data, dict):
            raise ConfigError("config root must be a table")
        return data

    def load(self, overrides: Mapping[str, Any] | None = None, *, profile: str | None = None) -> EffectiveConfig:
        raw = self._read_file()
        allowed = {"profile", "provider", "base_url", "model", "api_key_env", "default_mode", "approval", "streaming", "max_steps", "max_tool_calls", "context_budget_chars", "compact_threshold_chars", "provider_timeout_seconds", "run_timeout_seconds", "verification_command", "repair_attempts", "session_max_chars", "transaction_max_bytes", "tool_policy", "profiles"}
        unknown = set(raw) - allowed
        if unknown:
            raise ConfigError("unknown config fields: " + ", ".join(sorted(unknown)))
        _reject_secret_fields(raw)
        merged: dict[str, Any] = {
            "workspace": self.workspace, "profile": "default", "provider": "openai-compatible", "base_url": "https://api.openai.com/v1", "model": None,
            "api_key_env": "FORGECODE_API_KEY", "default_mode": "act", "approval": "interactive", "streaming": "auto", "max_steps": 12,
            "max_tool_calls": 512, "context_budget_chars": 60_000, "compact_threshold_chars": 48_000, "provider_timeout_seconds": 90.0,
            "run_timeout_seconds": 600.0, "verification_command": None, "repair_attempts": 2, "session_max_chars": 100_000, "transaction_max_bytes": 50_000_000,
            "tool_policy": ToolPolicy(), "sources": ("defaults",),
        }
        env = _env_overlay()
        merged.update(env); merged["sources"] = tuple(["defaults", "environment"] if env else ["defaults"])
        profiles = raw.get("profiles", {})
        if profiles is not None and not isinstance(profiles, dict):
            raise ConfigError("profiles must be a table")
        if isinstance(profiles, dict):
            for profile_name, profile_data in profiles.items():
                _bounded_text(profile_name, "profile name", 64)
                if not isinstance(profile_data, dict):
                    raise ConfigError(f"profile {profile_name} must be a table")
                _reject_secret_fields(profile_data)
                profile_unknown = set(profile_data) - (allowed - {"profiles", "profile", "workspace", "sources"})
                if profile_unknown:
                    raise ConfigError(f"unknown fields in profile {profile_name}: " + ", ".join(sorted(profile_unknown)))
        explicit_profile = profile is not None
        selected = profile if explicit_profile else (raw.get("profile") or os.getenv("FORGECODE_PROFILE") or "default")
        _bounded_text(selected, "profile", 64)
        selected_profile_data: dict[str, Any] = {}
        if selected != "default":
            profile_data = profiles.get(selected) if isinstance(profiles, dict) else None
            if not isinstance(profile_data, dict):
                raise ConfigError(f"profile not found: {selected}")
            selected_profile_data = profile_data
        for key in allowed - {"profiles", "tool_policy", "profile"}:
            if key in raw:
                merged[key] = raw[key]
        if "tool_policy" in raw:
            policy = raw["tool_policy"]
            if not isinstance(policy, dict):
                raise ConfigError("tool_policy must be a table")
            if set(policy) - {"allow", "deny"}:
                raise ConfigError("unknown tool_policy fields: " + ", ".join(sorted(set(policy) - {"allow", "deny"})))
            for field_name in ("allow", "deny"):
                if field_name in policy and (not isinstance(policy[field_name], (list, tuple)) or any(not isinstance(item, str) for item in policy[field_name])):
                    raise ConfigError(f"tool_policy.{field_name} must be an array of strings")
            merged["tool_policy"] = ToolPolicy(tuple(policy.get("allow", ())), tuple(policy.get("deny", ())))
        # A named profile is the most specific part of the workspace config,
        # so it overrides root-level TOML defaults while the whole file still
        # outranks environment values.  CLI overrides are applied afterwards.
        merged.update(selected_profile_data)
        merged["profile"] = selected
        if overrides:
            for key, value in overrides.items():
                if key not in allowed - {"profiles", "tool_policy"}:
                    raise ConfigError(f"unknown override: {key}")
                merged[key] = value
            merged["sources"] = tuple((*merged.get("sources", ()), "cli"))
        elif raw:
            merged["sources"] = tuple((*merged.get("sources", ()), "config"))
        if isinstance(merged.get("tool_policy"), dict):
            policy = merged["tool_policy"]
            if set(policy) - {"allow", "deny"}:
                raise ConfigError("unknown tool_policy fields: " + ", ".join(sorted(set(policy) - {"allow", "deny"})))
            for field_name in ("allow", "deny"):
                if field_name in policy and (not isinstance(policy[field_name], (list, tuple)) or any(not isinstance(item, str) for item in policy[field_name])):
                    raise ConfigError(f"tool_policy.{field_name} must be an array of strings")
            merged["tool_policy"] = ToolPolicy(tuple(policy.get("allow", ())), tuple(policy.get("deny", ())))
        try:
            config = EffectiveConfig(**merged)
        except TypeError as exc:
            raise ConfigError(f"invalid config fields: {exc}") from exc
        config.validate()
        return config


def load_effective_config(workspace: Path, overrides: Mapping[str, Any] | None = None, *, profile: str | None = None) -> EffectiveConfig:
    return ConfigLoader(workspace).load(overrides, profile=profile)


def _reject_secret_fields(value: Any, *, _path: str = "config") -> None:
    """Reject plaintext credential-shaped fields without echoing their values."""
    def walk(current: Any, path: str, depth: int) -> None:
        if depth > 20:
            raise ConfigError("configuration nesting exceeds the safety limit")
        if isinstance(current, dict):
            if len(current) > 256:
                raise ConfigError("configuration object contains too many fields")
            for key, item in current.items():
                key_text = str(key).lower().replace("-", "_")
                if any(marker in key_text for marker in ("api_key", "apikey", "access_token", "refresh_token", "password", "secret", "cookie", "authorization")):
                    # api_key_env is explicitly safe because it is only a name.
                    if key_text == "api_key_env":
                        continue
                    raise ConfigError(f"plaintext secret field is not allowed: {path}.{key}")
                walk(item, f"{path}.{key}", depth + 1)
        elif isinstance(current, (list, tuple)):
            if len(current) > 256:
                raise ConfigError("configuration array contains too many items")
            for index, item in enumerate(current):
                walk(item, f"{path}[{index}]", depth + 1)

    walk(value, _path, 0)


__all__ = ["ConfigError", "ConfigLoader", "EffectiveConfig", "ModelProfile", "Settings", "ToolPolicy", "load_effective_config"]
