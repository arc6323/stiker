from __future__ import annotations

import http.server
import logging
import os

from telegram.ext import Application


class _NoopHTTPServer:
    """Prevent bot.py's old health server from binding the same Render port."""

    def __init__(self, *args, **kwargs):
        pass

    def serve_forever(self):
        return


# bot.py imports ThreadingHTTPServer after Python loads sitecustomize.
# Replacing it here avoids a port conflict with the Telegram webhook server.
http.server.ThreadingHTTPServer = _NoopHTTPServer

_original_run_polling = Application.run_polling


def _run_polling_as_webhook(self, *args, **kwargs):
    port = int(os.getenv("PORT", "10000"))
    base_url = os.getenv("WEBHOOK_BASE_URL", "https://stickerme-ai.onrender.com").rstrip("/")
    path = os.getenv("WEBHOOK_PATH", "telegram").strip("/") or "telegram"
    webhook_url = f"{base_url}/{path}"

    logging.getLogger("stiker-bot").info("Starting Telegram webhook at %s", webhook_url)
    return self.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=webhook_url,
        allowed_updates=kwargs.get("allowed_updates"),
        drop_pending_updates=False,
    )


Application.run_polling = _run_polling_as_webhook
