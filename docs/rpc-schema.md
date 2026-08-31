# RPC schema quick reference

The JSONL service uses `schema_version: 1`. Each request is a JSON object with
an optional bounded `id`, `method`, and `params`; each response echoes the id
and contains exactly one `data` or `error` branch plus `ok`, `kind`, `command`,
and `exit_code`.

```json
{"id":"demo-1","method":"session.status","params":{"handle":"abc"}}
{"id":"demo-1","schema_version":1,"kind":"result","ok":true,"data":{"state":"completed"},"exit_code":0}
```

Core methods include `run`, `session.open|run|status|events|wait|result|cancel|
pause|resume|approval|close|inspect|tree|list`, `provider.list|health`,
`config.profiles|policy`, and `rpc.describe`. Request lines are capped at 1
MiB; ids and strings are bounded and newline-safe. Reusing an id returns the
original response without replaying a side effect. Handles have bounded TTL
and active-count limits.

Errors are typed (`invalid_params`, `request_too_large`, `busy`, `terminal`,
`trust_revoked`, `recovery_required`, `process_error`, `cancelled`, and
`timeout`). Read-only status/result/wait/events remain available for audit
after trust revocation. See `rpc-sdk.md` for lifecycle and embedding details.

