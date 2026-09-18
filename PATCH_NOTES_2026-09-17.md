# Discord Translator hotfix - 2026-09-17

Changes:
- Translation failures now default to `skip` instead of forwarding untranslated originals.
- Google HTTP 429 opens a 30-minute global circuit breaker (`TRANSLATION_429_COOLDOWN_SECONDS=1800`).
- The first Google 429 aborts immediately instead of retrying the same target after a long wait.
- While the Google circuit is open, other target languages are skipped immediately instead of waiting.
- Excessive Discord webhook Retry-After values now quarantine that destination for the actual Retry-After duration, with the configured quarantine as a minimum.
- Failure logs report the actual number of translation attempts.

Render environment values to set:
- `TRANSLATION_429_COOLDOWN_SECONDS=1800`
- `TRANSLATION_FAILURE_MODE=skip`

Validation:
- 20/20 unit tests passing.
- Python compileall passing.
