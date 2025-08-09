# main.py
import os
import json
import random
import threading
import time
from datetime import datetime
from typing import Dict, Any

from flask import Flask, jsonify
import telebot

# Optional: pymongo if MONGO_URI provided
MONGO_URI = os.getenv("MONGO_URI")

if MONGO_URI:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    db = client.get_database("spinnrewards_db")
    users_col = db.get_collection("users")
else:
    users_col = None

# envs (Railway/Render/Heroku -> set these in project settings)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Reward economy (use your numbers)
REWARD_POOL = [(0, 20), (300, 35), (500, 27), (800, 10), (1000, 5), (1500, 3)]

# Local file fallback (not recommended for production)
DATA_FILE = "users.json"

# --- Flask app (health) ---
app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"ok": True, "msg": "SpinNRewards backend is running"})

# --- Helper functions for storage ---
def load_users_file() -> Dict[str, Any]:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_users_file(users: Dict[str, Any]):
    with open(DATA_FILE, "w") as f:
        json.dump(users, f, indent=2)

def user_from_db(uid: str) -> Dict[str, Any]:
    doc = users_col.find_one({"user_id": int(uid)})
    if doc:
        # keep backwards-compatible shape
        doc.pop("_id", None)
        return doc
    return None

def upsert_user_db(uid: str, payload: Dict[str, Any]):
    payload["user_id"] = int(uid)
    users_col.update_one({"user_id": int(uid)}, {"$set": payload}, upsert=True)

# --- In-memory / file storage fallback ---
_users_cache = load_users_file()

def get_user(uid: str) -> Dict[str, Any]:
    uid = str(uid)
    if users_col:
        doc = user_from_db(uid)
        if doc:
            return doc
        # create default if missing
        default = {
            "user_id": int(uid),
            "coins": 0,
            "spins": 3,
            "last_spin_time": 0,
            "ref_by": None,
            "refs": [],
            "daily_refs": [],
            "weekly_refs": [],
            "task_pending": [],
            "task_done": []
        }
        upsert_user_db(uid, default)
        return default
    else:
        if uid not in _users_cache:
            _users_cache[uid] = {
                "coins": 0,
                "spins": 3,
                "last_spin_time": 0,
                "ref_by": None,
                "refs": [],
                "daily_refs": [],
                "weekly_refs": [],
                "task_pending": [],
                "task_done": []
            }
            save_users_file(_users_cache)
        return _users_cache[uid]

def save_user(uid: str, user_obj: Dict[str, Any]):
    uid = str(uid)
    if users_col:
        # remove user_id from doc to avoid conflict, will be set in upsert
        copy = dict(user_obj)
        copy.pop("user_id", None)
        upsert_user_db(uid, copy)
    else:
        _users_cache[uid] = user_obj
        save_users_file(_users_cache)

# --- utility to draw reward according to weights ---
def draw_reward() -> int:
    choices = []
    for val, weight in REWARD_POOL:
        choices.extend([val] * weight)
    return random.choice(choices)

# --- Telegram bot ---
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = str(message.from_user.id)
    user = get_user(uid)
    # referral handling: /start <ref>
    parts = message.text.split()
    if len(parts) > 1:
        ref = parts[1]
        try:
            if ref != uid:
                ref_user = get_user(ref)
                # prevent self-ref & duplicates & limits
                if uid not in ref_user["refs"]:
                    # limit daily/weekly handled lightly here
                    if len(ref_user.get("daily_refs", [])) < 5 and len(ref_user.get("weekly_refs", [])) < 40:
                        ref_user["refs"].append(uid)
                        ref_user.setdefault("daily_refs", []).append(uid)
                        ref_user.setdefault("weekly_refs", []).append(uid)
                        ref_user["spins"] = ref_user.get("spins", 0) + 1
                        save_user(ref, ref_user)
                        try:
                            bot.send_message(int(ref), f"🎉 You got 1 spin for referring {message.from_user.first_name}!")
                        except Exception:
                            pass
        except Exception:
            pass

    save_user(uid, user)  # ensure created
    bot.send_message(message.chat.id, "🎁 Welcome to SpinNRewards! Use /spin to play and /balance to check coins.")

@bot.message_handler(commands=["spin"])
def cmd_spin(message):
    uid = str(message.from_user.id)
    user = get_user(uid)
    now = time.time()

    # refill logic: if spins are 0 check last_spin_time for 10 hours (36000s)
    if user.get("spins", 0) <= 0:
        if now - user.get("last_spin_time", 0) >= 36000:
            user["spins"] = 2
        else:
            remain = int(36000 - (now - user.get("last_spin_time", 0)))
            hours = remain // 3600
            mins = (remain % 3600) // 60
            bot.send_message(message.chat.id, f"⏳ You can spin again in {hours}h {mins}m")
            return

    user["spins"] = user.get("spins", 0) - 1
    user["last_spin_time"] = now

    reward = draw_reward()
    user["coins"] = user.get("coins", 0) + reward
    save_user(uid, user)

    bot.send_message(message.chat.id, f"🎯 You won {reward} coins!")

@bot.message_handler(commands=["balance"])
def cmd_balance(message):
    uid = str(message.from_user.id)
    user = get_user(uid)
    coins = user.get("coins", 0)
    rupees = coins * 0.02
    bot.send_message(message.chat.id, f"💰 Coins: {coins}\n💵 Value: ₹{rupees:.2f}")

@bot.message_handler(commands=["myrefs"])
def cmd_myrefs(message):
    uid = str(message.from_user.id)
    user = get_user(uid)
    total = len(user.get("refs", []))
    bot.send_message(message.chat.id, f"👥 You referred {total} users.")

@bot.message_handler(commands=["referralboard"])
def cmd_referralboard(message):
    # fetch all users (DB) or from cache and sort
    if users_col:
        board = list(users_col.find({}, {"user_id": 1, "refs": 1}))
        board_sorted = sorted(board, key=lambda d: len(d.get("refs", [])), reverse=True)[:10]
        msg = "🏆 Top Referrers:\n"
        for i, d in enumerate(board_sorted, start=1):
            msg += f"{i}. {d.get('user_id')} - {len(d.get('refs', []))} refs\n"
    else:
        board = sorted(_users_cache.items(), key=lambda x: len(x[1].get("refs", [])), reverse=True)[:10]
        msg = "🏆 Top Referrers:\n"
        for i, (uid, data) in enumerate(board, start=1):
            msg += f"{i}. {uid} - {len(data.get('refs', []))} refs\n"
    bot.send_message(message.chat.id, msg)

@bot.message_handler(commands=["leaderboard"])
def cmd_leaderboard(message):
    if users_col:
        board = list(users_col.find({}, {"user_id": 1, "coins": 1}))
        board_sorted = sorted(board, key=lambda d: d.get("coins", 0), reverse=True)[:10]
        msg = "🏆 Top Users by Coins:\n"
        for i, d in enumerate(board_sorted, start=1):
            msg += f"{i}. {d.get('user_id')} - {d.get('coins',0)} coins\n"
    else:
        board = sorted(_users_cache.items(), key=lambda x: x[1].get("coins", 0), reverse=True)[:10]
        msg = "🏆 Top Users by Coins:\n"
        for i, (uid, data) in enumerate(board, start=1):
            msg += f"{i}. {uid} - {data.get('coins',0)} coins\n"
    bot.send_message(message.chat.id, msg)

# --- Task submission & admin approval (semi-automatic) ---
@bot.message_handler(commands=["submit"])
def cmd_submit(message):
    uid = str(message.from_user.id)
    user = get_user(uid)
    # mark a pending task (for now we just note user id)
    if uid not in user.get("task_pending", []):
        user.setdefault("task_pending", []).append(uid)
        save_user(uid, user)
        bot.send_message(message.chat.id, "✅ Task submitted. Awaiting admin approval.")
        # notify admin
        try:
            bot.send_message(ADMIN_ID, f"🔔 User {message.from_user.first_name} submitted a task. Approve with /approvetask {uid}")
        except Exception:
            pass

@bot.message_handler(commands=["approvetask"])
def cmd_approvetask(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /approvetask <user_id>")
        return
    uid = parts[1]
    user = get_user(uid)
    if uid in user.get("task_pending", []):
        reward = 500  # default task reward (you can change later)
        user["coins"] = user.get("coins", 0) + reward
        user["task_pending"].remove(uid)
        user.setdefault("task_done", []).append(uid)
        save_user(uid, user)
        bot.send_message(int(uid), f"✅ Your task has been approved! You earned {reward} coins.")
        bot.send_message(message.chat.id, "✅ Task approved.")
    else:
        bot.send_message(message.chat.id, "❌ No pending task found for that user.")

# --- Admin commands ---
@bot.message_handler(commands=["userstats"])
def cmd_userstats(message):
    if message.from_user.id != ADMIN_ID:
        return
    if users_col:
        total = users_col.count_documents({})
    else:
        total = len(_users_cache)
    bot.send_message(message.chat.id, f"📊 Total Users: {total}")

@bot.message_handler(commands=["setcoins"])
def cmd_setcoins(message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 3:
        bot.send_message(message.chat.id, "❌ Usage: /setcoins <user_id> <amount>")
        return
    uid, amount = parts[1], parts[2]
    try:
        amount_i = int(amount)
    except ValueError:
        bot.send_message(message.chat.id, "❌ amount must be a number")
        return
    user = get_user(uid)
    user["coins"] = amount_i
    save_user(uid, user)
    bot.send_message(message.chat.id, f"✅ Set {amount_i} coins for user {uid}.")

@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text.split(" ", 1)
    if len(text) < 2:
        bot.send_message(message.chat.id, "❌ Usage: /broadcast <message>")
        return
    msg = text[1]
    if users_col:
        for doc in users_col.find({}, {"user_id": 1}):
            try:
                bot.send_message(int(doc["user_id"]), f"📢 {msg}")
            except Exception:
                pass
    else:
        for uid in list(_users_cache.keys()):
            try:
                bot.send_message(int(uid), f"📢 {msg}")
            except Exception:
                pass
    bot.send_message(message.chat.id, "📣 Broadcast sent (attempted).")

# --- Start bot thread at import time so gunicorn main:app will create it ---
def _start_bot_thread():
    t = threading.Thread(target=bot.infinity_polling, kwargs={"timeout": 60, "long_polling_timeout": 60})
    t.daemon = True
    t.start()

# ensure we start only once
if "BOT_THREAD_STARTED" not in globals():
    _start_bot_thread()
    BOT_THREAD_STARTED = True

# Optionally - webhook setup (not used here):
# To switch to webhooks, remove polling and add a Flask route to receive updates,
# then call bot.remove_webhook() and bot.set_webhook(url="https://your-railway-url/webhook")
# Example (very brief):
#
# @app.route("/webhook", methods=["POST"])
# def webhook():
#     json_data = request.get_data().decode("utf-8")
#     update = telebot.types.Update.de_json(json_data)
#     bot.process_new_updates([update])
#     return "", 200
#
# Then you must set webhook manually with bot.set_webhook or via Telegram API.