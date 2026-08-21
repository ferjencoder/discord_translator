# OZY Discord Translator - Hardening Report

## Scope

Reviewed and rebuilt the translation bot, permission repair utility, channel purge utility, permission exporter, configuration handling, translation layer, media relay, message lifecycle, and deployment setup.

## Fixed - high severity

- **Wrong-destination data leak:** every webhook is fetched and its actual Discord channel/guild is checked before the bot becomes operational.
- **Permission overwrite escalation:** `fix_permissions.py` no longer modifies arbitrary existing role/member overwrites. It only touches `@everyone`, `OZY Translator`, and the expected language role for each guarded channel.
- **Destructive purge risk:** `clear_channels.py` is dry-run by default and requires explicit language targets, exact guild/category/channel guards, an apply flag, and a confirmation phrase.
- **Conversation reordering:** source create/edit/delete events are processed through one FIFO queue. The next source event waits for the current event's destination deliveries to complete.
- **Silent translation failure:** failed translations are visibly marked and include the original text.

## Fixed - medium severity

- Disabled processed mentions in translated webhook posts with `AllowedMentions.none()`.
- Replaced manual webhook HTTP code and private `discord.utils._to_json` use with `discord.Webhook`.
- Added webhook message-ID tracking for delete/edit propagation.
- Added explicit Google connect/read timeouts around `deep-translator` 1.11.4.
- Added configurable translation concurrency plus a real HTTP-start rate gate.
- Added global cooldown after detected Google 429 responses.
- Protected URLs, code, Discord mentions, timestamps, slash commands, custom emoji, broadcast mentions, and Total Battle coordinates from translation.
- Replaced hard 1900-character slicing with boundary-aware chunking.
- Re-uploaded normal attachments within configured size limits instead of relying only on source CDN URLs.
- Corrected sticker behavior: raster/GIF formats are relayed; Lottie falls back to a label.
- Removed Flask, Gunicorn, import-time server threads, and duplicate reconnect keepalive tasks.
- Added strict configuration parsing and fully pinned runtime dependencies.

## Remaining design risks

### Translation provider

The Google backend used by `deep-translator` is an unofficial/public endpoint with no SLA. The bot now contains failures, timeouts, and rate limits, but it cannot make that provider contractually reliable. An official Google Cloud, DeepL, or Azure API is the next upgrade if translation becomes mission-critical.

### Privacy

Message text is transmitted to the external translation provider. Do not use these channels for information that must remain exclusively inside Discord/OZY infrastructure.

### Render local state

The SQLite map contains Discord message IDs only and is useful on a persistent filesystem. Render free web services use ephemeral local storage, so mappings disappear on service restart/redeploy/spin-down. Translation itself continues, but edits/deletes of messages translated before that reset cannot be correlated automatically afterward.

### Distributed atomicity

Discord accepting a webhook message and SQLite recording its ID cannot be one atomic transaction. A crash in the tiny gap between those operations can leave one translated copy untracked. Eliminating that edge case would require a durable job/state backend and idempotency/reconciliation strategy.

## Validation performed

- Python compilation of all project modules.
- Unit tests for protected token round-trips.
- Unit tests for Discord-safe chunking.
- Unit tests for persistent message map replace/get/delete behavior.
- Configuration smoke test for all 10 channel mappings.
- Static secret scan: no live Discord bot token or webhook token found in the hardened package.
- Static scan confirms the old manual webhook serializer, Flask server, and broad permission-overwrite mutation are removed.

## Deployment recommendation

Deploy this version before adding more features. Run `fix_permissions.py` and `clear_channels.py` in dry-run mode first. Keep the old service/environment variables available for rollback until live translation, media relay, edit/delete behavior, and all 10 webhook destinations have been tested in Discord.

## Reaction-based topic translation added

A second translation mode now handles normal topic channels via flag reactions without creating language-specific copies of every channel.

Security/reliability controls added:

- category-ID and channel-ID allowlists; disabled unless configured
- dedicated language channels explicitly excluded from reaction mode
- separate bounded reaction queue so topic requests cannot block automatic fan-out
- in-memory pending-key suppression before queue insertion
- persistent `(source_message_id, target_lang)` deduplication in SQLite
- alias flags deduplicate (`🇬🇧`/`🇺🇸`, `🇵🇹`/`🇧🇷`)
- per-user sliding-window request limit (default 15/minute)
- per-message language cap (default 5)
- source-message age limit (default 7 days)
- bot/webhook source messages ignored
- only known flag reactions invoke the provider
- reaction replies are silent by default and use `AllowedMentions.none()`
- reaction translation uses the existing provider semaphore, 200 ms request-start gate, retry policy, timeouts, and global 429 cooldown
- requested translations update on source edits and are deleted with the source
- edit refresh uses in-place bot-message edits whenever chunk structure permits
- reaction state stores message IDs/language metadata, not source or translated chat text

This architecture makes reaction translation lighter than the automatic fan-out path for normal use: a source message creates zero translations until someone explicitly requests one.
