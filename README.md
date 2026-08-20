# 🌐 OZY Translator Bot

An automated, multi-language real-time translation bridge for Discord, built with Python (`discord.py`), `deep-translator`, and Discord Webhooks. Designed specifically for cross-language alliance and clan coordination.

---

## 🚀 Features

* **Automatic Real-Time Translation:** Translates incoming messages from any configured channel and broadcasts them to all other target language channels.
* **Webhook Identity Preserved:** Displays translated messages using the original sender's avatar and display name with their source language tag (e.g., `PlayerName (ES)`).
* **Robust Error Handling & Auto-Retry:** Uses exponential backoff (1s, 2s, 4s) to automatically retry failed translations or rate-limited webhook deliveries.
* **Fallback Protection:** If translation service APIs experience temporary downtime, the bot forwards the raw message with a `[Translation temporarily unavailable]` note so no conversation history is ever lost.
* **Media & Attachment Support:** Automatically relays images, screenshots, and file attachments alongside translated text.
* **Non-Blocking Architecture:** Executes blocking translation calls asynchronously via `asyncio.to_thread` to maintain instant message throughput.
* **Render-Ready Health Server:** Includes an embedded, lightweight Flask server to satisfy Render's web service health checks.
* **Infinite Loop Protection:** Automatically ignores messages originating from bots or webhooks.

---

## 🛠️ Supported Languages & Environment Variables

| Language | Code | Webhook Variable |
| :--- | :---: | :--- |
| **English** | `en` | `WEBHOOK_EN` |
| **Spanish** | `es` | `WEBHOOK_ES` |
| **French** | `fr` | `WEBHOOK_FR` |
| **Portuguese** | `pt` | `WEBHOOK_PT` |
| **Swedish** | `sv` | `WEBHOOK_SV` |
| **German** | `de` | `WEBHOOK_DE` |
| **Bisaya / Cebuano** | `ceb` | `WEBHOOK_CEB` |
| **Russian** | `ru` | `WEBHOOK_RU` |
| **Arabic** | `ar` | `WEBHOOK_AR` |
| **Norwegian** | `no` | `WEBHOOK_NO` |

---

## ⚙️ Prerequisites

* **Python 3.10+**
* A **Discord Developer Bot Token** with `Message Content Intent` enabled.
* **Discord Webhook URLs** generated for each corresponding language channel.

---

## 📦 Tech Stack & Dependencies

* [`discord.py`](https://github.com/Rapptz/discord.py) — Discord API Wrapper
* [`deep-translator`](https://github.com/nidhaloff/deep-translator) — Google Translate API Wrapper
* [`aiohttp`](https://docs.aiohttp.org/) — Async HTTP Requests for Webhook Deliveries
* [`flask`](https://flask.palletsprojects.com/) — Web Server for Render Port Binding
* [`python-dotenv`](https://github.com/theskumar/python-dotenv) — Local Environment Variable Management

---

## 🔑 Environment Variables Setup

Create a `.env` file in the root directory (for local testing) or populate these secrets in your hosting platform (e.g., Render Dashboard):

```env
DISCORD_TOKEN=your_discord_bot_token_here
WEBHOOK_EN=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_ES=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_FR=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_PT=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_SV=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_DE=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_CEB=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_RU=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_AR=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...
WEBHOOK_NO=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...