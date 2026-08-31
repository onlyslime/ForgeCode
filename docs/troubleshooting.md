# Troubleshooting

| Symptom | First checks |
|---|---|
| Provider unavailable | `config validate`, `provider health`; verify the referenced environment variable and endpoint. |
| Trust or approval denied | `trust status`, `config policy`; use Plan mode or approve only the intended risk. |
| Session says `recovery_required` | Inspect `session show/events`, then explicitly start a new run to reclaim it; never replay effects manually. |
| Patch or undo conflict | Run `diff` and review hashes; preserve the external edit and create a fresh transaction. |
| Timeout/cancel leaves work | Fetch `session result` and `events`; unresolved side effects require recovery evidence. |
| No telemetry file | Check `telemetry status`; offline mode intentionally prevents local telemetry creation. |
| Symlink tests skipped | Windows link privileges are unavailable; the tests report this platform condition explicitly. |

For machine clients, capture the complete JSON envelope, exit code, stderr,
request id, and session cursor. Do not paste API keys or full provider bodies
into bug reports.

