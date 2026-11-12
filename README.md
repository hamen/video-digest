# video-digest

Turns the videos I like into posts. I share a TikTok, Instagram or YouTube link to the
Telegram bot (@cwti_nuc_social_bot). A few minutes later the bot sends one message for that
video: a summary, an X draft and a LinkedIn draft, with buttons to publish them. The posts go
out across the day, one tap each.

1. **listen** (systemd service): reads the bot. A link from my chat (`TELEGRAM_CHAT_ID`) is
   queued, the bot replies "Queued 1 link", and processing starts at once. A button press from
   my chat publishes a draft. Everyone else is ignored.
2. **process** (started by listen; a timer reruns it every 10 minutes for retries):
   [`grab`](https://github.com/hamen/video-grabber) downloads and transcribes the video, and
   `claude -p --model sonnet` writes the summary and the two drafts. The drafts credit the
   creator, may use only two facts about me, and must invent nothing. Then one message per
   video, with buttons. A link that fails twice gets a failure message without buttons.
3. **Buttons**: "Post on 𝕏" runs `bird tweet`, "Post on LinkedIn" runs the Buffer CLI with
   `shareNow` (the same tools as the standup button), "Both" does both. Each destination is
   recorded the moment it succeeds: a second press says "already posted" and posts nothing.
   The bot replies under the video message with the result.

State is in `~/.local/state/video-digest/`: `items.json` (queue, drafts, message ids and what
was posted; locked with flock because both units write it), `telegram-offset`, and `videos/`
(transcripts and metadata; `grab --no-video` deletes the videos).

## Install

```bash
mkdir -p ~/.config/video-digest
cp contrib/env.example ~/.config/video-digest/env   # fill in TELEGRAM_CHAT_ID
bin/install
```

The units read three more files and never copy them:

- `~/claude-telegram-bot-social/.env`: the bot token (`TELEGRAM_BOT_TOKEN`).
- `~/.config/standup/buffer.env`: `BUFFER_API_KEY` and `BUFFER_LINKEDIN_CHANNEL` for LinkedIn
  (optional; without it the LinkedIn button says so).
- `~/.config/claude-code-token.env`: headless auth for `claude -p`, like the standup cron.

They expect the checkout at `~/code/video-digest`, `grab` and `claude` in `~/.local/bin`, `bird`
in `~/.npm-global/bin` and the Buffer CLI under mise's Node 22.16. They are systemd user units,
not cron, because `--cookies-from-browser chrome` needs the desktop keyring over D-Bus.

**One reader per bot.** Telegram gives each update to one `getUpdates` reader only. Keep
`claude-telegram-bot-social.service` disabled; if it runs, link messages and button presses go
to whichever reader asks first.

## Operate

```bash
systemctl --user status video-digest-listen
journalctl --user -u video-digest-listen -n 50     # queued links, button presses, posts
journalctl --user -u video-digest-process -n 50    # downloads, summaries, sent messages
systemctl --user start video-digest-process        # process the queue now
```

## Develop

```bash
git config core.hooksPath .githooks   # once per clone
bin/ci
```
