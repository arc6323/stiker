from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputSticker, LabeledPrice, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from ai_generator import AIServiceError, OpenAIImageService, StickerIdea
from state_store import OrderState, OrderStore
from sticker_utils import prepare_static_sticker, sanitize_pack_name

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("stiker-bot")

MIN_PHOTOS: Final = 3
MAX_PHOTOS: Final = 5
PREVIEW_COUNT: Final = 2
FULL_PACK_COUNT: Final = 20
DATA_DIR: Final = Path(os.getenv("DATA_DIR", "data")).resolve()
BOT_TOKEN: Final = os.getenv("BOT_TOKEN", "").strip()
PACK_PRICE_STARS: Final = int(os.getenv("PACK_PRICE_STARS", "399"))
ENABLE_PAYMENTS: Final = os.getenv("ENABLE_PAYMENTS", "false").lower() in {"1", "true", "yes", "on"}
ALLOW_FREE_PACKS: Final = os.getenv("ALLOW_FREE_PACKS", "true").lower() in {"1", "true", "yes", "on"}
AUTO_PUBLISH: Final = os.getenv("AUTO_PUBLISH_STICKER_SET", "true").lower() in {"1", "true", "yes", "on"}

_STORE: OrderStore | None = None
RUNNING_JOBS: set[int] = set()


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
STYLE_BY_CODE = {style.code: style for style in STYLES}

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


def get_store() -> OrderStore:
    global _STORE
    if _STORE is None:
        _STORE = OrderStore()
        _STORE.ensure_schema()
    return _STORE


async def db_get(user_id: int) -> OrderState:
    return await asyncio.to_thread(get_store().get, user_id)


async def db_reset(user_id: int) -> OrderState:
    return await asyncio.to_thread(get_store().reset, user_id)


async def db_add_photo(user_id: int, file_id: str) -> OrderState:
    return await asyncio.to_thread(get_store().add_photo, user_id, file_id, MAX_PHOTOS)


async def db_set_style(user_id: int, style: str, stage: str = "ready") -> OrderState:
    return await asyncio.to_thread(get_store().set_style, user_id, style, stage)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✨ Создать стикеры", callback_data="new")],
            [InlineKeyboardButton("📦 Текущий заказ", callback_data="status")],
            [InlineKeyboardButton("🎨 Стили", callback_data="styles")],
            [InlineKeyboardButton("ℹ️ Как это работает", callback_data="how")],
        ]
    )


def styles_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{style.emoji} {style.title}", callback_data=f"style:{style.code}")]
        for style in STYLES
    ]
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def ready_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎨 Выбрать стиль", callback_data="styles")],
            [InlineKeyboardButton("🔄 Начать заново", callback_data="new")],
        ]
    )


def order_menu() -> InlineKeyboardMarkup:
    label = (
        f"⭐ Оплатить {PACK_PRICE_STARS} Stars и собрать пак"
        if ENABLE_PAYMENTS
        else "🚀 Собрать полный пак"
    )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🖼 Сгенерировать 2 превью", callback_data="preview")],
            [InlineKeyboardButton(label, callback_data="buy")],
            [InlineKeyboardButton("🎨 Другой стиль", callback_data="styles")],
            [InlineKeyboardButton("🔄 Новый заказ", callback_data="new")],
        ]
    )


def user_dirs(user_id: int) -> dict[str, Path]:
    base = DATA_DIR / str(user_id)
    result = {
        "base": base,
        "source": base / "source",
        "generated": base / "generated",
        "prepared": base / "prepared",
    }
    for path in result.values():
        path.mkdir(parents=True, exist_ok=True)
    return result


def clear_ephemeral_user_files(user_id: int) -> None:
    base = DATA_DIR / str(user_id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)


def state_text(state: OrderState) -> str:
    style = STYLE_BY_CODE.get(state.style or "")
    return (
        "📦 <b>Текущий заказ</b>\n\n"
        f"Фото: <b>{len(state.photo_file_ids)}/{MAX_PHOTOS}</b>\n"
        f"Стиль: <b>{style.emoji + ' ' + style.title if style else 'не выбран'}</b>\n"
        f"Статус: <b>{state.stage}</b>\n"
        f"Пак: <b>{'готов' if state.pack_url else 'ещё нет'}</b>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    state = await db_get(update.effective_user.id)
    text = (
        "👋 <b>StickerMe Ai</b>\n\n"
        "Я создаю персональные Telegram-стикеры из твоих фотографий.\n\n"
        "Важно: существующий заказ сохраняется даже после перезапуска сервера."
    )
    if state.photo_file_ids or state.style or state.pack_url:
        text += "\n\n" + state_text(state)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu())


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    await db_reset(update.effective_user.id)
    clear_ephemeral_user_files(update.effective_user.id)
    if update.message:
        await update.message.reply_text(
            "📷 Пришли <b>3–5 фото</b>: лицо хорошо видно, разные ракурсы, без сильных фильтров.",
            parse_mode=ParseMode.HTML,
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    state = await db_get(update.effective_user.id)
    await update.message.reply_text(state_text(state), parse_mode=ParseMode.HTML)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    await db_reset(update.effective_user.id)
    clear_ephemeral_user_files(update.effective_user.id)
    if update.message:
        await update.message.reply_text("Заказ сброшен.", reply_markup=main_menu())


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo or not update.effective_user:
        return

    user_id = update.effective_user.id
    state = await db_get(user_id)
    if state.stage not in {"collecting", "ready"}:
        await update.message.reply_text("Сначала начни новый заказ: /new")
        return

    if len(state.photo_file_ids) >= MAX_PHOTOS:
        await update.message.reply_text("Уже загружено 5 фото. Выбирай стиль.", reply_markup=ready_menu())
        return

    file_id = update.message.photo[-1].file_id
    state = await db_add_photo(user_id, file_id)

    if len(state.photo_file_ids) >= MIN_PHOTOS and state.style:
        state = await db_set_style(user_id, state.style, "ready")

    count = len(state.photo_file_ids)
    if count < MIN_PHOTOS:
        await update.message.reply_text(f"✅ Фото {count}/{MIN_PHOTOS} принято. Нужны ещё {MIN_PHOTOS - count}.")
        return

    if state.style:
        style = STYLE_BY_CODE.get(state.style)
        await update.message.reply_text(
            f"✅ Загружено {count} фото. Стиль {style.emoji if style else ''} {style.title if style else state.style} уже выбран.",
            reply_markup=order_menu(),
        )
    else:
        await update.message.reply_text(
            f"✅ Загружено {count} фото. Можно добавить ещё или выбрать стиль.",
            reply_markup=ready_menu(),
        )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id

    if data == "menu":
        await query.edit_message_text("Выбери действие:", reply_markup=main_menu())
        return

    if data == "status":
        state = await db_get(user_id)
        await query.edit_message_text(state_text(state), parse_mode=ParseMode.HTML, reply_markup=main_menu())
        return

    if data == "new":
        await db_reset(user_id)
        clear_ephemeral_user_files(user_id)
        await query.edit_message_text("📷 Пришли 3–5 фотографий. Лучше разные ракурсы и без сильных фильтров.")
        return

    if data == "how":
        await query.edit_message_text(
            "1️⃣ Загружаешь 3–5 фото\n"
            "2️⃣ Выбираешь стиль\n"
            "3️⃣ Получаешь 2 AI-превью\n"
            "4️⃣ Собираешь полный набор\n"
            "5️⃣ Бот публикует готовый Telegram-стикерпак\n\n"
            "Фото и выбранный стиль сохраняются, даже если сервер перезапустится.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В меню", callback_data="menu")]]),
        )
        return

    if data == "styles":
        await query.edit_message_text("🎨 <b>Выбери стиль</b>", parse_mode=ParseMode.HTML, reply_markup=styles_menu())
        return

    if data.startswith("style:"):
        style = STYLE_BY_CODE.get(data.split(":", 1)[1])
        if not style:
            await query.message.reply_text("Неизвестный стиль.")
            return
        state = await db_get(user_id)
        stage = "ready" if len(state.photo_file_ids) >= MIN_PHOTOS else "collecting"
        state = await db_set_style(user_id, style.code, stage)
        if len(state.photo_file_ids) < MIN_PHOTOS:
            await query.edit_message_text(
                f"{style.emoji} <b>{style.title}</b> выбран.\n\n"
                f"Теперь пришли ещё {MIN_PHOTOS - len(state.photo_file_ids)} фото через /new или кнопку «Создать стикеры».",
                parse_mode=ParseMode.HTML,
            )
            return
        await query.edit_message_text(
            f"{style.emoji} <b>{style.title}</b> выбран.\n\n"
            f"Фото: {len(state.photo_file_ids)}\n"
            f"Полный набор: {FULL_PACK_COUNT} стикеров\n"
            f"Цена: {PACK_PRICE_STARS} ⭐",
            parse_mode=ParseMode.HTML,
            reply_markup=order_menu(),
        )
        return

    if data == "preview":
        await generate(update, context, preview_only=True)
        return

    if data == "buy":
        state = await db_get(user_id)
        if len(state.photo_file_ids) < MIN_PHOTOS or not state.style:
            await query.message.reply_text("Нужны минимум 3 фото и выбранный стиль.")
            return
        if ENABLE_PAYMENTS:
            await context.bot.send_invoice(
                chat_id=query.message.chat_id,
                title="20 персональных AI-стикеров",
                description="StickerMe Ai персональный набор",
                payload=f"pack:{user_id}:{state.style}",
                currency="XTR",
                prices=[LabeledPrice("Стикерпак", PACK_PRICE_STARS)],
            )
            return
        if ALLOW_FREE_PACKS:
            await generate(update, context, preview_only=False)
        else:
            await query.message.reply_text("Бесплатный режим выключен.")


async def download_reference_photos(context: ContextTypes.DEFAULT_TYPE, user_id: int, file_ids: list[str]) -> list[Path]:
    paths = user_dirs(user_id)
    source_dir = paths["source"]
    if source_dir.exists():
        for old in source_dir.iterdir():
            if old.is_file():
                old.unlink(missing_ok=True)

    result: list[Path] = []
    for index, file_id in enumerate(file_ids[:MAX_PHOTOS], start=1):
        tg_file = await context.bot.get_file(file_id)
        target = source_dir / f"source_{index}_{uuid.uuid4().hex[:6]}.jpg"
        await tg_file.download_to_drive(custom_path=str(target))
        result.append(target)
    return result


async def keep_render_awake() -> None:
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        return
    try:
        while True:
            await asyncio.sleep(240)
            try:
                await asyncio.to_thread(requests.get, base_url, timeout=15)
            except Exception:
                logger.warning("Keep-awake ping failed", exc_info=True)
    except asyncio.CancelledError:
        raise


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE, preview_only: bool) -> None:
    if not update.effective_user or not update.effective_chat:
        return
    user_id = update.effective_user.id
    if user_id in RUNNING_JOBS:
        await update.effective_message.reply_text("⏳ Генерация уже идёт.")
        return

    state = await db_get(user_id)
    style = STYLE_BY_CODE.get(state.style or "")
    if len(state.photo_file_ids) < MIN_PHOTOS or not style:
        await update.effective_message.reply_text("Нужны минимум 3 фото и выбранный стиль.")
        return

    RUNNING_JOBS.add(user_id)
    keep_awake_task = asyncio.create_task(keep_render_awake())
    msg = await update.effective_message.reply_text(
        "🧠 Анализирую внешность и создаю стикеры.\n"
        "Фото и стиль уже сохранены — перезапуск сервера их не потеряет."
    )

    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        photo_paths = await download_reference_photos(context, user_id, state.photo_file_ids)
        count = PREVIEW_COUNT if preview_only else FULL_PACK_COUNT
        await msg.edit_text(f"🎨 Генерирую {count} стикера{'ов' if count != 2 else ''}…")
        result = await asyncio.to_thread(build_assets, user_id, photo_paths, style, preview_only)

        if preview_only:
            await msg.edit_text("✅ Превью готовы.")
            for path in result["preview"]:
                with path.open("rb") as fh:
                    await context.bot.send_photo(update.effective_chat.id, photo=fh)
            await update.effective_message.reply_text("Если нравится — собирай полный пак.", reply_markup=order_menu())
            return

        await msg.edit_text("✅ 20 изображений готовы. Публикую Telegram-стикерпак…")
        url = await publish(context, user_id, style, result["all"]) if AUTO_PUBLISH else None
        if url:
            await asyncio.to_thread(get_store().set_pack_url, user_id, url)
            await msg.edit_text(f"🎉 Готово! Добавить стикерпак:\n{url}")
        else:
            await msg.edit_text("✅ Генерация завершена.")

    except AIServiceError as exc:
        logger.exception("AI generation failed")
        message = str(exc)
        if "credit_balance_exhausted" in message or "insufficient_quota" in message:
            user_message = "⚠️ На AI-балансе закончились средства. Попробуй позже."
        elif "429" in message:
            user_message = "⚠️ AI временно перегружен. Попробуй ещё раз через минуту."
        else:
            user_message = "⚠️ AI-генерация временно не удалась. Попробуй ещё раз чуть позже."
        await msg.edit_text(user_message)
    except Exception:
        logger.exception("Generation failed for user %s", user_id)
        await msg.edit_text(
            "❌ Не удалось завершить генерацию. Заказ сохранён — фото повторно загружать не нужно. Попробуй ещё раз."
        )
    finally:
        keep_awake_task.cancel()
        try:
            await keep_awake_task
        except asyncio.CancelledError:
            pass
        RUNNING_JOBS.discard(user_id)


def build_assets(user_id: int, photo_paths: list[Path], style: Style, preview_only: bool) -> dict[str, object]:
    dirs_map = user_dirs(user_id)
    generated_dir = dirs_map["generated"]
    prepared_dir = dirs_map["prepared"]
    generated_dir.mkdir(parents=True, exist_ok=True)
    prepared_dir.mkdir(parents=True, exist_ok=True)

    ideas = IDEAS[:PREVIEW_COUNT] if preview_only else IDEAS[:FULL_PACK_COUNT]
    identity, raw = OpenAIImageService().generate_set(
        photo_paths,
        style.title,
        style.description,
        ideas,
        generated_dir,
    )

    prepared: list[Path] = []
    for index, raw_path in enumerate(raw, start=1):
        prepared.append(
            prepare_static_sticker(raw_path, prepared_dir / f"sticker_{index:02d}.png")
        )
    return {"identity": identity, "preview": prepared[:PREVIEW_COUNT], "all": prepared}


async def publish(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    style: Style,
    paths: list[Path],
) -> str:
    me = await context.bot.get_me()
    if not me.username:
        raise RuntimeError("У бота нет username")

    base = sanitize_pack_name(f"u{user_id}_{style.code}_{uuid.uuid4().hex[:8]}")
    name = re.sub(r"__+", "_", f"{base}_by_{me.username}")[:64]
    handles = [path.open("rb") for path in paths]
    try:
        stickers = [
            InputSticker(sticker=handle, emoji_list=[idea.emoji], format="static")
            for handle, idea in zip(handles, IDEAS[: len(handles)])
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


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return
    ok = query.invoice_payload.startswith("pack:")
    await query.answer(ok=ok, error_message=None if ok else "Неизвестный заказ")


async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.successful_payment or not update.effective_user:
        return
    charge_id = update.message.successful_payment.telegram_payment_charge_id
    await asyncio.to_thread(get_store().set_payment, update.effective_user.id, charge_id)
    await update.message.reply_text("✅ Оплата получена. Запускаю генерацию…")
    await generate(update, context, preview_only=False)


async def text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Используй кнопки меню или /new.", reply_markup=main_menu())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception", exc_info=context.error)


def build_application() -> Application:
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
    get_store()

    port = int(os.getenv("PORT", "10000"))
    public_url = os.getenv("RENDER_EXTERNAL_URL", "https://stickerme-ai.onrender.com").rstrip("/")
    webhook_path = "telegram"
    webhook_url = f"{public_url}/{webhook_path}"
    logger.info("Postgres state store ready")
    logger.info("Starting webhook on port %s at %s", port, webhook_url)
    build_application().run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
