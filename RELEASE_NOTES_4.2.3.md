# VCBlogger v4.2.3 — Reliability and bug-fix release

## Fixed
- Restored Monthly period navigation across statistics commands, with timezone-aware calendar-month boundaries and year rollover.
- Replaced the confusing disappearing-notice UI behavior with an explicit Keep choice and direct 5s/10s/15s/30s choices. `/disappear 0sec` keeps notices in history.
- Improved the document-store adapters' query semantics for nested fields, `$exists`, `$in`/`$nin`, range comparisons, dotted updates, sorting of mixed legacy fields, identity lookup, and equality-only upserts.
- Serialized per-user voice-time writes, aggregate repair, attendance recording, and personal-data deletion within one process.
- Repaired session-count aggregates from the durable attendance ledger if execution stopped after a ledger row was written but before counters were updated.
- Kept sessions visible to privacy suppression during asynchronous close and applied suppression before user data deletion.
- Made transient group-title writes best-effort and preserved active-session tracking state across snapshots/recovery.
- Added cancellation and cleanup of outstanding UI refresh/Assistant setup tasks, plus earlier validation for invalid timezone configuration.

## Verification
- `python -m pytest -q`: **108 passed**.
- `python -m compileall -q VCBlogger`: passed.
- AST parse of all project Python files: passed.
- ZIP integrity check: passed after packaging.

## Limitations
Tests run offline using mocks/in-memory storage. A live Telegram group, MTProto Assistant accounts, and real MongoDB/PostgreSQL/Redis credentials were not available here, so actual service connectivity and production voice-chat event delivery still need a deployment smoke test.
