# Changelog

## v0.5.4 — 2026-08-29

- Unified interactive `/login` with `/connect` and added an explicit provider
  selection screen.
- Models are no longer silently defaulted; users must enter the model while a
  recommendation is shown.
- Fixed bypass-mode interactive checkpoints and rendered command feedback.

## v0.5.3 — 2026-08-29

- Consolidated provider setup around `/connect`; `/login` is now a compatibility
  alias with guidance to use `/connect`.
- Added built-in provider defaults for OpenAI, Anthropic, Google, DeepSeek,
  OpenRouter, Groq, Mistral, xAI, and Ollama, including endpoint, credential
  environment variable, and recommended model hints.
- OpenAI-compatible adapters can now target the expanded provider catalog.

## v0.5.2 — 2026-08-29

- Fixed human-readable `/status` output; it now shows mode, run ID, last state,
  transaction count, verification state, and worker queue status.

## v0.5.1 — 2026-08-29

- Fixed the interactive TTY prompt to catch invalid slash commands and render
  a recoverable error instead of terminating the chat process.

## v0.5.0 — 2026-08-29

- Started the repair-focused release line. This release only updates the
  version and records the theme; command-by-command fixes will follow after
  real interactive reproduction.

## v0.4.4 — 2026-08-29

- Added controlled background process tools: `run_background`,
  `process_status`, `poll_process`, and `kill_process`.
- Background tasks have ForgeCode-owned IDs, bounded incremental output, status
  and exit metadata, cancellation, approval, and command risk checks.

## v0.4.3 — 2026-08-29

- Added read-only `git_log` for recent commit history.
- Added approval-gated `git_commit`; it refuses plan mode, cancellation, and
  empty unstaged commits.

## v0.4.2 — 2026-08-29

- Added `read_range` for precise bounded line-range inspection.
- Added `list_symbols` for lightweight source structure discovery.
- Added `file_metadata` for encoding, size, line count, mtime, and SHA-256
  inspection.

## v0.4.1 — 2026-08-29

- Added `find_files` for bounded glob discovery.
- Added `test` for approved, bounded project test execution.
- Added `diagnostics` for approved compile/lint-style checks with structured
  exit results.

## v0.4.0 — 2026-08-29

- Started the tools-focused release line with dedicated read-only `git_status`
  and `git_diff` tools for auditable repository inspection and review.
- The tools are workspace-scoped, bounded, and available through the normal
  registry and `/tools` inventory.

## v0.3.7 — 2026-08-29

- Added the interactive `/tools` command with descriptions and mode-aware
  availability, plus slash completion support.

## v0.3.6 — 2026-08-29

- Added a compact startup status card with mode, model, tool count, and
  workspace state.
- Added visible Understand/Inspect/Modify/Verify phase separators and numbered
  tool steps to the human timeline.
- Added a structured `Completed` summary with verification status and tool-step
  count.

## v0.3.5 — 2026-08-29

- Improved the human timeline with bounded file-content previews (line
  numbers), command/search output panels, truncation hints, and cumulative tool
  step counts in the final `Worked for …` summary.

## v0.3.4 — 2026-08-29

- Enabled SSE streaming by default for profiles using `streaming = "auto"`;
  providers without stream transport still fall back to normal completion.
  This makes supported interactive providers visibly responsive without
  changing machine-output contracts.

## v0.3.3 — 2026-08-29

- Improved human-readable task timelines with numbered assistant turns,
  elapsed time, and cumulative tool-step counts.

## v0.3.2 — 2026-08-29

- Added immediate assistant progress events before each model turn, so
  multi-step tasks visibly show analysis and continuation instead of appearing
  silent between tool calls.

## v0.3.1 — 2026-08-29

- Bound standalone `Esc` in the prompt UI to cancel the active task while
  keeping the chat session and input buffer available.

## v0.3.0 — 2026-08-29

- Started the 0.3 release line with the current interactive launcher modes,
  slash-command completion, live progress display, and robust tool-call
  context handling.

## v0.2.12 — 2026-08-29

- Added `fcc --plan` and `fcc --act` launch shortcuts alongside
  `fcc --bypass`.
- Fixed `/clear` to flush the terminal clear sequence immediately and return
  a structured result for interactive transports.

## v0.2.11 — 2026-08-29

- Added `fcc --bypass` to launch directly in bypass mode.
- Added interactive slash-command completion; typing `/m` suggests commands
  such as `/mode` and `/model`.

## v0.2.10 — 2026-08-29

- Added a conversational execution contract: the model is instructed to give a
  brief plan before tools, concise progress updates during multi-step work, and
  a final summary with verification and remaining limitations.

## v0.2.9 — 2026-08-29

- Interactive chat now renders assistant progress messages as soon as each
  model turn completes, instead of showing only the final response. Tool
  progress remains visible and machine JSON output is unchanged.

## v0.2.8 — 2026-08-29

- Replaced the `fc` launcher with `fcc` to avoid PowerShell's built-in alias.

## v0.2.7 — 2026-08-29

- Added the `fc` executable shortcut, which opens chat directly without
  arguments.
- Fixed runtime duration tracking by importing the monotonic clock module.

## v0.2.6 — 2026-08-29

- Added elapsed runtime markers to interactive progress events and a final
  `Worked for …` duration in completed chat responses.

## v0.2.5 — 2026-08-29

- Removed the default fixed 12-step AgentLoop cap. Runs now continue until the
  model finishes, fails, is cancelled, or an explicit `max_steps` is set.

## v0.2.4 — 2026-08-29

- Added dark-background file previews with unified red deletion and green
  addition lines during write operations.

## v0.2.3 — 2026-08-29

- Added inline previews for write and patch operations in interactive progress,
  with green additions and red deletions.

## v0.2.2 — 2026-08-29

- Improved live progress labels with file paths and command text, including
  distinct success and failure markers for tool and verification events.

## v0.2.1 — 2026-08-29

- Added live human-readable progress events for interactive runs, including
  tool calls, successful/failed results, and verification status.
- Progress lines use cyan, green, and red markers and remain above the input
  area.

## v0.2.0 — 2026-08-29

- Promoted the stable multiline, fixed-footer terminal chat interface to the
  0.2 feature release.
- Enter submits input, Shift+Enter inserts newlines, and multiline rendering
  remains compatible with the supported prompt-toolkit callback signature.

## v0.1.6 — 2026-08-29

- Fixed multiline prompt rendering on prompt_toolkit versions that pass the
  wrap-count argument to continuation callbacks.

## v0.1.5 — 2026-08-29

- Styled multiline continuation rows so the entire input buffer keeps the
  dark input background.
- Enter submits; the terminal's Shift+Enter escape sequence inserts a newline.

## v0.1.4 — 2026-08-29

- Fixed chat startup failure caused by an unsupported prompt-toolkit
  `s-enter` binding; Ctrl-J now inserts a newline while Enter submits.

## v0.1.3 — 2026-08-29

- Enter now submits chat input; Shift+Enter inserts a newline in the multiline
  buffer.
- Added explicit dark styling for the fixed input area.

## v0.1.2 — 2026-08-29

- Added a prompt-toolkit chat surface with a fixed bottom multiline input
  buffer and safe asynchronous output repainting.
- Pasted multiline content is submitted as one prompt when Enter is pressed.

## v0.1.1 — 2026-08-29

- Published the next patch release after validating interactive bypass-mode
  file creation with a short `hello.txt` task.
- Keeps long provider requests unchanged for a follow-up investigation; those
  requests may still hit the configured provider deadline.
- Verification: `uv run python -m compileall -q src`; `forgecode doctor`.
