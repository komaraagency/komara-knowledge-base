    import os
    import telebot
    from dotenv import load_dotenv

    load_dotenv()
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    bot = telebot.TeleBot(TOKEN)

    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        bot.reply_to(message, "Salut ! Je suis Komara Bot 🤖 Je suis en ligne.")

    @bot.message_handler(func=lambda message: True)
    def handle_message(message):
        bot.reply_to(message, f"Message reçu : {message.text}")

    print("Bot Komara lancé...")
    bot.polling()
