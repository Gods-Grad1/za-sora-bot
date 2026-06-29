import os
import sys
import threading
import time

# Disable proxies
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'

from telebot import apihelper
apihelper.proxy = None

# Import your bot
import bot

# Delete any existing webhook to avoid conflict (and kill hijack)
bot.bot.delete_webhook()
time.sleep(2)  # Let Telegram process

def run_bot():
    bot.bot.infinity_polling()

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()

from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return "Za Sora Bot is running! 🚀"