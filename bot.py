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

# --- Мини веб-сервер для Render ---
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


threading.Thread(target=run_health_server, daemon=True).start()
# --- Конец мини веб-сервера ---

WAITING_REVIEW = 0
pending_reviews = {}

WELCOME_TEXT = (
    "Если вас всё устроило, будем очень благодарны, если вы оставите отзыв "
    "о нашем сотрудничестве. Для повышения доверия к отзывам прошу, по возможности, "
    "приложить любой скриншот, подтверждающий сделку (например, часть нашей переписки, "
    "подтверждение оплаты, заказа или любой другой скриншот, который не содержит "
    "ваших личных данных). Это поможет другим людям убедиться, что отзыв оставлен "
    "реальным клиентом. Спасибо вам за доверие и уделённое время! 🤝\n\n"
    "📝 Напишите ваш отзыв одним сообщением.\n"
    "📎 Если хотите приложить скриншот — прикрепите фото и напишите отзыв "
    "в подписи к нему."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(WELCOME_TEXT)
    return WAITING_REVIEW


async def handle_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if msg.photo and msg.caption:
        return await submit_review(
            update, context,
            review_text=msg.caption,
            screenshot_id=msg.photo[-1].file_id,
        )

    if msg.photo and not msg.caption:
        context.user_data['screenshot_id'] = msg.photo[-1].file_id
        await msg.reply_text(
            "📸 Скриншот получен!\n\nТеперь напишите текст вашего отзыва:"
        )
        return WAITING_REVIEW

    if msg.text:
        screenshot_id = context.user_data.get('screenshot_id')
        return await submit_review(
            update, context,
            review_text=msg.text,
            screenshot_id=screenshot_id,
        )

    await msg.reply_text("Пожалуйста, отправьте текст отзыва или фото с подписью.")
    return WAITING_REVIEW


async def submit_review(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        review_text: str, screenshot_id):
    user = update.effective_user
    review_id = str(uuid.uuid4())[:8]

    pending_reviews[review_id] = {
        'first_name': user.first_name,
        'review_text': review_text,
        'screenshot_id': screenshot_id,
        'user_id': user.id,
        'timestamp': datetime.now(),
    }

    await update.message.reply_text(
        "✅ Спасибо! Ваш отзыв отправлен на модерацию.\n\n"
        "После одобрения он появится в группе!"
    )

    caption = (
        f"📋 НОВЫЙ ОТЗЫВ НА МОДЕРАЦИЮ\n\n"
        f"👤 От: {user.first_name}\n"
        f"💬 Текст: \"{review_text}\"\n"
        f"📎 Скриншот: {'есть' if screenshot_id else 'нет'}\n\n"
        f"⏰ {datetime.now().strftime('%d.%m.%Y в %H:%M')}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{review_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{review_id}"),
        ]
    ])

    if screenshot_id:
        await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=screenshot_id,
            caption=caption, reply_markup=keyboard,
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=caption, reply_markup=keyboard,
        )

    context.user_data.clear()
    return ConversationHandler.END


async def edit_moderation_message(query, new_text: str):
    if query.message.photo:
        await query.edit_message_caption(caption=new_text)
    else:
        await query.edit_message_text(text=new_text)


async def approve_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review_id = query.data.replace("approve_", "")

    if review_id not in pending_reviews:
        await edit_moderation_message(query, "❌ Отзыв не найден или уже обработан")
        return

    review = pending_reviews[review_id]

    caption_text = (
        f"⭐ <b>ОТЗЫВ ОТ {review['first_name'].upper()}</b>\n\n"
        f"\"{review['review_text']}\"\n\n"
        f"<i>{review['timestamp'].strftime('%d.%m.%Y в %H:%M')}</i>"
    )

    if review['screenshot_id']:
        await context.bot.send_photo(
            chat_id=GROUP_ID, photo=review['screenshot_id'],
            caption=caption_text, parse_mode='HTML',
        )
    else:
        await context.bot.send_message(
            chat_id=GROUP_ID, text=caption_text, parse_mode='HTML',
        )

    await edit_moderation_message(query, "✅ Отзыв одобрен и опубликован!")

    try:
        await context.bot.send_message(
            chat_id=review['user_id'],
            text="✅ Ваш отзыв одобрен и опубликован в группе! Спасибо! 🤝",
        )
    except Exception as e:
        print(f"Не удалось уведомить пользователя: {e}")

    del pending_reviews[review_id]


async def reject_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review_id = query.data.replace("reject_", "")

    if review_id not in pending_reviews:
        await edit_moderation_message(query, "❌ Отзыв не найден или уже обработан")
        return

    review = pending_reviews[review_id]
    await edit_moderation_message(query, "❌ Отзыв отклонён")

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
    await update.message.reply_text("Отменено. Начать заново: /start")
    context.user_data.clear()
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CallbackQueryHandler(approve_review, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(reject_review, pattern="^reject_"))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_REVIEW: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
                    handle_review,
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    print("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
