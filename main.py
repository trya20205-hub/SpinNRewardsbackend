import os
import telebot
from flask import Flask, request

# Load environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY", "secret")  # default if not set

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Root route to test server
@app.route("/", methods=["GET"])
def home():
    return {"ok": True, "message": "SpinNRewards backend is running"}, 200

# Webhook route
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def receive_update():
    json_str = request.get_data().decode("UTF-8")
    print("📩 Update received from Telegram:", json_str, flush=True)  # Logs every update
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return {"ok": True}, 200

# Simple start command
@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.reply_to(message, "✅ Bot is alive and webhook is working!")

if __name__ == "__main__":
    import requests

    RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
    if not RENDER_URL:
        RENDER_URL = f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com"

    webhook_url = f"{RENDER_URL}/{BOT_TOKEN}"
    print(f"🔗 Setting webhook to: {webhook_url}", flush=True)

    # Remove old webhook and set new one
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
    set_webhook_resp = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    )
    print("Webhook set response:", set_webhook_resp.text, flush=True)

    # Run Flask app
    app.run(host="0.0.0.0", port=10000)
