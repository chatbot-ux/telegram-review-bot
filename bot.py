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
from datetime import datetime, timedelta
import uuid

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# ================== НАСТРОЙКИ ЗАЩИТЫ ОТ СПАМА ==================
COOLDOWN_MINUTES = 30       # минимум времени между отзывами одного человека
MAX_REVIEWS_PER_DAY = 2     # сколько отзывов в сутки может оставить один человек
FLOOD_SECONDS = 2           # если пишет чаще — считаем флудом
MIN_REVIEW_LEN = 5          # минимальная длина текста отзыва
MAX_REVIEW_LEN = 1000       # максимальная длина текста отзыва
# ==============================================================

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
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


threading.Thread(target=run_health_server, daemon=True).start()
# --- Конец мини веб-сервера ---

# Этапы
WAITING_RATING, WAITING_REVIEW, CONFIRMING = range(3)

# Хранилища (в памяти)
pending_reviews = {}        # отзывы на модерации
last_submission = {}        # user_id -> время последней отправки
daily_count = {}            # user_id -> [дата, счётчик]
last_message_time = {}      # user_id -> время последнего сообщения (антифлуд)
banned_users = set()        # заблокированные user_id
stats = {"approved": 0, "rejected": 0, "ratings": []}

# Приветствие (основной текст — не менялся)
WELCOME_TEXT = (
    "🤝 <b>Спасибо, что были с нами!</b>\n\n"
    "Если вас всё устроило, будем очень благодарны, если вы оставите отзыв "
    "о нашем сотрудничестве.\n\n"
    "Для повышения доверия к отзывам прошу, по возможности, приложить "
    "<b>любой скриншот, подтверждающий сделку</b> (например, часть нашей переписки, "
    "подтверждение оплаты, заказа или любой другой скриншот, который "
    "<b>не содержит ваших личных данных</b>).\n\n"
    "Это поможет другим людям убедиться, что отзыв оставлен реальными людьми.\n\n"
    "<i>Спасибо вам за доверие и уделённое время!</i> 🤝\n\n"
    "➖➖➖➖➖➖➖➖➖\n\n"
    "Для начала, пожалуйста, оцените наше сотрудничество:"
)

REVIEW_INSTRUCTION = (
    "📝 Напишите ваш отзыв одним сообщением.\n"
    "📎 Хотите приложить скриншот — прикрепите фото и напишите отзыв "
    "в подписи к нему."
)


def rating_keyboard():
    buttons = [
        InlineKeyboardButton(f"{i}⭐", callback_data=f"rate_{i}")
        for i in range(1, 6)
    ]
    return InlineKeyboardMarkup([buttons])


def stars(rating: int) -> str:
    return "⭐" * rating


def is_flooding(user_id: int) -> bool:
    """Проверка: не пишет ли человек слишком часто"""
    now = datetime.now()
    last = last_message_time.get(user_id)
    last_message_time[user_id] = now
    if last and (now - last).total_seconds() < FLOOD_SECONDS:
        return True
    return False


def check_limits(user_id: int):
    """Проверка задержки и дневного лимита. Возвращает текст ошибки или None"""
    now = datetime.now()

    # Задержка между отзывами
    last = last_submission.get(user_id)
    if last and now - last < timedelta(minutes=COOLDOWN_MINUTES):
        minutes_left = COOLDOWN_MINUTES - int((now - last).total_seconds() // 60)
        return f"⏳ Вы недавно оставили отзыв. Попробуйте снова через {minutes_left} мин."

    # Дневной лимит
    today = now.date()
    record = daily_count.get(user_id)
    if record and record[0] == today and record[1] >= MAX_REVIEWS_PER_DAY:
        return "⏳ Вы уже оставили максимальное количество отзывов на сегодня. Спасибо!"

    return None


def register_submission(user_id: int):
    """Фиксируем факт отправки отзыва"""
    now = datetime.now()
    last_submission[user_id] = now
    today = now.date()
    record = daily_count.get(user_id)
    if record and record[0] == today:
        record[1] += 1
    else:
        daily_count[user_id] = [today, 1]


# ==================== ДИАЛОГ С ПОЛЬЗОВАТЕЛЕМ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in banned_users:
        await update.message.reply_text("⛔ К сожалению, вы не можете оставлять отзывы.")
        return ConversationHandler.END

    limit_msg = check_limits(user_id)
    if limit_msg:
        await update.message.reply_text(limit_msg)
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        WELCOME_TEXT, reply_markup=rating_keyboard(), parse_mode='HTML'
    )
    return WAITING_RATING


async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rating = int(query.data.replace("rate_", ""))
    context.user_data['rating'] = rating

    await query.edit_message_text(
        f"Ваша оценка: {stars(rating)}\n\n{REVIEW_INSTRUCTION}"
    )
    return WAITING_REVIEW


async def handle_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    # Антифлуд
    if is_flooding(user_id):
        return WAITING_REVIEW

    # Фото с подписью
    if msg.photo and msg.caption:
        text = msg.caption.strip()
        if len(text) < MIN_REVIEW_LEN:
            await msg.reply_text("Пожалуйста, напишите отзыв чуть подробнее 🙏")
            return WAITING_REVIEW
        return await confirm_review(update, context, text[:MAX_REVIEW_LEN], msg.photo[-1].file_id)

    # Фото без подписи
    if msg.photo and not msg.caption:
        context.user_data['screenshot_id'] = msg.photo[-1].file_id
        await msg.reply_text("📸 Скриншот получен!\n\nТеперь напишите текст вашего отзыва:")
        return WAITING_REVIEW

    # Текст
    if msg.text:
        text = msg.text.strip()
        if len(text) < MIN_REVIEW_LEN:
            await msg.reply_text("Пожалуйста, напишите отзыв чуть подробнее 🙏")
            return WAITING_REVIEW
        screenshot_id = context.user_data.get('screenshot_id')
        return await confirm_review(update, context, text[:MAX_REVIEW_LEN], screenshot_id)

    await msg.reply_text("Пожалуйста, отправьте текст отзыва или фото с подписью.")
    return WAITING_REVIEW


async def confirm_review(update, context, review_text, screenshot_id):
    """Показываем предпросмотр и просим подтвердить"""
    context.user_data['review_text'] = review_text
    context.user_data['screenshot_id'] = screenshot_id
    rating = context.user_data.get('rating', 0)

    preview = (
        f"<b>Проверьте ваш отзыв перед отправкой:</b>\n\n"
        f"{stars(rating)} ({rating}/5)\n"
        f"\"{review_text}\"\n"
        f"📎 Скриншот: {'приложен' if screenshot_id else 'нет'}\n\n"
        f"Всё верно?"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Отправить", callback_data="confirm_send"),
            InlineKeyboardButton("🔄 Заново", callback_data="confirm_restart"),
        ]
    ])
    await update.message.reply_text(preview, reply_markup=keyboard, parse_mode='HTML')
    return CONFIRMING


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_restart":
        await query.edit_message_text("Хорошо, начнём заново 🙂 Отправьте /start")
        context.user_data.clear()
        return ConversationHandler.END

    # confirm_send
    user = update.effective_user
    review_text = context.user_data.get('review_text')
    screenshot_id = context.user_data.get('screenshot_id')
    rating = context.user_data.get('rating', 0)

    review_id = str(uuid.uuid4())[:8]
    pending_reviews[review_id] = {
        'first_name': user.first_name,
        'review_text': review_text,
        'screenshot_id': screenshot_id,
        'rating': rating,
        'user_id': user.id,
        'timestamp': datetime.now(),
    }
    register_submission(user.id)

    await query.edit_message_text(
        "✅ <b>Спасибо за ваш отзыв!</b>\n\n"
        "Он отправлен на модерацию и совсем скоро появится в нашей группе. "
        "Мы искренне ценим, что вы уделили время 🤝",
        parse_mode='HTML',
    )

    # Отправляем админу на модерацию
    caption = (
        f"📋 НОВЫЙ ОТЗЫВ НА МОДЕРАЦИЮ\n\n"
        f"👤 От: {user.first_name}\n"
        f"⭐ Оценка: {stars(rating)} ({rating}/5)\n"
        f"💬 Текст: \"{review_text}\"\n"
        f"📎 Скриншот: {'есть' if screenshot_id else 'нет'}\n\n"
        f"⏰ {datetime.now().strftime('%d.%m.%Y в %H:%M')}\n"
        f"(для блокировки автора ответьте на это сообщение командой /ban)"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{review_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{review_id}"),
        ]
    ])

    if screenshot_id:
        sent = await context.bot.send_photo(
            chat_id=ADMIN_ID, photo=screenshot_id, caption=caption, reply_markup=keyboard
        )
    else:
        sent = await context.bot.send_message(
            chat_id=ADMIN_ID, text=caption, reply_markup=keyboard
        )

    # Запоминаем, какому user_id соответствует сообщение (для /ban ответом)
    context.bot_data[sent.message_id] = user.id

    context.user_data.clear()
    return ConversationHandler.END


# ==================== МОДЕРАЦИЯ ====================

async def edit_mod_msg(query, text: str):
    if query.message.photo:
        await query.edit_message_caption(caption=text)
    else:
        await query.edit_message_text(text=text)


async def approve_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    review_id = query.data.replace("approve_", "")

    if review_id not in pending_reviews:
        await edit_mod_msg(query, "❌ Отзыв не найден или уже обработан")
        return

    review = pending_reviews[review_id]
    caption_text = (
        f"⭐ <b>ОТЗЫВ ОТ {review['first_name'].upper()}</b>\n"
        f"{stars(review['rating'])} ({review['rating']}/5)\n\n"
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

    stats["approved"] += 1
    stats["ratings"].append(review['rating'])
    await edit_mod_msg(query, "✅ Отзыв одобрен и опубликован!")

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
        await edit_mod_msg(query, "❌ Отзыв не найден или уже обработан")
        return

    review = pending_reviews[review_id]
    stats["rejected"] += 1
    await edit_mod_msg(query, "❌ Отзыв отклонён")

    try:
        await context.bot.send_message(
            chat_id=review['user_id'],
            text="❌ К сожалению, ваш отзыв не был одобрен.\n\n"
                 "Вы можете попробовать заново: /start",
        )
    except Exception as e:
        print(f"Не удалось уведомить пользователя: {e}")

    del pending_reviews[review_id]


# ==================== АДМИН-КОМАНДЫ ====================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    ratings = stats["ratings"]
    avg = round(sum(ratings) / len(ratings), 2) if ratings else 0
    await update.message.reply_text(
        f"📊 <b>Статистика отзывов</b>\n\n"
        f"✅ Одобрено: {stats['approved']}\n"
        f"❌ Отклонено: {stats['rejected']}\n"
        f"⏳ На модерации: {len(pending_reviews)}\n"
        f"⭐ Средняя оценка: {avg}/5\n"
        f"⛔ В чёрном списке: {len(banned_users)}",
        parse_mode='HTML',
    )


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Блокировка: ответьте /ban на сообщение о модерации"""
    if update.effective_user.id != ADMIN_ID:
        return
    reply = update.message.reply_to_message
    if not reply or reply.message_id not in context.bot_data:
        await update.message.reply_text("Ответьте командой /ban на сообщение с отзывом.")
        return
    target = context.bot_data[reply.message_id]
    banned_users.add(target)
    await update.message.reply_text(f"⛔ Пользователь заблокирован (ID: {target})")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разблокировка: /unban ID"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /unban 123456789")
        return
    try:
        target = int(context.args[0])
        banned_users.discard(target)
        await update.message.reply_text(f"✅ Пользователь разблокирован (ID: {target})")
    except ValueError:
        await update.message.reply_text("Неверный ID.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Начать заново: /start")
    context.user_data.clear()
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Модерация
    app.add_handler(CallbackQueryHandler(approve_review, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(reject_review, pattern="^reject_"))

    # Админ-команды
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    # Диалог с пользователем
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_RATING: [CallbackQueryHandler(handle_rating, pattern="^rate_")],
            WAITING_REVIEW: [
                MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_review)
            ],
            CONFIRMING: [CallbackQueryHandler(handle_confirm, pattern="^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    print("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
