# OZY Translator Bot - Hardened Edition

A 10-channel Discord translation bridge for OZY. Messages posted in one configured language channel are translated to the other nine channels and delivered through channel-specific Discord webhooks while preserving the source author's display name and avatar.

## Supported channels

| Language | Code | Channel | Role | Webhook env |
|---|---|---|---|---|
| English | `en` | `#english` | `EN` | `WEBHOOK_EN` |
| Spanish | `es` | `#español` | `ES` | `WEBHOOK_ES` |
| French | `fr` | `#français` | `FR` | `WEBHOOK_FR` |
| Portuguese | `pt` | `#português` | `PT` | `WEBHOOK_PT` |
| Swedish | `sv` | `#svenska` | `SE` | `WEBHOOK_SV` |
| German | `de` | `#deutsch` | `DE` | `WEBHOOK_DE` |
| Bisaya / Cebuano | `ceb` | `#bisaya` | `PH` | `WEBHOOK_CEB` |
| Russian | `ru` | `#русский` | `RU` | `WEBHOOK_RU` |
| Arabic | `ar` | `#العربية` | `AR` | `WEBHOOK_AR` |
| Norwegian | `no` | `#norsk` | `NO` | `WEBHOOK_NO` |

The configured Discord IDs remain in `settings.py`. The bot refuses to start if a webhook points to the wrong channel or guild.

## What changed from the original bot

### Security and configuration

- Requires `SERVER_ID` and refuses messages from any other guild.
- Validates every webhook URL structurally before startup.
- Fetches every webhook at startup and verifies that its actual Discord channel ID and guild match the expected configuration.
- Rejects duplicate webhook IDs.
- Uses `discord.AllowedMentions.none()` for all translated webhook messages, preventing replicated `@user`, `@role`, and similar pings.
- `.env` remains ignored; `.env.example` contains placeholders only.

### Translation reliability

- Preserves the known source language instead of asking Google to auto-detect every short Discord message.
- Protects URLs, code blocks, inline code, Discord mentions, channel mentions, timestamps, slash-command mentions, custom emoji, and Total Battle coordinates before translation.
- Verifies protected placeholders survived translation before restoring them.
- A failed translation is visibly marked as unavailable instead of silently pretending the untranslated source is a successful translation.
- Adds explicit HTTP connect/read timeouts around the `deep-translator` Google request.
- Uses a global 200 ms request-start gate and configurable translation semaphore. Requests can overlap, but new Google calls do not all start at once.
- A detected HTTP 429 activates a global translation cooldown.

### Ordering and lifecycle

- Create, edit, and delete events enter one FIFO event queue.
- The nine destination translations run concurrently for each source message.
- The next source event is not processed until delivery of the current event finishes, preserving conversation ordering.
- Edit events remove the old translated copies and post fresh translations marked `[Edited]`.
- Delete events remove the translated copies.
- Source-to-webhook message IDs are stored in SQLite. This survives process restarts only when the filesystem itself persists.
- SQLite stores IDs only, not chat text.
- Render free services use an ephemeral filesystem, so the SQLite mapping is lost on a Render restart, redeploy, or spin-down. For guaranteed cross-deploy edit/delete cleanup, move `MessageState` to Render Postgres/Key Value or attach a persistent disk on an eligible plan.
- The old Flask thread, Gunicorn dependency, import-time server startup, and duplicate `on_ready()` keepalive tasks are gone.
- Health endpoints `/` and `/healthz` run on `aiohttp` in the same asyncio process as Discord.

### Discord delivery and media

- Uses `discord.Webhook` instead of manually constructing webhook HTTP requests.
- Uses `wait=True` so destination message IDs can be recorded.
- Recreates `discord.File` objects on retries so a consumed file stream is never reused.
- Normal Discord attachments are re-uploaded when they fit configured size limits. Oversized or failed downloads fall back to links.
- GIF/image/video embed URLs are deduplicated.
- PNG/APNG/GIF stickers are re-uploaded. Lottie stickers fall back to a visible `[Sticker: name]` marker instead of claiming they were converted to PNG.
- Message splitting prefers paragraphs/newlines/sentences/spaces and avoids splitting normal protected URLs, mentions, coordinates, and code blocks when possible.

## Installation

Python 3.11+ is recommended.

```bash
python -m venv .venv
```

Windows:

```bat
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with the real bot token, `SERVER_ID`, and all ten webhook URLs.

## Discord bot settings

In the Discord Developer Portal, enable the privileged **Message Content Intent** for the bot. The bot only requests the normal default intents plus message content.

For each translation source channel, the bot member must be able to:

- View Channel
- Read Message History

The channel webhooks perform destination posting.

## Run locally

```bash
python bot.py
```

A successful startup performs three guards before normal operation:

1. environment/config validation
2. webhook-to-channel validation
3. guild/channel read-access validation

If any required mapping is wrong, startup fails instead of forwarding messages somewhere unexpected.

## Render deployment

Use a **Web Service** if you want Render to require a bound HTTP port.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
python bot.py
```

Set the secrets/environment variables from `.env.example` in the Render dashboard. Do not upload the real `.env` file.

`SELF_PING_ENABLED=true` keeps the previous self-ping behavior when `RENDER_EXTERNAL_URL` exists. Set it to `false` if your hosting tier does not need it.

## Important translation-provider limitation

`deep-translator`'s Google translator uses Google's public/mobile translation endpoint rather than a contracted Google Cloud Translation API. It is free and useful for this private bot, but it has no SLA and can be rate-limited or changed by Google.

The provider logic is isolated in `translator.py`. If reliability becomes more important than zero API cost, replace that module with an official provider such as Google Cloud Translation, DeepL API, or Azure Translator without changing the Discord routing architecture.

Also remember that message text is sent to the selected external translation provider. Do not treat translated channels as an appropriate place for secrets that must never leave Discord/provider infrastructure.

## Safe permission maintenance

### Export current permissions

```bash
python export_permissions.py
```

The export includes guild, category, channel, role/member names, IDs, and allowed/denied overwrites.

### Repair translation channel permissions

Dry-run only:

```bash
python fix_permissions.py
```

Apply only after reviewing the dry-run:

```bash
python fix_permissions.py --apply --confirm OZY_FIX_TRANSLATION_PERMISSIONS
```

The script modifies only:

- `@everyone`
- `OZY Translator`
- that channel's expected language role

It deliberately leaves every unrelated role/member overwrite untouched.

## Safe channel purge

Dry-run two channels:

```bash
python clear_channels.py --channels en es
```

Actually purge them:

```bash
python clear_channels.py --channels en es --apply --confirm OZY_DELETE_TRANSLATIONS
```

Safety controls:

- requires explicit language codes
- verifies `SERVER_ID`
- verifies category
- verifies exact channel ID and channel name
- dry-run by default
- preserves pinned messages by default
- default maximum of 5,000 deletions per channel
- messages older than 14 days are deleted individually because Discord cannot bulk-delete them

Use `--include-pinned` only when you really want pinned messages deleted.

## Tests

The included tests cover token protection, safe message splitting, and persistent message-ID mapping.

```bash
python -m unittest discover -s tests -v
```

## Files

- `bot.py` - Discord client, event ordering, media collection, webhook delivery, edit/delete propagation, health endpoint
- `settings.py` - channel map and strict environment validation
- `translator.py` - Google/deep-translator adapter, timeouts, throttling, retries, cooldown
- `text_utils.py` - protected-token handling and Discord-safe chunking
- `state.py` - SQLite automatic + reaction translation message mapping
- `reaction_utils.py` - flag/language aliases and canonical labels
- `TOPIC_TRANSLATION_SETUP.md` - quick setup for topic/category reaction translation
- `clear_channels.py` - guarded purge utility
- `fix_permissions.py` - guarded permission repair utility
- `export_permissions.py` - permission audit export
- `.env.example` - deployment template without secrets
- `tests/` - local unit tests

## Known limits

- Edits are propagated by deleting the old translated copies and posting new `[Edited]` copies. Discord webhooks cannot move an edited translation back to its original chronological position after it was replaced.
- If the process crashes after Discord accepted a webhook message but before its message ID was committed to SQLite, that one translated copy may not be tracked for later edit/delete cleanup. The window is very small but cannot be made transactionally atomic across Discord and SQLite.
- An individual protected URL/code token longer than Discord's message limit still has to be split.
- Translation quality and availability remain dependent on Google through `deep-translator` until an official translation API is configured.

# Reaction-based translation for topic channels

This build supports a second translation mode for normal topic channels so you do not need ten copies of `#war-room`, `#events`, `#strategy`, and similar channels.

## How it works

A member posts normally in any language. Another member reacts to that source message with a supported flag. The bot translates the source text into that language and posts a silent bot reply directly under the original message.

Examples:

- `🇬🇧` or `🇺🇸` -> English
- `🇪🇸` -> Spanish
- `🇫🇷` -> French
- `🇵🇹` or `🇧🇷` -> Portuguese
- `🇸🇪` -> Swedish
- `🇩🇪` -> German
- `🇵🇭` -> Bisaya
- `🇷🇺` -> Russian
- `🇸🇦` -> Arabic
- `🇳🇴` -> Norwegian

Country aliases intentionally deduplicate to the same target language. For example, `🇬🇧` and `🇺🇸` both request one English translation, never two.

## Recommended setup: one topic category

Create a Discord category such as:

```text
📚 OZY Topics
  #war-room
  #events
  #strategy
  #questions
  #mercenary-exchange
```

Enable Discord Developer Mode, copy the **category ID**, and set:

```env
REACTION_CATEGORY_IDS=123456789012345678
```

Every normal text channel created later inside that category automatically supports flag translation. No bot restart/config edit is needed for each new topic channel as long as it remains in an allowed category.

You can also allow individual channels outside those categories:

```env
REACTION_CHANNEL_IDS=234567890123456789,345678901234567890
```

Both lists may be used together. If both are blank, reaction translation is disabled.

Dedicated `#english`, `#español`, etc. channels are explicitly excluded from reaction mode and continue using automatic 9-language fan-out.

## Anti-spam / anti-clog controls

Reaction translation has its own queue, so a slow requested translation does not block the automatic language-channel queue.

Default protections:

```env
REACTION_QUEUE_SIZE=100
REACTION_MAX_AGE_DAYS=7
REACTION_MAX_TRANSLATIONS_PER_MESSAGE=5
REACTION_MAX_SOURCE_CHARS=4000
REACTION_USER_REQUESTS_PER_MINUTE=15
SILENT_REACTION_TRANSLATIONS=true
```

Behavior:

- one translation maximum per `(source message, target language)`
- duplicate reactions are rejected before entering the queue
- multiple country flags mapping to the same language deduplicate
- maximum 5 different displayed translations per source message by default
- a user may request at most 15 unique translations per minute by default
- queue is bounded; overload drops new reaction jobs instead of consuming unlimited memory
- old messages are ignored after 7 days by default
- only configured channels/categories are eligible
- bot messages and webhook messages are never translated by reaction
- normal reactions such as 👍 ❤️ 😂 are ignored with zero translation API work
- removing a flag does not delete an existing translation
- translated replies use `AllowedMentions.none()` and are silent by default
- all Google calls still pass through the same global concurrency, request-start spacing, retry, timeout, and 429 cooldown controls as automatic translation

## Edits and deletes

If an original topic message is edited, only languages that were already requested are translated again. Existing bot replies are edited **in place** whenever possible, so they do not jump to the bottom of the conversation.

If the translation changes from one chunk to multiple chunks, only the new continuation replies are added. If it becomes shorter, obsolete continuation replies are deleted.

If the original message is deleted, all tracked reaction-generated translations are deleted as well.

Reaction translation mappings are stored in the same SQLite state database. As with automatic edit/delete mappings, Render's ephemeral filesystem means those mappings do not survive a service filesystem reset unless you use persistent storage.

## Topic-channel bot permissions

In reaction-enabled topic channels, the bot role needs:

- View Channel
- Read Message History
- Send Messages
- Send Messages in Threads if you use threads/forum posts

It does not need permission to manage other users' messages. It only deletes/edits its own generated replies.

## Why source language uses auto-detection here

Dedicated language channels have a known source language from their channel. Topic channels do not: Spanish, English, German, etc. can all appear in the same channel. Reaction translation therefore uses source `auto` only for this mode, while the original 10 dedicated channels continue using their known source language.
