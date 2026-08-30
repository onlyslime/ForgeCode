# ForgeCode v0.8.25 offline walkthrough

This deterministic, offline demo exercises the production AgentLoop, tool
registry, approval boundary, named test runner, durable session, transaction
ledger, and evidence review. It needs Python 3.11+, `uv`, and no API key.
Always use a fresh temporary workspace: the fixture writer refuses to
overwrite an existing fixture.

## 1. Prepare and inspect

From the repository root, install dependencies and inspect the local runtime:

```powershell
uv sync
uv run forgecode doctor
uv run forgecode tools
uv run forgecode provider list
uv run forgecode provider health
uv run forgecode skills list
uv run forgecode config profiles
```

Create an isolated workspace and run the scripted scenario:

```powershell
$demo = Join-Path ([IO.Path]::GetTempPath()) ('forgecode-demo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $demo | Out-Null
uv run forgecode --workspace $demo run --demo --auto-approve
```

The offline provider asks the agent to inspect a calculator fixture, reproduce
an intentional failing edge case, apply a validated unified patch, and rerun
the regression test. `--auto-approve` is suitable only for this disposable
workspace.

## 2. Inspect durable evidence

Run the named test profile and inspect the same session after the process exits:

```powershell
uv run forgecode --workspace $demo test list --jsonl
uv run forgecode --workspace $demo test run default --auto-approve --jsonl
uv run forgecode --workspace $demo sessions
uv run forgecode --workspace $demo status
uv run forgecode --workspace $demo diff
uv run forgecode --workspace $demo transaction
uv run forgecode --workspace $demo review --jsonl
```

Export a review artifact and verify its workspace/hash binding:

```powershell
uv run forgecode --workspace $demo review --export review-artifact.json --jsonl
uv run forgecode --workspace $demo review --verify review-artifact.json --jsonl
```

Expected evidence includes `status=completed`, a passing verification result,
an audit-complete session, bounded stdout/stderr, approval metadata, changed
file hashes, and a review report. A non-zero command or denied approval must
remain visible in the session; never infer success from the final model text.

## 3. Context and recovery paths

The context commands build an ignored, bounded local index and expose session
recovery metadata:

```powershell
uv run forgecode --workspace $demo context index --json
uv run forgecode --workspace $demo context search "calculator" --json
uv run forgecode --workspace $demo context show --json
uv run forgecode --workspace $demo context complete demo --jsonl
uv run forgecode --workspace $demo session tree --jsonl
uv run forgecode --workspace $demo eval latest --jsonl
```

To demonstrate hash-checked undo, execute the latest recorded transaction in
the disposable workspace and inspect the ledger again:

```powershell
uv run forgecode --workspace $demo transaction latest --execute --auto-approve
uv run forgecode --workspace $demo transaction
```

Undo intentionally restores the pre-fix state, so the original failing test
can fail again. Do not run it against a real project without a backup.

## What this demonstrates

The run proves that provider output is normalized before dispatch, tool
arguments and paths are validated, side effects cross an explicit approval
boundary, writes are atomic and reversible, command limits are enforced, and
all model/tool/verification outcomes are persisted as auditable JSONL events.
