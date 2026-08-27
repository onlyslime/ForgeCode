"""Configuration that is safe to keep separate from agent execution."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace: Path
    model: str | None = None
    api_key_env: str = "FORGECODE_API_KEY"

    @classmethod
    def from_environment(cls, workspace: Path) -> "Settings":
        model = os.getenv("FORGECODE_MODEL") or None
        return cls(workspace=workspace, model=model)
