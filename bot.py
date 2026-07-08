from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
import os
from datetime import datetime
import uuid

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# --- Мини веб-сервер для Render (чтобы был открытый порт) ---
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass  # не засоряем логи


def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


threading.Thread(target=run_health_server, daemon=True).start()
# --- Конец мини веб-сервера ---

# Этапы диалога
WAITING_REVIEW, WAITING_SCREENSHOT = range(2)

# Хранилище отзывов на модерации
pending_reviews = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога"""
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Поделитесь вашим отзывом (2-3 предложения):"
    )
    return WAITING_REVIEW


async def handle_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем текст отзыва"""
    context.user_data['review_text'] = update.message.text
    context.user_data['first_name'] = update.effective_user.first_name
    context.user_data['user_id'] = update.effective_user.id

    await update.message.reply_text(
        "Спасибо! 👍\n\n"
        "Теперь загрузите скриншот\n\n"
        "⚠️ Замажьте перед загрузкой:\n"
        "• Ваше имя/фамилию\n"
        "• Номер телефона\n"
        "• Адреса\n"
        "• Данные других людей"
    )
    return WAITING_SCREENSHOT


async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем скриншот и отправляем админу на модерацию"""
    first_name = context.user_data['first_name']
    review_text = context.user_data['review_text']
    user_id = context.user_data['user_id']
    screenshot_id = update.message.photo[-1].file_id

    # Уникальный ID отзыва
    review_id = str(uuid.uuid4())[:8]

    # Сохраняем отзыв в ожидание
    pending_reviews[review_id] = {
        'first_name': first_name,
        'review_text': review_text,
        'screenshot_id': screenshot_id,
        'user_id': user_id,
        'timestamp': datetime.now(),
    }

    await update.message.reply_text(
        "✅ Спасибо! Ваш отзыв отправлен на модерацию.\n\n"
        "После одобрения он появится в группе!"
    )

    # Отправляем админу с кнопками
    caption = (
        f"📋 НОВЫЙ ОТЗЫВ НА МОДЕРАЦИЮ\n\n"
        f"👤 От: {first_name}\n"
        f"💬 Текст: \"{review_text}\"\n\n"
        f"⏰ {datetime.now().strftime('%d.%m.%Y в %H:%M')}"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{review_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{review_id}"),
        ]
    ]

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=screenshot_id,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.user_data.clear()
    return ConversationHandler.END


async def approve_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ одобрил отзыв"""
    query = update.callback_query
    await query.answer()

    review_id = query.data.replace("approve_", "")

    if review_id not in pending_reviews:
        await query.edit_message_caption(caption="❌ Отзыв не найден или уже обработан")
        return

    review = pending_reviews[review_id]

    caption_text = (
        f"⭐ <b>ОТЗЫВ ОТ {review['first_name'].upper()}</b>\n\n"
        f"\"{review['review_text']}\"\n\n"
        f"<i>{review['timestamp'].strftime('%d.%m.%Y в %H:%M')}</i>"
    )

    # Публикуем в группу
    await context.bot.send_photo(
        chat_id=GROUP_ID,
        photo=review['screenshot_id'],
        caption=caption_text,
        parse_mode='HTML',
    )

    await query.edit_message_caption(caption="✅ Отзыв одобрен и опубликован!")

    # Уведомляем пользователя
    try:
        await context.bot.send_message(
            chat_id=review['user_id'],
            text="✅ Ваш отзыв одобрен и опубликован в группе!",
        )
    except Exception as e:
        print(f"Не удалось уведомить пользователя: {e}")

    del pending_reviews[review_id]


async def reject_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ отклонил отзыв"""
    query = update.callback_query
    await query.answer()

    review_id = query.data.replace("reject_", "")

    if review_id not in pending_reviews:
        await query.edit_message_caption(caption="❌ Отзыв не найден или уже обработан")
        return

    review = pending_reviews[review_id]

    await query.edit_message_caption(caption="❌ Отзыв отклонён")

    # Уведомляем пользователя
    try:
        await context.bot.send_message(
            chat_id=review['user_id'],
            text="❌ К сожалению, ваш отзыв не был одобрен.\n\n"
                 "Вы можете попробовать заново: /start",
        )
    except Exception as e:
        print(f"Не удалось уведомить пользователя: {e}")

    del pending_reviews[review_id]


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена"""
    await update.message.reply_text("Отменено. Начать заново: /start")
    context.user_data.clear()
    return ConversationHandler.END


def main():
    """Запуск бота"""
    app = Application.builder().token(BOT_TOKEN).build()

    # Кнопки модерации
    app.add_handler(CallbackQueryHandler(approve_review, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(reject_review, pattern="^reject_"))

    # Диалог с пользователем
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text)],
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, handle_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    print("Бот запущен!")
    app.run_polling()


if __name__ == '__main__':
    main()
