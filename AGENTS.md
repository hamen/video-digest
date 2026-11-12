# AGENTS.md

- `video-digest` is one Python file, standard library only. Keep it that way.
- Run `bin/ci` before you push; the pre-push hook runs it too. Never bypass the hook.
- Every behavior change gets a test in `tests/test_video_digest.py`. The tests use a fake
  Telegram server and the fakes in `tests/fakes/`; they never call the real bot, grab or Claude.
- The bot must answer only `TELEGRAM_CHAT_ID`. A change that lets another chat queue links or
  receive messages is a security bug.
- Secrets live in `~/.config/video-digest/env`, never in the repo.
- To test against the real services, use a scratch `VD_STATE_DIR`, never the live state in
  `~/.local/state/video-digest/`.
