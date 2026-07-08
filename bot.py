from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
import os
from datetime import datetime
import uuid

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# Этапы
WAITING_REVIEW, WAITING_SCREENSHOT = range(2)

# Хранилище отзывов
pending_reviews = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало"""
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Поделитесь вашим отзывом (2-3 предложения):"
    )
    return WAITING_REVIEW

async def handle_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем отзыв"""
    context.user_data['review_text'] = update.message.text
    context.user_data['first_name'] = update.effective_user.first_name
    context.user_data['user_id'] = update.effective_user.id
    
    await update.message.reply_text(
        "Спасибо! 👍\n\n"
        "Загрузите скриншот\n\n"
        "⚠️ Замажьте перед загрузкой:\n"
        "• Ваше имя/фамилию\n"
        "• Номер телефона\n"
        "• Адреса\n"
        "• Данные клиентов"
    )
    return WAITING_SCREENSHOT

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем скриншот и отправляем на модерацию"""
    
    first_name = context.user_data['first_name']
    review_text = context.user_data['review_text']
    user_id = context.user_data['user_id']
    screenshot_id = update.message.photo[-1].file_id
    
    # Создаем уникальный ID для отзыва
    review_id = str(uuid.uuid4())[:8]
    
    # Сохраняем отзыв
    pending_reviews[review_id] = {
        'first_name': first_name,
        'review_text': review_text,
        'screenshot_id': screenshot_id,
        'user_id': user_id,
        'timestamp': datetime.now()
    }
    
    # Сообщаем пользователю
    await update.message.reply_text(
        "✅ Спасибо! Ваш отзыв отправлен на модерацию.\n\n"
        "После одобрения он появится в группе!"
    )
    
    # Отправляем на модерацию админу
    await send_for_moderation(context, review_id)
    
    context.user_data.clear()
    return ConversationHandler.END

async def send_for_moderation(context: ContextTypes.DEFAULT_TYPE, review_id: str):
    """Отправляем отзыв админу на модерацию"""
    
    review = pending_reviews[review_id]
    
    caption = (
        f"📋 НОВЫЙ ОТЗЫВ НА МОДЕРАЦИЮ\n\n"
        f"👤 От: {review['first_name']}\n"
        f"💬 Текст: \"{review['review_text']}\"\n\n"
        f"⏰ Время: {review['timestamp'].strftime('%d.%m.%Y в %H:%M')}\n\n"
        f"ID отзыва: `{review_id}`"
    )
    
    # Кнопки одобрить/отклонить
    keyboard = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{review_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{review_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=review['screenshot_id'],
            caption=caption,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Ошибка при отправке админу: {e}")

async def approve_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Одобрить отзыв"""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем ID отзыва из callback_data
    review_id = query.data.replace("approve_", "")
    
    if review_id not in pending_reviews:
        await query.edit_message_caption(caption="❌ Отзыв не найден или уже обработан")
        return
    
    review = pending_reviews[review_id]
    
    # Публикуем в группу
    caption_text = (
        f"⭐ <b>ОТЗЫВ ОТ {review['first_name'].upper()}</b>\n\n"
        f"\"{review['review_text']}\"\n\n"
        f"<i>{review['timestamp'].strftime('%d.%m.%Y в %H:%M')}</i>"
    )
    
    try:
        await context.bot.send_photo(
            chat_id=GROUP_ID,
            photo=review['screenshot_id'],
            caption=caption_text,
            parse_mode='HTML'
        )
        
        # Уведомляем админа об одобрении
        await query.edit_message_caption(
            caption=f"✅ Отзыв одобрен и опубликован!\n\nID: `{review_id}`",
            parse_mode='Markdown'
        )
        
        # Удаляем из ожидания
        del pending_reviews[review_id]
        
        # Уведомляем пользователя
        await context.bot.send_message(
            chat_id=review['user_id'],
            text="✅ Ваш отзыв одобрен и опубликован в группе!"
        )
        
    except Exception as e:
        print(f"Ошибка при публикации: {e}")
        await query.edit_message_caption(caption=f"❌ Ошибка: {e}")

async def reject_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонить отзыв"""
    query = update.callback_query
    await query.answer()
    
    review_id = query.data.replace("reject_", "")
    
    if review_id not in pending_reviews:
        await query.edit_message_caption(caption="❌ Отзыв не найден или уже обработан")
        return
    
    review = pending_reviews[review_id]
    
    # Уведомляем админа об отклонении
    await query.edit_message_caption(
        caption=f"❌ Отзыв отклонен\n\nID: `{review_id}`",
        parse_mode='Markdown'
    )
    
    # Удаляем из ожидания
    del pending_reviews[review_id]
    
    # Уведомляем пользователя
    await context.bot.send_message(
        chat_id=review['user_id'],
        text="❌ К сожалению, ваш отзыв не был одобрен.\n\nПопробуйте заново с командой /start"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена"""
    await update.message.reply_text("Отменено")
    context.user_data.clear()
    return ConversationHandler.END

async def main():
    """Запуск бота"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Диалог для пользователей
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text)],
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, handle_screenshot)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Обработчики кнопок модерации
    app.add_handler(CallbackQueryHandler(approve_review, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(reject_review, pattern="^reject_"))
    
    # Основной диалог
    app.add_handler(conv_handler)
    
    await app.run_polling()

if __name__ == '__main__':
    app.run_polling()
