# OZY Topic Translation Setup

## Recommended Discord layout

Create one category for topic channels, for example:

```text
📚 OZY Topics
  #war-room
  #events
  #strategy
  #questions
  #mercenary-exchange
```

Do not create ten language copies of these channels.

## 1. Copy the category ID

Discord -> User Settings -> Advanced -> Developer Mode -> On.

Right-click the topic category -> Copy Category ID.

## 2. Add it to Render

Add an environment variable:

```text
REACTION_CATEGORY_IDS=<your category id>
```

For more than one category:

```text
REACTION_CATEGORY_IDS=111111111111111111,222222222222222222
```

Optional individual channel IDs:

```text
REACTION_CHANNEL_IDS=333333333333333333
```

## 3. Bot permissions for that category

Give the bot role:

- View Channel
- Read Message History
- Send Messages
- Send Messages in Threads

## 4. Usage

Member writes:

```text
Necesitamos atacar después del reset.
```

Another member reacts `🇬🇧`.

Bot replies to that exact source message:

```text
🇬🇧 English
We need to attack after reset.
```

If ten people also react `🇬🇧`, no additional English message is created.

If somebody reacts `🇩🇪`, one German reply is added.

## Supported flags

| Flag | Target |
|---|---|
| 🇬🇧 / 🇺🇸 | English |
| 🇪🇸 | Spanish |
| 🇫🇷 | French |
| 🇵🇹 / 🇧🇷 | Portuguese |
| 🇸🇪 | Swedish |
| 🇩🇪 | German |
| 🇵🇭 | Bisaya |
| 🇷🇺 | Russian |
| 🇸🇦 | Arabic |
| 🇳🇴 | Norwegian |

## Default safety limits

- 5 different requested languages per source message
- 15 unique requests per member per minute
- source message must be no older than 7 days
- reaction queue holds at most 100 pending jobs
- translations are silent and cannot generate mention pings
- duplicate requests never reach Google

Change the limits in Render only if actual usage justifies it.
