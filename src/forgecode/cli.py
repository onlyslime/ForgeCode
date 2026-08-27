"""Stable CLI entry point kept small for downstream callers."""

from .application.commands import _parser, main

__all__ = ["_parser", "main"]
