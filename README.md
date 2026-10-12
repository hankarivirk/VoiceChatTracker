# VCBlogger 4.3.0 — Professional UI & Reliability Release

Telegram bot that records **who joined a voice chat and for how long**, with a polished inline dashboard, private personal stats, owner operations, and group-separated analytics.

## Commands

**In a group**

| Command | Shows |
|---|---|
| `/start` | Menu with buttons |
| `/vc` | Live voice chat: who is in, how long |
| `/stats` | Group summary (calls, call time, member time, peak) |
| `/top` | Leaderboard (top 10) |
| `/me` | Your own time, calls, rank |
| `/settings` | Tracking and alert toggles (admins) |
| `/disappear 10sec` | Set join/leave notice lifetime (admins) |
| `/groups` | Top groups by occupied VC time |
| `/history` | Recent calls and live status |

**In a private chat:** `/start`, `/stats` (all groups), `/top` (top users), `/groups` (top groups), `/me`.

Add a period to `/stats`, `/top`, `/me`: `today`, `weekly`, `monthly` or `all` (default `all`). Example: `/top weekly`.
Every screen also has these as buttons — you never have to type them.

**Bot owner only** (`SUDO_USERS`): `/owner` console with Health, Issues, Broadcast, Backup now, and System totals. Owner-only operations are intentionally omitted from public `/help`.

Older command names (`/leaderboard`, `/vctop`, `/vcnotices`, `/threshold` …) still work as aliases. `/mystats` also opens personal stats.

## How the screens behave
- Top Users and Top Groups refresh while open every 15 seconds; Recent VC History refreshes too.
- Buttons edit the same message instead of posting new ones.
- **My stats** always opens as its own card. Only its owner can use the buttons on it, so nobody's numbers replace somebody else's.
- Join/leave alerts are one line. A leave alert shows the time spent. Alerts auto-delete after the per-group `/disappear` setting (default 10 seconds). Choose Keep, 5s, 10s, 15s or 30s in Settings; `/disappear 0sec` keeps notices in chat history.
- Settings are changed with buttons; only group admins (and owners) can change them.

## How numbers are counted
- Group time counts only while at least one person is present in the VC. User time counts while that user is present; mute/unmute state is not read. User time is saved when a person **leaves** (or the call ends). User visits shorter than the group's *minimum time* (default 5s) are not counted; group occupied time still counts while anyone is present.
- Completed calls are grouped by their end time; user presence segments are grouped by when each segment is recorded.
- **Group VC time** = time while at least one participant is present. Empty VC uptime is excluded. **Member time** = each participant's presence time added together.
- All-time numbers include all older history. Nothing is deleted automatically.
- Reading stats or settings never creates records.

## Setup
```bash
cp .env.example .env        # fill BOT_TOKEN, API_ID, API_HASH, one database URL, SUDO_USERS
pip install -r requirements.txt
python -m VCBlogger.app     # or: docker compose up --build -d
```
- `SESSION_STRING` (+ optional `ASSISTANT_SESSION_STRINGS`, max 3 accounts in total) are **user accounts**, not bots. Telegram only sends voice-chat join/leave events to a user account that is a member of the group, so each Assistant must be added to the groups it should watch. Without one, commands work but nothing is recorded.
- The **Add to group** button asks for the `manage_chat` admin right only. Telegram shows an editable confirmation.
- Database: set exactly one of `MONGO_URI` (recommended), `POSTGRES_URL`, `REDIS_URL`.
- Run one bot instance only.
- Never share `.env`, session strings or database URLs.

## Project layout
```
VCBlogger/
  app.py            start-up and background loops
  config.py         environment settings
  bot/
    client.py       Telegram wiring (commands, buttons, command menu)
    router.py       command parsing, access rules, picks the screen
    screens.py      all message texts
    keyboards.py    all buttons
    admin.py        group setting changes
    context.py      who/where + AccessDenied
  vc/               live tracking (monitor, sessions, recovery, MTProto listener)
  stats/            period stats, user tiers
  database/         MongoDB / PostgreSQL / Redis storage, backups
  utils/            formatting, permissions, logging
  tests/
```

## Owner tools (private chat only for sensitive actions)

- `/owner` opens the owner console. Its buttons open health diagnostics, recent tracking issues, broadcast preview, manual backup, and database totals.
- `/broadcast Your announcement` prepares a preview first. Confirm **Send to groups** to reach known groups, or **Groups + users** to additionally attempt private delivery to tracked user IDs. Telegram may reject private delivery when the user has not started the bot. The owner must confirm; previews expire after five minutes.
- `/backupnow` writes a compressed database backup to `BACKUP_DIR`; use persistent disk storage on cloud hosts.
- `/botstats` shows stored group, user, session, attendance-segment, and issue totals.
- User-data deletion and user blocking tools remain owner-only and require private chat; destructive data deletion still uses a confirmation token.

## Live behavior
- Open Top Users or Top Groups to auto-refresh that message every 15 seconds. Recent VC history also refreshes every 15 seconds.
- VC roster reconciliation runs every 15 seconds by default to recover missed join/leave transitions; this is not mic polling.
- Owner commands: `/deletedata` (confirmation token expires after 60 seconds), `/deleteuserdata USER_ID`, `/removeuser USER_ID`, `/unremoveuser USER_ID`.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
Tests cover the logic offline. Real voice-chat events still need a live Telegram group and Assistant account to verify.


## v4.2.2 — Settings UI hotfix

- Replaced the misleading disappearing-notice help button with direct 5s, 10s, 15s, and 30s buttons.
- Marked the currently selected notice lifetime in the settings keyboard.
- Clarified the settings text so users know to use the buttons.
- Re-ran the automated test suite and Python compile check.

## v4.2.1 — Presence-only tracking and bug fixes

- Group time is counted only while at least one participant is present; an empty active VC contributes zero time.
- User time is based on their presence in the VC. Mic mute/unmute state is not collected or used.
- Group and user leaderboards refresh while open every 15 seconds. Active sessions are included in the live ranking.
- Group rankings use participant-presence intervals or a counter explicitly marked as presence-based. Legacy records that only contain raw VC uptime are not counted as occupied time, because empty-call time cannot be safely reconstructed.
- Main group menu uses Top Users and Top Groups instead of Settings. Settings remain available through `/settings`; disappearing notice lifetime uses `/disappear 5sec`, `/disappear 10sec`, `/disappear 15sec`, or `/disappear 30sec`.
- Join/leave notices include a clickable profile name, user ID, action, and leave duration.
- Owner tools include confirmed `/deletedata` (token expires in 60 seconds), `/deleteuserdata USER_ID`, `/removeuser USER_ID`, and `/unremoveuser USER_ID`.
- When the bot is added to a group, it tries to invite connected Assistant accounts only if the bot has the required admin/invite permission. Otherwise it gives the Assistant account ID and manual instructions.
- Personal stats are never posted into a shared group message; group requests direct users to private chat, and callbacks attempt a private delivery.
- Transient leaderboard edit failures are retried instead of immediately stopping the 15-second refresh task.
- Interrupted-session recovery uses trusted occupied-time snapshots and excludes raw VC uptime and downtime.
- Python compile check and 91 automated tests pass in v4.2.2. Live Telegram/Railway testing is still required.


## v4.2.3 — Reliability, privacy, storage adapters, and stats UI

- Restored the Monthly period throughout `/stats`, `/top`, `/me`, `/groups`, and their inline period tabs, including local timezone month boundaries and December-to-January rollover.
- Made notice retention unambiguous: **Keep** is zero seconds; 5s, 10s, 15s, and 30s are available; `/disappear 0sec` keeps the notices in chat history.
- Hardened document-store query and update behavior for the PostgreSQL/Redis adapters, including nested fields, `$exists`, array membership, safe range comparisons, stable identity for legacy records, and correct upsert equality seeding.
- Serialized per-user voice-time ledger writes, aggregate repair, attendance counting, and user-data deletion within this process to reduce double-counting and delete/write races.
- Added recovery for session-count aggregate updates interrupted after the durable attendance row was written.
- User deletion now suppresses current/closing-session writes before deleting the stored data; closing sessions remain visible to privacy suppression until their close path finishes.
- Made group-title metadata writes best-effort, saved privacy state in session snapshots, and prevented recovery from recreating a deleted user's personal stats from a suppressed live-only entry.
- Improved graceful background-task cleanup and startup validation for invalid `STATS_TIMEZONE` values.
- Verification: **108 automated tests passed**; Python compilation and source AST parsing passed. Provider connectivity and live Telegram group-call behavior still require a deployment smoke test.


## v4.3.0 — Professional UI and owner tools

- Rebuilt the bot's message hierarchy with branded headings, clear status blocks, shorter labels, more consistent emphasis, and cleaner inline navigation. Common screens can now be opened without typing commands.
- Removed owner-only commands from public Help, even when the owner views it. The private owner console now provides buttons for diagnostics, issues, broadcasts, backups, and system totals.
- Added a confirm-before-send broadcast workflow with group-only and explicitly opted-in-by-owner groups-plus-users scopes, delivery throttling, FloodWait retry, and a delivery report. Announcement text is HTML-escaped and private delivery failures are reported instead of stopping the broadcast.
- Added `/backupnow` and `/botstats` for routine owner maintenance.
- Improved the owner health view with a direct warning when no Assistant account is connected; without a logged-in Assistant account that has joined the group, Telegram Bot API alone cannot provide the live VC participant stream.
- Added bounded user-profile lookups when participant updates omit user objects, cached resolved names, and prevented a later `User_<id>` placeholder from overwriting a real participant name.
- Added regression tests for owner help visibility, broadcast parsing/confirmation/recipient discovery, and participant-name enrichment.
- Verification: **115 automated tests passed**; Python compilation, AST parsing, and package integrity checks pass. Live Telegram/MTProto/database credentials were not available for an external deployment smoke test.
