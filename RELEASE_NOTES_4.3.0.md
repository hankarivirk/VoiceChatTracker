# VCBlogger v4.3.0 — Professional UI and reliability

## UI refresh
- Consistent branded headers and clearer typography across Home, Help, Live VC, History, Stats, Leaderboards, Group Settings, Health, and Issues.
- Cleaner, task-first inline dashboards and navigation buttons for common actions.
- Public Help no longer exposes owner-only operations. Owner controls live in the private `/owner` console.
- Clearer Health guidance when an Assistant session is missing or not connected.

## Owner tools
- `/broadcast <message>` prepares a preview and requires confirmation before sending.
- The owner chooses **Send to groups** or **Groups + users**; the second option is an explicit choice to attempt private delivery to users whose IDs are in the tracked-user store.
- Broadcast messages are escaped safely, paced to reduce rate-limit errors, retried once on FloodWait, and summarized with delivered/failed counts.
- `/backupnow` creates an immediate compressed backup.
- `/botstats` displays stored group, user, session, attendance-segment and incident counts.

## VC tracking and display fixes
- Added a bounded, cached profile lookup when Telegram's participant update does not include a user object.
- A fallback `User_<id>` name no longer overwrites a real participant name during roster reconciliation.
- Owner health messaging makes the Assistant requirement explicit: the Bot API alone cannot provide the detailed voice-chat participant update stream. At least one logged-in Assistant account must be connected and a member of each target group.

## Verification
- `python -m pytest -q`: **115 passed**.
- `python -m compileall -q VCBlogger`: passed.
- AST parsing and ZIP integrity checks: passed.

## Deployment note
Offline tests use mocks/in-memory storage. Live Telegram API, MTProto Assistant sessions, and production database credentials were not available in this environment, so real-group event delivery still needs to be confirmed after deployment.
