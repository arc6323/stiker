# Stiker Bot

Telegram bot MVP for creating personal AI sticker packs.

## Current MVP

- /start onboarding
- collects 3–5 user photos
- lets the user choose a visual style
- prepares an order for a 20-sticker pack
- Telegram Stars payment flow scaffold (XTR)
- clear extension point for AI image generation and sticker-set creation

## Environment

Create these environment variables on your host:

- `BOT_TOKEN` — token from @BotFather
- `PACK_PRICE_STARS` — optional, defaults to `399`

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Deployment

The bot uses long polling, so it can run on any always-on Python host or container.

## Next milestones

1. Connect image generation API.
2. Normalize generated files to Telegram sticker requirements.
3. Create sticker sets automatically after successful payment.
4. Add SQLite/PostgreSQL persistence.
5. Add referrals and analytics.
