# Changelog

## 4.0.0 — Full UI and structure rewrite

**New interface**
- New command set: `/vc`, `/stats`, `/top`, `/me`, `/settings` in groups; `/stats`, `/top`, `/groups`, `/me` in private chats. Old names still work as hidden aliases.
- One consistent screen layout, short texts, no divider lines, no filler or disclaimers. Emojis only for medals, live status and ON/OFF.
- Today / Weekly / All-time tabs on every stats screen (the active one is marked). "All groups" / "This group" switch where it makes sense.
- Settings are now one screen with toggle buttons that show their current state (Tracking, Join alerts, Leave alerts, End summary, minimum time).
- Join/leave alerts shortened to one line; leave shows time spent. End-of-call summary shows duration, members, peak and top 3.
- Removed screens that only showed technical noise: `/vc5`, `/groupinfo`, `/ping`, `/dbstatus`, "VC Center" and menu-of-menus pages. Health and issues moved into the owner panel.

**Wrong or misleading records fixed**
- Viewing stats no longer creates empty user records, and reading group settings no longer creates group records.
- Calls are counted in the period they **ended**, matching the voice-time ledger (before, a call and its member time could land in different days).
- Another person pressing "My stats" in a group no longer replaces your numbers; personal cards are locked to their owner.
- Switching tracking off mid-call no longer leaves people "in the call" (which inflated their time later); leaves are always processed.
- Owner "Issues" in a private chat now shows all issues (it used the owner's user id as a group id and was always empty).
- Placeholder names like `User_123` are never shown; long rosters are capped so a reply can never exceed Telegram's size limit.
- Group name is stored when it changes instead of on every command.

**Structure**
- `bot/` split into `client` (Telegram only), `router` (rules), `screens` (texts), `keyboards` (buttons), `admin`, `context`.
- Removed dead code: `commands.py`, `snapshots.py`, `leaderboard.py`, `group_stats.py`, Monthly period.
- Docs merged into one README; docs now match the real defaults.

## Earlier versions
### 3.4.x — Professional redesign and lifetime-history safety

## User interface and commands
- Reworked the inline navigation into compact, separated screens for dashboard, voice chat, statistics, global groups, global participants, settings, help, and owner diagnostics.
- Replaced Monthly period shortcuts with Today, Weekly, and All-Time in the UI; historical monthly rows remain preserved.
- Global group ranking shows group names and statistics only, with no group IDs or Join controls.
- Global participant rankings use display names and clickable Telegram profile links where identity data is available.
- Public BotFather command menu exposes intended user commands only; diagnostics and legacy aliases are not advertised publicly.
- Added a Bot Owner panel restricted to `SUDO_USERS`; group admins retain group-scoped controls.
- The Add-to-Group button requests only the `manage_chat` admin flag for group-admin verification; rights are editable/confirmed by the user, and destructive moderation rights are not requested.

## Voice-chat monitoring
- Added support for up to three configurable MTProto Assistant user sessions using `SESSION_STRING` and optional `ASSISTANT_SESSION_STRINGS`.
- Added per-account connection status, reconnect-compatible multi-client startup, and per-account active-call discovery/reconciliation.
- Reconciliation can notify recovered joins/leaves after an established baseline when authoritative roster changes are available; the first roster is seeded silently to avoid false join spam on startup.
- Join and Leave notifications have independent per-group switches and remain visible by default (`VC_EVENT_MESSAGE_TTL_SECONDS=0`).
- Kept short-duration filtering for attendance totals; short visits below the configured threshold do not add duration to lifetime aggregates.

## Historical data and statistics
- Disabled automatic deletion and compaction of old sessions, incident details, attendance records, and voice-time ledger rows.
- Retention maintenance now reports storage/history status without deleting records, including when called in force mode.
- Updated group and participant ranking queries to avoid prior fixed result caps that could omit historical rows from All-Time calculations.
- Added tests for preserving old records, independent Join/Leave toggles, and persistent notices.
- Stabilized the period-stat test fixture against the current date.

## Operational notes
- Multiple Assistants still require each user account to have access to the groups being tracked; Telegram access restrictions and missing events cannot be bypassed.
- Live Telegram/MTProto and live database verification require real deployment credentials and were not performed in the offline build environment.
- The historical data preservation policy can increase database storage indefinitely; monitor provider quotas and maintain independent backups.


## v3.4.1 — Startup responsiveness hotfix
- Start the Telegram Bot API client immediately after database connection, before slow ledger reconciliation, dangling-session recovery, or Assistant discovery.
- Assistant sessions are optional for command responsiveness; no Assistant or an invalid/slow Assistant no longer prevents `/start` and other bot commands from coming online. Detailed participant tracking still requires a logged-in user session with access to target groups.
- Bound each Assistant startup attempt and active-call discovery; failures are logged while the bot remains online.
- Added explicit startup logs to identify when the bot is ready and when Assistant tracking is unavailable.

## v3.4.2 — Compact UI hotfix
- Replaced long welcome/help copy with short readable text.
- Reduced inline navigation to a few contextual buttons per screen.
- Reduced Telegram slash-command suggestions to the main five per chat type.
- Defaulted Join/Leave notices to auto-delete after 10 seconds; notice includes clickable name and Telegram user ID.
- Global group rankings and participant leaderboards default to top 10.
- Kept All-Time history and existing database records intact.


## 4.2.0
- Reworked group duration to count occupied VC time only.
- Removed mute/unmute state collection and processing.
- Added 15-second live refresh for user/group leaderboards.
- Simplified the main menu and leaderboard navigation; settings moved to `/settings`.
- Added disappearing notice duration command, structured join/leave notices, owner user-management commands, confirmed database wipe, and Assistant auto-join attempt with manual fallback.
- Added adapter delete-many support and improved legacy occupied-time calculation from participant intervals.
