# video-digest

Turns the videos I like into posts. I share a TikTok, Instagram or YouTube link to the
Telegram bot (@cwti_nuc_social_bot). A few minutes later the bot sends one message for that
video: a summary, an X draft and a LinkedIn draft, with buttons to publish them. The posts go
out across the day, one tap each.

1. **listen** (systemd service): reads the bot. A link from my chat (`TELEGRAM_CHAT_ID`) is
   queued, the bot replies "Queued 1 link", and processing starts at once. A button press from
   my chat publishes a draft. Everyone else is ignored. The words I write next to the link
   ("lol this is my Monday") are my note: the X draft is built around them, and they are the
   only opinion a draft may give me. No note for a forwarded message or a message with several
   links; without a note the X draft only passes on what the creator says.
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

Every prompt also carries the `ivan-x-voice` skill for the X draft, read from
`~/.agents/skills/ivan-x-voice/SKILL.md` (`VD_VOICE_FILE` overrides it; missing = the video fails).
Every prompt carries the pattern list of the AI Slop Detector skill, read from
`~/code/ai-slop-detector/skills/ai-slop-detector/SKILL.md` (`VD_SLOP_FILE` overrides it); without
that file every video fails, on purpose.

They expect the checkout at `~/code/video-digest`, `grab` and `claude` in `~/.local/bin`, `bird`
and the Buffer CLI in `~/.npm-global/bin`
(`npm install -g --prefix ~/.npm-global @bufferapp/cli`, outside mise, so a Node upgrade
does not move it). They are systemd user units,
not cron, because `--cookies-from-browser chrome+gnomekeyring` needs the desktop keyring over
D-Bus. The keyring must be named: without a desktop session in the environment, yt-dlp guesses
wrong, sends no cookies, and TikTok answers "requiring login" for some videos.

**One reader per bot.** Telegram gives each update to one `getUpdates` reader only. Keep
`claude-telegram-bot-social.service` disabled; if it runs, link messages and button presses go
to whichever reader asks first.

## Run your own

This is my setup, published as it runs on my machine (Linux, systemd user units). To run it
for yourself:

- Create a bot with @BotFather. Put `TELEGRAM_BOT_TOKEN=...` and your own `TELEGRAM_CHAT_ID`
  in `~/.config/video-digest/env` (see `contrib/env.example`), and delete the
  `EnvironmentFile=%h/claude-telegram-bot-social/.env` line from **both** units in `systemd/`:
  that is where my token lives.
- Install [`grab`](https://github.com/hamen/video-grabber) and the Claude Code CLI.
- Write your own voice guide (how you write on X, with real examples of your posts) and set
  `VD_VOICE_FILE` to it. Mine is private, and a draft in my voice is no use to you.
- Clone the [AI Slop Detector](https://github.com/FeedHive/ai-slop-detector) skill to
  `~/code/ai-slop-detector`. `VD_SLOP_FILE` can point elsewhere, but `bin/install` checks the
  default path.
- Change what `PROMPT` in `video-digest` says about me: its first lines, and the facts about
  me in "Rules for the two posts".
- X needs a CLI that posts a tweet (`VD_BIRD`, I use `bird`) and LinkedIn the Buffer CLI; both buttons
  are optional.

The tests run with fakes for Telegram, grab, the summarizer and the posting CLIs: `bin/ci`.

## License

MIT, see [LICENSE](LICENSE).

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
