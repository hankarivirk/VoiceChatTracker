# VCBlogger v4.2.1 — Bug-fix release

## Fixed
- Group rankings no longer fall back to raw VC uptime for legacy sessions that have no participant timestamps. New session records explicitly mark presence-based occupied counters.
- Today/Weekly group ranking clips presence intervals to the requested period, including overnight calls.
- Recovery does not trust unmarked legacy `occupied_seconds`; it reconstructs only from participant intervals up to the last saved heartbeat.
- Active user/group statistics include currently present participants. Active groups count as one live call in the group ranking.
- My Stats no longer publishes private personal data into group chat messages. Group `/me` directs users to private chat; callback clicks try a private message and fall back to a private deep link.
- Ranking refresh retries transient Telegram/API errors instead of stopping after the first failure. Selecting an already-active period restarts refresh.
- `/deletedata` clears disposable leaderboard cache keys and pauses already-running calls until those calls end, preventing an immediate database repopulation.
- `/removeuser` now stops the user's current-session recording immediately. `/deleteuserdata` clears their personal profile and session snapshots but preserves their independent blocked/unblocked status.
- Assistant setup responds immediately and performs bounded background retries if the Assistant session is still starting.
- Group titles can be learned from MTProto updates.
- Settings display the current notification expiry, and leave notices show duration on its own line.

## Validation
- `python -m compileall -q VCBlogger`
- `python -m pytest -q` — 91 tests passed.

Live Telegram VC and Railway deployment are not testable from this local build environment; those must be verified after redeploy.
