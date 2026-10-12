# VCBlogger v4.2.2 — Settings UI hotfix

## Fixed
- The disappearing-notice control previously looked like a command hint and opened Help instead of changing the setting. It now provides direct 5s, 10s, 15s, and 30s buttons.
- The active notice lifetime is marked in the keyboard.
- Settings copy now clearly tells admins to choose a duration below.

## Verification
- `python -m pytest -q`: 91 passed.
- `python -m compileall -q VCBlogger`: passed.

## Notes
- This is an offline code/test verification. A live Telegram group and Assistant user account are still needed to validate actual VC events and Telegram UI behavior.
