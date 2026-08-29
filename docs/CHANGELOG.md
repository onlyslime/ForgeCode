# Changelog

## v0.2.0 — 2026-08-29

- Added a dedicated Windows terminal input buffer for chat, so Enter submits a
  complete edited payload and pasted CRLF text is kept together.
- Asynchronous model output now redraws above the active input area.
- Verification: targeted interactive tests and Python compile check.

## v0.1.1 — 2026-08-29

- Published the next patch release after validating interactive bypass-mode
  file creation with a short `hello.txt` task.
- Keeps long provider requests unchanged for a follow-up investigation; those
  requests may still hit the configured provider deadline.
- Verification: `uv run python -m compileall -q src`; `forgecode doctor`.
