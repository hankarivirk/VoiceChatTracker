# VCBlogger 4.2.0

Telegram bot that records **who joined a voice chat and for how long**, with simple stats and leaderboards.

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

Add a period to `/stats`, `/top`, `/me`: `today`, `weekly` or `all` (default `all`). Example: `/top weekly`.
Every screen also has these as buttons — you never have to type them.

**Bot owner only** (`SUDO_USERS`): `/owner` panel with **Health** and **Issues** screens.

Older command names (`/leaderboard`, `/vctop`, `/vcnotices`, `/threshold` …) still work as aliases. `/mystats` also opens personal stats.

## How the screens behave
- Top Users and Top Groups refresh while open every 15 seconds; Recent VC History refreshes too.
- Buttons edit the same message instead of posting new ones.
- **My stats** always opens as its own card. Only its owner can use the buttons on it, so nobody's numbers replace somebody else's.
- Join/leave alerts are one line. A leave alert shows the time spent. Alerts auto-delete after the per-group `/disappear` setting (default 10 seconds; `/disappear 0sec` is not supported).
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


## v4.2.0 — Presence-only tracking and UI reliability

- Group time is counted only while at least one participant is present; an empty active VC contributes zero time.
- User time is based on their presence in the VC. Mic mute/unmute state is not collected or used.
- Group and user leaderboards refresh while open every 15 seconds. Active sessions are included in the live ranking.
- Legacy group sessions with participant join/leave intervals are ranked by the union of those intervals; old records without interval data use the stored duration because their true occupied time cannot be reconstructed.
- Main group menu uses Top Users and Top Groups instead of Settings. Settings remain available through `/settings`; disappearing notice lifetime uses `/disappear 5sec`, `/disappear 10sec`, `/disappear 15sec`, or `/disappear 30sec`.
- Join/leave notices include a clickable profile name, user ID, action, and leave duration.
- Owner tools include confirmed `/deletedata` (token expires in 60 seconds), `/deleteuserdata USER_ID`, `/removeuser USER_ID`, and `/unremoveuser USER_ID`.
- When the bot is added to a group, it tries to invite connected Assistant accounts only if the bot has the required admin/invite permission. Otherwise it gives the Assistant account ID and manual instructions.
- Python compile check and 81 automated tests pass. Live Telegram/Railway testing is still required.
