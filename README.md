# video-digest

A daily Telegram digest of the videos I send to a bot.

1. **listen** (systemd service): I share a TikTok, Instagram or YouTube link to the bot from
   the phone. The bot queues it and replies "Queued 1 link." It reads only my chat
   (`TELEGRAM_CHAT_ID`) and ignores everyone else.
2. **process** (timer, every 10 minutes): [`grab`](https://github.com/hamen/video-grabber)
   downloads and transcribes each queued link, and `claude -p --model sonnet` writes an
   actionable summary: what the video says, the concrete claims, and one action. A link that
   fails twice is marked failed.
3. **digest** (timer, 9:00 Rome): one Telegram message with every summary and failure not sent
   yet. Nothing is sent on a day with no videos.

State is in `~/.local/state/video-digest/`: `items.json` (the queue and the summaries,
locked with flock because the three units write it), `telegram-offset`, and `videos/`
(transcripts and metadata; `grab --no-video` deletes the videos).

## Install

```bash
mkdir -p ~/.config/video-digest
cp contrib/env.example ~/.config/video-digest/env && chmod 600 ~/.config/video-digest/env
# fill in TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_CHAT_ID
bin/install
```

The units expect the checkout at `~/code/video-digest`, `grab` and `claude` in
`~/.local/bin`, and the headless Claude token in `~/.config/claude-code-token.env` (the same
file as the standup cron). They run as systemd user units, not cron, because
`--cookies-from-browser chrome` needs the desktop keyring over D-Bus.

## Operate

```bash
systemctl --user status video-digest-listen
journalctl --user -u video-digest-process -n 50
systemctl --user start video-digest-process   # process the queue now
systemctl --user start video-digest-digest    # send the digest now
```

The bot token belongs to this bot only. Telegram allows one `getUpdates` reader per bot, so
never point another poller (the standup button, a second listen) at the same token.

## Develop

```bash
git config core.hooksPath .githooks   # once per clone
bin/ci
```
