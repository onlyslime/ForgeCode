# Changelog

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
