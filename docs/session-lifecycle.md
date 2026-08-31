# Session lifecycle and recovery

Sessions persist bounded metadata and append-only JSONL evidence under the
workspace's ignored `.forgecode/` directory. Effects are never replayed from
an event log.

```text
created -> discovering -> planning -> awaiting_approval -> acting -> verifying -> completed
                                      |                  |
                                      v                  v
                                   paused            cancelled/failed
                         unsafe restart -> recovery_required
```

Use `session show`, `events`, `result`, and `wait` for read-only inspection.
`pause`, `resume`, `cancel`, and `approval` are control operations; Act controls
recheck trust and workspace identity. A daemon restart marks an orphaned
running handle `recovery_required` with `worker_alive=false`; only an explicit
new `session.run` may reclaim it.

`session tree` shows lineage. `fork` creates a linked run, `clone` creates an
inspect-only child, and `import` validates evidence without executing it.
`transaction undo` requires matching hashes; an external edit produces
`transaction_conflict` and leaves the workspace untouched.

RPC handles have bounded TTL and active-count limits. Clients should persist
the request id, poll or use `wait`, then fetch `result` and `events` using the
returned cursor. Trust revocation still permits cancellation and read-only
evidence retrieval, while new Act execution remains denied.

