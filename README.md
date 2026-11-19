# MATRIX MODS Telegram Bot

Premium Telegram utility bot with two tools:

- **Website → ZIP:** downloads publicly accessible pages and static assets from a website the user owns or is authorized to back up.
- **ZIP → Single HTML:** inlines local CSS, JavaScript bundles, and assets into a single HTML file where supported.

It does not recover server-side PHP/Python code, databases, private files, or login-protected content.

## Deploy on Render (GitHub)

1. Create a GitHub repository and upload `bot.py`, `requirements.txt`, `render.yaml`, `.gitignore`, and this README.
2. In Render, choose **New → Blueprint** and connect that GitHub repository.
3. Render reads `render.yaml` and creates a **Background Worker**. This bot uses Telegram long polling; do not deploy it as a normal web service.
4. In the Render service's **Environment** tab, set `BOT_TOKEN` to a freshly generated token from @BotFather.
5. Deploy and check the worker logs for `bot started successfully`.

## Required Telegram setup for force-join

1. Add the bot as an **administrator** in your channel.
2. Keep permission to view members / manage channel enabled as required by Telegram's channel setup. The bot must be able to call `getChatMember`.
3. Confirm `CHANNEL_ID` is `-1004473792888` and `CHANNEL_LINK` is your invite link.
4. Users must join the channel and press **I HAVE JOINED — VERIFY**. Access to URL processing and ZIP conversion is checked again server-side.

If membership checks fail, the bot denies access rather than bypassing the gate. Check the Render logs and bot admin status.

## Environment variables

| Variable | Purpose |
|---|---|
| `BOT_TOKEN` | Telegram bot token (secret; set only in Render Environment) |
| `OWNER_ID` | Telegram numeric owner ID |
| `CHANNEL_ID` | Channel numeric ID used for membership checks |
| `CHANNEL_LINK` | Public/invite link shown in the join button |

Never commit `.env` or a bot token to GitHub. If a token has ever been pasted into source code or a public/private repository, revoke it with @BotFather and use a new token.

## Important limits

The bot uses temporary local storage while processing. Render worker ephemeral storage is sufficient for temporary jobs; generated archives are sent to Telegram and then removed. The process is not a persistent file host.

Use the tools only on websites/files you own or have permission to process.
