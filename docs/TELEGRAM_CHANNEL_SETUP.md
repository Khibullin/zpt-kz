# ZPT.KZ — Telegram: first stage

Channel: https://t.me/zpt_kz_zakaz
Bot: https://t.me/zpt_kz_zakaz_bot

## Scope

This first stage is verification-only by default. It does NOT:
- automatically publish buyer requests;
- configure a webhook or scheduled task;
- change WhatsApp or request dispatch;
- touch production buyer records or send personal data.

The bot is already a channel administrator with Post Messages permission.
Only one environment secret is needed: TELEGRAM_BOT_TOKEN in Render's
market-parts / zpt-kz-backend service. Never commit or paste its value.

## Read-only verification

Run in the Render Shell after deployment:

    python manage.py check_zpt_telegram

This checks the token using getMe, the exact target channel using getChat,
and the bot's Post Messages permission using getChatMember. It does not
publish anything or write to the database.

## Explicit one-time test

Only after approving one public technical test:

    python manage.py check_zpt_telegram --send-test

This sends a fixed test message to @zpt_kz_zakaz, without any buyer data.
Repeated invocation sends another test post.

## Next phase: real requests (NOT IMPLEMENTED)

Before publishing a buyer request to a PUBLIC Telegram channel:
- obtain buyer consent for publication of an anonymized request;
- have an administrator review the final text, removing buyer phone,
  name, VIN, address, buyer access tokens and incidental identifiers
  from free-form input;
- record publication status, Telegram message_id and prevent duplicates;
- use deliberate admin approval first; automate only after separate approval.

No production Request object is processed in this stage.
