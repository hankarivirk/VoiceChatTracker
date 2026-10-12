# VCBlogger v4.2.0 — Presence Tracking + Professional UI

## Included
- Group VC duration counts only while at least one participant is present. Empty VC uptime contributes zero group time.
- User VC duration counts only while that user is present. Mic mute/unmute state is not fetched, stored, or used for counting.
- Top Users and Top Groups screens refresh every 15 seconds while open; Recent VC History refreshes and includes current live status.
- Group ranking uses occupied-time intervals where participant join/leave history exists. Legacy sessions without interval timestamps use stored duration because exact occupied time cannot be reconstructed from missing data.
- Group menu replaces Settings with Top Groups. Settings are accessible through `/settings`, with left-side setting labels and right-side ON/OFF controls.
- Disappearing notices can be configured by admins with `/disappear 5sec`, `/disappear 10sec`, `/disappear 15sec`, or `/disappear 30sec`.
- Join/leave notices contain a clickable first name, Telegram ID, action, and leave duration. They auto-delete using each group's configured TTL.
- Personal stats cards are locked to the user who requested them.
- Owner commands: `/deletedata` (60-second confirmation token), `/deleteuserdata USER_ID`, `/removeuser USER_ID`, `/unremoveuser USER_ID`.
- Assistant auto-setup attempts to invite configured, connected Assistant accounts only if the bot is a group admin with invite permission. Otherwise it reports Assistant IDs and manual steps.
- Group roster recovery defaults to 15 seconds (not mic polling) to improve recovery of missed participant events.
- Adapter `delete_many` support added for safe data management; optional PostgreSQL archive cleanup included.

## Validation
- `python -m compileall -q VCBlogger` — passed.
- `pytest -q VCBlogger/tests` — 81 passed.
- Live Telegram VC and Railway deployment tests have **not** been run in this environment; verify after deploying.
