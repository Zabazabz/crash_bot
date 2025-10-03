import os
import telebot
TOKEN = os.getenv("TOKEN")
bot = telebot.TeleBot(TOKEN)
@bot.message_handler(func=lambda m: True)
def echo(m):
    bot.reply_to(m, f"Ты написал: {m.text}")
if __name__ == "__main__":
    bot.infinity_polling()