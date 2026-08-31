# Testing and verification

Use the smallest gate that matches the change. Documentation-only changes
need link and formatting checks; shared runtime, protocol, persistence, or
security changes require their targeted regression suites.

```powershell
uv run python -m compileall -q src tests
uv run pytest tests/test_cli_machine_contract.py -q
uv run pytest -rs                 # release milestone gate
uv run forgecode doctor --json
```

The offline walkthrough in `demo-script.md` proves Plan/Act, patching, named
tests, session evidence, review, and undo in a fresh temporary workspace.
`test list|show|run` uses bounded named profiles; `eval` scores durable
trajectory evidence rather than trusting model prose. Windows symlink tests
may be skipped when the host denies link creation; this is reported by pytest,
not hidden.

