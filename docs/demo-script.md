# ForgeCode assessment demo (within 2 minutes)

The demo is deterministic and offline. It needs Python 3.11+, `uv`, and no
API key. Always use a fresh temporary directory: the CLI refuses to overwrite
an existing `demo_calculator.py` or `test_demo_calculator.py`.

```powershell
uv sync
uv run forgecode doctor
uv run forgecode tools
$demo = Join-Path ([System.IO.Path]::GetTempPath()) ('forgecode-demo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $demo | Out-Null
uv run forgecode --workspace $demo run --demo --auto-approve
```

## What to point out

1. `doctor` and `tools` show the locally implemented provider-neutral
   protocol, loop and tool registry.
2. Before the model loop starts, the CLI creates the two fixture files using
   the normal approved `write_file` path. The model itself then calls
   `workspace_summary` and `read_file`, so it must inspect real contents.
3. The first `python -B -m pytest -q test_demo_calculator.py` returns a real
   assertion failure, non-zero exit code and bounded stdout/stderr.
4. The model sends a unified diff. ForgeCode validates every hunk and path in
   memory, prints a bounded preview, records approval, then atomically replaces
   the source file.
5. The second pytest and the final verification pass. The terminal shows mode,
   approvals, risk metadata, changed files, diff, final message and session
   JSONL path.

For the read-only boundary, run the same command with `--mode plan`:

```powershell
$plan = Join-Path ([System.IO.Path]::GetTempPath()) ('forgecode-plan-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $plan | Out-Null
uv run forgecode --workspace $plan run --demo --mode plan --auto-approve
```

Plan mode exposes summary/read-only tools, records a plan, skips verification,
and cannot write, patch or run commands even if a provider requests one.

## If something fails

- `workspace is not a directory`: create the temporary directory first.
- `FORGECODE_API_KEY is not configured`: the command omitted `--demo`; use the
  offline command above or configure the three provider environment variables.
- A fixture conflict means the workspace is not fresh; choose another temp
  directory. Inspect the printed session file for structured events.

The demo deliberately does not claim IDE UI, browser control, MCP marketplace,
cloud execution, worktrees or parallel agents. It demonstrates the self-built
local inspect -> fail -> patch -> verify loop.
