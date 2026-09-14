import logging
import os
from dataclasses import dataclass
from typing import Final

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("stiker-bot")

MIN_PHOTOS: Final = 3
MAX_PHOTOS: Final = 5
DEFAULT_PRICE_STARS: Final = 399

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PACK_PRICE_STARS = int(os.getenv("PACK_PRICE_STARS", str(DEFAULT_PRICE_STARS)))
ENABLE_PAYMENTS = os.getenv("ENABLE_PAYMENTS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@dataclass(frozen=True)
class Style:
    code: str
    title: str
    emoji: str
    description: str


STYLES: Final[tuple[Style, ...]] = (
    Style("cartoon3d", "3D Cartoon", "🧸", "Объёмный мультяшный герой"),
    Style("anime", "Anime", "🌸", "Яркий аниме-образ"),
    Style("realistic", "Realistic", "📸", "Максимум сходства с фото"),
    Style("meme", "Meme", "😂", "Сильные эмоции и мемные реакции"),
    Style("gangster", "Gangster", "😎", "Дерзкий стиль и характер"),
    Style("romantic", "Romantic", "❤️", "Мягкий романтический набор"),
)
STYLE_BY_CODE = {style.code: style for style in STYLES}


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✨ Создать стикеры", callback_data="new_pack")],
            [InlineKeyboardButton("🎨 Посмотреть стили", callback_data="show_styles")],
            [InlineKeyboardButton("ℹ️ Как это работает", callback_data="how_it_works")],
        ]
    )


def styles_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for style in STYLES:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{style.emoji} {style.title}", callback_data=f"style:{style.code}"
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def photos_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎨 Выбрать стиль", callback_data="show_styles")],
            [InlineKeyboardButton("🔄 Начать заново", callback_data="new_pack")],
        ]
    )


def order_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"⭐ Получить 20 стикеров — {PACK_PRICE_STARS} Stars",
                    callback_data="buy_pack",
                )
            ],
            [InlineKeyboardButton("🎨 Выбрать другой стиль", callback_data="show_styles")],
        ]
    )


def reset_order(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    context.user_data["stage"] = "collecting_photos"
    context.user_data["photos"] = []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    context.user_data.clear()
    await update.message.reply_text(
        "👋 <b>Привет!</b>\n\n"
        "Я превращаю твои фотографии в персональный Telegram-стикерпак.\n\n"
        "Загрузи несколько фото, выбери стиль — и получишь набор с собой.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Чтобы создать набор, нажми «✨ Создать стикеры», затем отправь 3–5 фотографий.\n\n"
        "Команды:\n"
        "/start — главное меню\n"
        "/new — новый набор\n"
        "/status — состояние текущего заказа\n"
        "/cancel — сбросить текущий заказ"
    )


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_order(context)
    if update.message:
        await update.message.reply_text(
            "📷 Пришли мне <b>3–5 фотографий</b>.\n\n"
            "Лучше всего подходят фото, где:\n"
            "• лицо хорошо видно;\n"
            "• есть разные ракурсы;\n"
            "• нет сильных фильтров;\n"
            "• нормальное освещение.",
            parse_mode=ParseMode.HTML,
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    photos = context.user_data.get("photos", [])
    style_code = context.user_data.get("style")
    style = STYLE_BY_CODE.get(style_code) if style_code else None
    await update.message.reply_text(
        f"📦 Текущий заказ\n\n"
        f"Фото: {len(photos)}/{MAX_PHOTOS}\n"
        f"Стиль: {style.emoji + ' ' + style.title if style else 'не выбран'}\n"
        f"Цена: {PACK_PRICE_STARS} ⭐"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Заказ сброшен.", reply_markup=main_menu())


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    if context.user_data.get("stage") != "collecting_photos":
        await update.message.reply_text(
            "Сначала нажми «✨ Создать стикеры» или используй /new."
        )
        return

    photos = context.user_data.setdefault("photos", [])
    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"Уже загружено {MAX_PHOTOS} фото. Теперь выбери стиль.",
            reply_markup=photos_ready_keyboard(),
        )
        return

    best_photo = update.message.photo[-1]
    photos.append(best_photo.file_id)
    count = len(photos)

    if count < MIN_PHOTOS:
        await update.message.reply_text(
            f"✅ Фото {count}/{MIN_PHOTOS} принято. Пришли ещё {MIN_PHOTOS - count}."
        )
        return

    if count < MAX_PHOTOS:
        await update.message.reply_text(
            f"✅ Загружено {count} фото. Этого уже достаточно.\n"
            f"Можно добавить ещё {MAX_PHOTOS - count} или выбрать стиль.",
            reply_markup=photos_ready_keyboard(),
        )
        return

    await update.message.reply_text(
        f"🔥 Все {MAX_PHOTOS} фото загружены. Выбирай стиль.",
        reply_markup=photos_ready_keyboard(),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""

    if data == "menu":
        await query.edit_message_text(
            "Выбери действие:",
            reply_markup=main_menu(),
        )
        return

    if data == "new_pack":
        reset_order(context)
        await query.edit_message_text(
            "📷 Пришли мне <b>3–5 фотографий</b>.\n\n"
            "Лучше всего: лицо крупно, разные ракурсы, без сильных фильтров.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "how_it_works":
        await query.edit_message_text(
            "1️⃣ Загружаешь 3–5 фото\n"
            "2️⃣ Выбираешь стиль\n"
            "3️⃣ Получаешь превью\n"
            "4️⃣ После оплаты создаётся полный набор\n\n"
            "Сейчас запущен технический MVP: генерация изображений будет подключена следующим этапом.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ В меню", callback_data="menu")]]
            ),
        )
        return

    if data == "show_styles":
        photos = context.user_data.get("photos", [])
        text = "🎨 <b>Выбери стиль</b>"
        if len(photos) < MIN_PHOTOS:
            text += (
                f"\n\nДля заказа сначала нужно загрузить минимум {MIN_PHOTOS} фото. "
                "Стили можно посмотреть уже сейчас."
            )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=styles_keyboard(),
        )
        return

    if data.startswith("style:"):
        style_code = data.split(":", 1)[1]
        style = STYLE_BY_CODE.get(style_code)
        if not style:
            await query.edit_message_text("Неизвестный стиль. Попробуй ещё раз.")
            return

        photos = context.user_data.get("photos", [])
        context.user_data["style"] = style.code

        if len(photos) < MIN_PHOTOS:
            await query.edit_message_text(
                f"{style.emoji} <b>{style.title}</b>\n{style.description}\n\n"
                f"Стиль выбран. Теперь загрузи минимум {MIN_PHOTOS} фото через /new.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ К стилям", callback_data="show_styles")]]
                ),
            )
            return

        context.user_data["stage"] = "ready_to_order"
        await query.edit_message_text(
            f"{style.emoji} <b>{style.title}</b> выбран.\n\n"
            f"Фото: {len(photos)}\n"
            f"Полный набор: <b>20 стикеров</b>\n"
            f"Стоимость: <b>{PACK_PRICE_STARS} ⭐</b>\n\n"
            "Следующий этап проекта добавит 2 бесплатных AI-превью перед оплатой.",
            parse_mode=ParseMode.HTML,
            reply_markup=order_keyboard(),
        )
        return

    if data == "buy_pack":
        photos = context.user_data.get("photos", [])
        style_code = context.user_data.get("style")
        if len(photos) < MIN_PHOTOS or not style_code:
            await query.message.reply_text(
                "Для заказа нужны минимум 3 фото и выбранный стиль."
            )
            return

        if not ENABLE_PAYMENTS:
            await query.message.reply_text(
                "🧪 Платёжный модуль уже подготовлен, но реальные списания Stars пока отключены.\n\n"
                "Сначала подключим генерацию и выдачу готового стикерпака — после этого включим оплату."
            )
            return

        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title="20 персональных AI-стикеров",
            description="Персональный Telegram-стикерпак в выбранном стиле",
            payload=f"sticker_pack:{query.from_user.id}:{style_code}",
            currency="XTR",
            prices=[LabeledPrice("Стикерпак", PACK_PRICE_STARS)],
        )
        return


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return
    if not query.invoice_payload.startswith("sticker_pack:"):
        await query.answer(ok=False, error_message="Неизвестный заказ.")
        return
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.successful_payment:
        return
    payment = update.message.successful_payment
    context.user_data["payment_charge_id"] = payment.telegram_payment_charge_id
    context.user_data["stage"] = "paid"
    logger.info(
        "Successful payment: user=%s charge_id=%s amount=%s %s",
        update.effective_user.id if update.effective_user else None,
        payment.telegram_payment_charge_id,
        payment.total_amount,
        payment.currency,
    )
    await update.message.reply_text(
        "✅ Оплата получена. Заказ отмечен как оплаченный.\n"
        "Модуль генерации подключается следующим этапом."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Используй кнопки меню или команду /new, чтобы начать новый стикерпак.",
        reply_markup=main_menu(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update", exc_info=context.error)


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Add it to environment variables.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    app = build_application()
    logger.info(
        "Starting bot. price=%s stars payments_enabled=%s",
        PACK_PRICE_STARS,
        ENABLE_PAYMENTS,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
