from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import os
from datetime import datetime

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

WAITING_REVIEW, WAITING_SCREENSHOT = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Поделитесь вашим отзывом (2-3 предложения):"
    )
    return WAITING_REVIEW

async def handle_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['review_text'] = update.message.text
    context.user_data['first_name'] = update.effective_user.first_name
    
    await update.message.reply_text(
        "Спасибо! 👍\n\n"
        "Загрузите скриншот\n\n"
        "⚠️ Замажьте перед загрузкой:\n"
        "• Имя/фамилию\n"
        "• Номер телефона\n"
        "• Адреса\n"
        "• Данные клиентов"
    )
    return WAITING_SCREENSHOT

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    first_name = context.user_data['first_name']
    review_text = context.user_data['review_text']
    screenshot_id = update.message.photo[-1].file_id
    
    caption = (
        f"⭐ <b>ОТЗЫВ ОТ {first_name.upper()}</b>\n\n"
        f"\"{review_text}\"\n\n"
        f"<i>{datetime.now().strftime('%d.%m.%Y в %H:%M')}</i>"
    )
    
    await context.bot.send_photo(
        chat_id=GROUP_ID,
        photo=screenshot_id,
        caption=caption,
        parse_mode='HTML'
    )
    
    await update.message.reply_text("✅ Отзыв опубликован!")
    context.user_data.clear()
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено")
    context.user_data.clear()
    return ConversationHandler.END

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_REVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_review_text)],
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, handle_screenshot)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    app.add_handler(conv_handler)
    await app.run_polling()

if __name__ == '__main__':
    app.run_polling()
