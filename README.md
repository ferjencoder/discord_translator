# OZY Discord Translator — local Argos

Messages in the seven active language channels are translated to the other six
and delivered through the existing Discord webhooks, using the author's name/avatar.
Active languages: EN, ES, AR, DE, FR, NO, PT. Cebuano, Swedish and Russian are disabled;
their saved channel IDs remain reserved so they cannot accidentally become reaction topics.
No channels or roles are deleted.

## Translation

- Argos Translate 1.11.0 runs locally on CPU; no Google/deep-translator adapter or translation API key.
- Twelve directional models: English to/from Spanish, Arabic, German, French, Norwegian and Portuguese.
  Discord code `no` maps to Argos `nb`. Other pairs run sequentially through English.
- One inference at a time, int8 compute, one thread and beam/batch size 1.
  Low-memory mode unloads each neural model after its leg, before loading the next.
  This reduces retained models but does not guarantee a particular process RAM ceiling.
- Protected code, URLs, mentions, emoji and game coordinates bypass the neural model.
  Translating surrounding fragments separately can reduce fluency.
- Failed translations are skipped. Originals are never reposted as a failure fallback.
  An unsuccessful edit leaves the last successfully delivered copy unchanged.
- A task timeout includes time waiting for the translation slot. Native inference cannot be killed
  by asyncio: while an expired/cancelled call is still running, new requests are skipped.
  If it never returns, restart the process.
- Reaction sources use local `langdetect`; unsupported or low-confidence detections are skipped.
  Short, mixed-language or slang messages can still be confidently misidentified.

## Preserved Discord behavior

Existing guild/channel/webhook validation, author attribution, mention suppression, queues,
attachment forwarding, reply context, chunking, edit/delete synchronization, deduplication,
Discord delivery rate limits/retries/quarantine and SQLite mappings remain in place.
Enable Message Content Intent in the Discord Developer Portal. The bot needs View Channel and
Read Message History in source channels; webhooks post the automatic translations.

Topic reaction replies additionally need Send Messages and Send Messages in Threads.
Configure `REACTION_CATEGORY_IDS` or `REACTION_CHANNEL_IDS`; see
[topic setup](TOPIC_TRANSLATION_SETUP.md). Flags target only active languages.
UK/US and Portugal/Brazil flags share their respective languages.
Existing per-user limits, age limits and one-reply-per-language deduplication remain.

`/translator-status` reports local provider activity, queue sizes, success/failure,
latency, character counts and Discord delivery status. Retained telemetry can include
earlier Google failures; no cloud cost projection is used.

## Local setup

Use Python 3.12 and a virtual environment:

```text
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `DISCORD_TOKEN`, `SERVER_ID` and
`WEBHOOK_EN`, `WEBHOOK_ES`, `WEBHOOK_AR`, `WEBHOOK_DE`, `WEBHOOK_FR`,
`WEBHOOK_NO`, `WEBHOOK_PT`. Keep tokens private.

```text
python install_argos_models.py
python bot.py
```

The installer does not require Discord credentials. It installs only missing required pairs,
verifies all pairs exist and warms sentence splitters so their downloads occur at build time.
It does not remove unrelated models already on disk. Runtime uses only explicit required pairs.
Model downloads require internet and considerable disk space.

## Render

Build command:

```bash
pip install --index-url https://download.pytorch.org/whl/cpu torch && pip install -r requirements.txt && python install_argos_models.py
```

Start command:

```bash
python bot.py
```

The first build step selects the official CPU-only PyTorch distribution before installing
Argos dependencies, avoiding unnecessary CUDA libraries on Render.

Use these environment variables (also in `RENDER_TRANSLATION_SETTINGS.txt`):

```env
PYTHON_VERSION=3.12.10
ACTIVE_TRANSLATION_LANGS=en,es,ar,de,fr,no,pt
ARGOS_DEVICE_TYPE=cpu
ARGOS_COMPUTE_TYPE=int8
ARGOS_INTER_THREADS=1
ARGOS_INTRA_THREADS=1
ARGOS_BATCH_SIZE=1
ARGOS_BEAM_SIZE=1
ARGOS_CHUNK_TYPE=MINISBD
ARGOS_LOW_MEMORY=true
TRANSLATION_CONCURRENCY=1
TRANSLATION_START_INTERVAL_SECONDS=0.05
TRANSLATION_RETRIES=1
TRANSLATION_TASK_TIMEOUT_SECONDS=90
TRANSLATION_FAILURE_MODE=skip
```

Keep existing Discord identity, active webhooks, delivery, reaction and state settings.
Remove old `TRANSLATION_CONNECT_TIMEOUT_SECONDS`, `TRANSLATION_READ_TIMEOUT_SECONDS`,
`TRANSLATION_429_COOLDOWN_SECONDS`, `TRANSLATION_FALLBACK_DELAY_SECONDS`, and `GOOGLE_CLOUD_*`.
Discord startup and webhook 429 settings still apply.

Models default to project-local `data/argos/packages`; sentence splitters/cache/config
also live under `data/argos`. Build and runtime must use identical asset paths and active
languages. Do not set different `ARGOS_PACKAGES_DIR` or `XDG_*` overrides between them.
Rebuild if changing languages. Only subsets of these seven languages (including EN) are accepted.

The existing health server listens on `0.0.0.0:$PORT` (default 10000).
The existing optional self-ping is retained but is not an uptime guarantee.
Render free services can sleep/restart, and runtime SQLite mappings can be lost with their
ephemeral filesystem. See [Render free-service limits](https://render.com/docs/free).
Do not upgrade a hosting plan without assessing the cost.

CPU latency, memory use, build size and translation quality need a real Render smoke test.
Argos dependencies include heavyweight ML libraries even with MiniSBD selected.
Unloading models does not mean all memory is returned to the OS. Test chat bursts, two-leg
translations, reaction detection, edits and restarts on the actual service.

Local verification on 2026-09-20: 40 unit tests passed, compile/dependency checks passed,
and 20 synthetic translations completed with socket connections blocked after loop startup.
All twelve directions, English pivots, one auto-detected reaction and protected text were
exercised. Windows Python 3.12 peak working set reached 688 MiB; final working set was
365 MiB and private committed memory reached about 1,478 MiB. These are desktop measurements,
not Linux/Render measurements, and do not demonstrate that a 512 MB service is viable.
Installed model/splitter assets occupied about 1.33 GiB, excluding Python dependencies.
No live Discord messages were sent and no Render deployment was performed.

## Verification

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
pip check
```

Unit tests mock neural output; they validate routing/configuration, model selection and cleanup,
timeout/cancellation isolation, protected text, detection rejection, failure delivery,
reaction flags, message state and Discord startup rate-limit handling.
They do not establish model quality or Render resource suitability.

Provider implementation: `translator.py`. Shared paths/languages: `argos_config.py`.
Build installer: `install_argos_models.py`. Routing/delivery: `bot.py`.
Historical hardening/patch reports describe earlier releases; this README and the Render
settings file are the current deployment instructions.
