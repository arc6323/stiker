from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputSticker, LabeledPrice, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters

from ai_generator import OpenAIImageService, StickerIdea
from sticker_utils import prepare_static_sticker, sanitize_pack_name

load_dotenv()
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("stiker-bot")

MIN_PHOTOS: Final = 3
MAX_PHOTOS: Final = 5
PREVIEW_COUNT: Final = 2
FULL_PACK_COUNT: Final = 20
DATA_DIR = Path(os.getenv("DATA_DIR", "data")).resolve()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PACK_PRICE_STARS = int(os.getenv("PACK_PRICE_STARS", "399"))
ENABLE_PAYMENTS = os.getenv("ENABLE_PAYMENTS", "false").lower() in {"1", "true", "yes", "on"}
ALLOW_FREE_PACKS = os.getenv("ALLOW_FREE_PACKS", "true").lower() in {"1", "true", "yes", "on"}
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH_STICKER_SET", "true").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Style:
    code: str
    title: str
    emoji: str
    description: str


STYLES: Final = (
    Style("cartoon3d", "3D Cartoon", "🧸", "3D cartoon render, soft volumes, friendly expressive features"),
    Style("anime", "Anime", "🌸", "clean anime shading, vibrant look, charming facial expressions"),
    Style("realistic", "Realistic", "📸", "semi-realistic sticker art with strong likeness"),
    Style("meme", "Meme", "😂", "memey reaction sticker, exaggerated emotion, fun energy"),
    Style("gangster", "Gangster", "😎", "bold swagger, cool vibe, street-style attitude"),
    Style("romantic", "Romantic", "❤️", "soft warm mood, affectionate and cute expression"),
)
STYLE_BY_CODE = {s.code: s for s in STYLES}

IDEAS: Final = (
    StickerIdea("👋", "hello", "greets warmly with a hand wave"),
    StickerIdea("😂", "laugh", "laughs hard with joyful energy"),
    StickerIdea("😎", "cool", "poses confidently"),
    StickerIdea("👍", "thumbs_up", "gives an approving thumbs up"),
    StickerIdea("🙏", "thanks", "shows sincere gratitude"),
    StickerIdea("🔥", "fire", "reacts like something is awesome"),
    StickerIdea("❤️", "love", "sends love with a heart gesture"),
    StickerIdea("🤯", "shocked", "is shocked and amazed"),
    StickerIdea("😴", "sleepy", "is sleepy and ready for bed"),
    StickerIdea("🤔", "thinking", "thinks with a puzzled face"),
    StickerIdea("😡", "angry", "is funny-annoyed and expressive"),
    StickerIdea("🥳", "party", "celebrates enthusiastically"),
    StickerIdea("😢", "sad", "is sad and emotional"),
    StickerIdea("💸", "money", "shows success and money playfully"),
    StickerIdea("💪", "strong", "flexes confidently"),
    StickerIdea("🤝", "deal", "agrees and seals a deal"),
    StickerIdea("🤦", "facepalm", "reacts with a facepalm"),
    StickerIdea("✅", "done", "shows everything is done"),
    StickerIdea("🎉", "congrats", "congratulates happily"),
    StickerIdea("👀", "watching", "watches carefully with expressive eyes"),
)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"StickerMe Ai OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    logger.info("Health server listening on port %s", port)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Создать стикеры", callback_data="new")],
        [InlineKeyboardButton("🎨 Стили", callback_data="styles")],
        [InlineKeyboardButton("ℹ️ Как это работает", callback_data="how")],
    ])


def styles_menu():
    rows = [[InlineKeyboardButton(f"{s.emoji} {s.title}", callback_data=f"style:{s.code}")] for s in STYLES]
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def ready_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Выбрать стиль", callback_data="styles")],
        [InlineKeyboardButton("🔄 Начать заново", callback_data="new")],
    ])


def order_menu():
    label = f"⭐ Оплатить {PACK_PRICE_STARS} Stars и собрать пак" if ENABLE_PAYMENTS else "🚀 Собрать полный пак"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Сгенерировать 2 превью", callback_data="preview")],
        [InlineKeyboardButton(label, callback_data="buy")],
        [InlineKeyboardButton("🎨 Другой стиль", callback_data="styles")],
    ])


def dirs(user_id: int):
    base = DATA_DIR / str(user_id)
    result = {"source": base / "source", "generated": base / "generated", "prepared": base / "prepared"}
    for path in result.values():
        path.mkdir(parents=True, exist_ok=True)
    return result


def reset(context, user_id: int | None = None):
    for path in context.user_data.get("photo_paths", []):
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
    context.user_data.clear()
    context.user_data.update(stage="collecting", photos=[], photo_paths=[], job_running=False)
    if user_id:
        dirs(user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    reset(context, update.effective_user.id)
    await update.message.reply_text(
        "👋 <b>StickerMe Ai</b>\n\nОтправь 3–5 фото, выбери стиль — и я создам персональный стикерпак.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def new_cmd(update, context):
    if not update.effective_user:
        return
    reset(context, update.effective_user.id)
    if update.message:
        await update.message.reply_text(
            "📷 Пришли <b>3–5 фото</b>: лицо хорошо видно, разные ракурсы, без сильных фильтров.",
            parse_mode=ParseMode.HTML,
        )


async def status(update, context):
    if not update.message:
        return
    style = STYLE_BY_CODE.get(context.user_data.get("style"))
    count = len(context.user_data.get("photos", []))
    await update.message.reply_text(
        f"📦 Фото: {count}/{MAX_PHOTOS}\n"
        f"Стиль: {style.emoji + ' ' + style.title if style else 'не выбран'}\n"
        f"Превью: {'готовы' if context.user_data.get('preview_stickers') else 'нет'}\n"
        f"Пак: {'готов' if context.user_data.get('pack_url') else 'нет'}"
    )


async def cancel(update, context):
    if update.effective_user:
        reset(context, update.effective_user.id)
    if update.message:
        await update.message.reply_text("Заказ сброшен.", reply_markup=main_menu())


async def on_photo(update, context):
    if not update.message or not update.message.photo or not update.effective_user:
        return
    if context.user_data.get("stage") != "collecting":
        await update.message.reply_text("Сначала нажми «Создать стикеры» или /new.")
        return

    photos = context.user_data.setdefault("photos", [])
    paths = context.user_data.setdefault("photo_paths", [])
    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text("Уже 5 фото. Выбирай стиль.", reply_markup=ready_menu())
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    path = dirs(update.effective_user.id)["source"] / f"source_{len(photos)+1}_{uuid.uuid4().hex[:6]}.jpg"
    await file.download_to_drive(str(path))
    photos.append(photo.file_id)
    paths.append(str(path))

    count = len(photos)
    if count < MIN_PHOTOS:
        await update.message.reply_text(f"✅ Фото {count}/3 принято. Нужны ещё {MIN_PHOTOS-count}.")
    else:
        await update.message.reply_text(
            f"✅ Загружено {count} фото. Можно добавить ещё или выбрать стиль.",
            reply_markup=ready_menu(),
        )


async def callback(update, context):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""

    if data == "menu":
        await query.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return
    if data == "new":
        if update.effective_user:
            reset(context, update.effective_user.id)
        await query.edit_message_text("📷 Пришли 3–5 фотографий. Лучше разные ракурсы и без сильных фильтров.")
        return
    if data == "how":
        await query.edit_message_text(
            "1️⃣ Загружаешь 3–5 фото\n2️⃣ Выбираешь стиль\n3️⃣ Получаешь 2 AI-превью\n4️⃣ Собираешь полный набор\n5️⃣ Бот публикует готовый Telegram-стикерпак",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="menu")]]),
        )
        return
    if data == "styles":
        await query.edit_message_text("🎨 <b>Выбери стиль</b>", parse_mode=ParseMode.HTML, reply_markup=styles_menu())
        return
    if data.startswith("style:"):
        style = STYLE_BY_CODE.get(data.split(":", 1)[1])
        if not style:
            return
        context.user_data["style"] = style.code
        if len(context.user_data.get("photos", [])) < MIN_PHOTOS:
            await query.edit_message_text(f"{style.emoji} {style.title} выбран. Теперь загрузи минимум 3 фото через /new.")
            return
        context.user_data["stage"] = "ready"
        await query.edit_message_text(
            f"{style.emoji} <b>{style.title}</b> выбран.\n\nПолный набор: 20 стикеров\nЦена: {PACK_PRICE_STARS} ⭐",
            parse_mode=ParseMode.HTML,
            reply_markup=order_menu(),
        )
        return
    if data == "preview":
        await generate(update, context, True)
        return
    if data == "buy":
        if ENABLE_PAYMENTS:
            await context.bot.send_invoice(
                chat_id=query.message.chat_id,
                title="20 персональных AI-стикеров",
                description="StickerMe Ai персональный набор",
                payload=f"pack:{query.from_user.id}:{context.user_data.get('style')}",
                currency="XTR",
                prices=[LabeledPrice("Стикерпак", PACK_PRICE_STARS)],
            )
            return
        if ALLOW_FREE_PACKS:
            await generate(update, context, False)
        else:
            await query.message.reply_text("Бесплатный режим выключен.")


async def generate(update, context, preview_only: bool):
    if context.user_data.get("job_running"):
        await update.effective_message.reply_text("⏳ Генерация уже идёт.")
        return
    if not update.effective_user:
        return

    paths = [Path(x) for x in context.user_data.get("photo_paths", [])]
    style = STYLE_BY_CODE.get(context.user_data.get("style"))
    if len(paths) < MIN_PHOTOS or not style:
        await update.effective_message.reply_text("Нужны минимум 3 фото и выбранный стиль.")
        return

    context.user_data["job_running"] = True
    msg = await update.effective_message.reply_text("🧠 Анализирую внешность и создаю стикеры. Это может занять несколько минут...")
    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        result = await asyncio.to_thread(build_assets, update.effective_user.id, paths, style, preview_only)
        context.user_data["preview_stickers"] = [str(x) for x in result["preview"]]

        if preview_only:
            await msg.edit_text("✅ Превью готовы.")
            for path in result["preview"]:
                with path.open("rb") as fh:
                    await context.bot.send_photo(update.effective_chat.id, fh)
            await update.effective_message.reply_text("Если нравится — собирай полный пак.", reply_markup=order_menu())
            return

        await msg.edit_text("✅ Стикеры готовы. Публикую набор...")
        url = await publish(context, update.effective_user.id, style, result["all"]) if AUTO_PUBLISH else None
        if url:
            context.user_data["pack_url"] = url
            await msg.edit_text(f"🎉 Готово! Добавить стикерпак:\n{url}")
        else:
            await msg.edit_text("✅ Генерация завершена.")
    except Exception as exc:
        logger.exception("generation failed")
        await msg.edit_text(f"❌ Ошибка: {exc}")
    finally:
        context.user_data["job_running"] = False


def build_assets(user_id: int, photo_paths: list[Path], style: Style, preview_only: bool):
    user_dirs = dirs(user_id)
    ideas = IDEAS[:PREVIEW_COUNT] if preview_only else IDEAS[:FULL_PACK_COUNT]
    identity, raw = OpenAIImageService().generate_set(photo_paths, style.title, style.description, ideas, user_dirs["generated"])
    prepared = []
    for i, path in enumerate(raw, 1):
        prepared.append(prepare_static_sticker(path, user_dirs["prepared"] / f"sticker_{i:02d}.png"))
    return {"identity": identity, "preview": prepared[:2], "all": prepared}


async def publish(context, user_id: int, style: Style, paths: list[Path]):
    me = await context.bot.get_me()
    if not me.username:
        raise RuntimeError("У бота нет username")

    base = sanitize_pack_name(f"u{user_id}_{style.code}_{uuid.uuid4().hex[:8]}")
    name = re.sub(r"__+", "_", f"{base}_by_{me.username}")
    handles = [path.open("rb") for path in paths]
    try:
        stickers = [
            InputSticker(sticker=handle, emoji_list=[idea.emoji], format="static")
            for handle, idea in zip(handles, IDEAS)
        ]
        await context.bot.create_new_sticker_set(
            user_id=user_id,
            name=name,
            title=f"{style.emoji} {style.title} pack",
            stickers=stickers,
            sticker_type="regular",
        )
    finally:
        for handle in handles:
            handle.close()
    return f"https://t.me/addstickers/{name}"


async def precheckout(update, context):
    query = update.pre_checkout_query
    if not query:
        return
    ok = query.invoice_payload.startswith("pack:")
    await query.answer(ok=ok, error_message=None if ok else "Неизвестный заказ")


async def paid(update, context):
    if not update.message or not update.message.successful_payment:
        return
    context.user_data["payment_charge_id"] = update.message.successful_payment.telegram_payment_charge_id
    await update.message.reply_text("✅ Оплата получена. Запускаю генерацию...")
    await generate(update, context, False)


async def text(update, context):
    if update.message:
        await update.message.reply_text("Используй кнопки меню или /new.", reply_markup=main_menu())


async def error_handler(update, context):
    logger.exception("Unhandled exception", exc_info=context.error)


def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    app.add_error_handler(error_handler)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    public_url = os.getenv("RENDER_EXTERNAL_URL", "https://stickerme-ai.onrender.com").rstrip("/")
    webhook_path = "telegram"
    webhook_url = f"{public_url}/{webhook_path}"
    logger.info("Starting webhook on port %s at %s", port, webhook_url)
    build_application().run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )