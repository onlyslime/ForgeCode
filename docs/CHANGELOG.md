# Changelog

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
