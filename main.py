import os, json, asyncio, threading
from datetime import datetime, timezone
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE = "/tmp/pairs.json"
USERS_FILE = "/tmp/users.json"

def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default
def save_json(path, data):
    json.dump(data, open(path, "w"))

PAIRS = load_json(DATA_FILE, {
    "XAUUSD": {"yf": "XAUUSD"},
    "NAS100": {"yf": "NQ"},
    "SPX500": {"yf": "ES"},
    "GBPUSD": {"yf": "GBPUSD"},
    "US30": {"yf": "YM"},
    "BTCUSD": {"yf": "BTCUSD"},
    "EURUSD": {"yf": "EURUSD"},
})
USERS = load_json(USERS_FILE, [ADMIN_ID] if ADMIN_ID else [])
user_state = {}

def is_admin(uid): return uid == ADMIN_ID
def is_allowed(uid): return uid in USERS
def save_pairs(): save_json(DATA_FILE, PAIRS)

# === TELEGRAM BOT ===
def main_menu(uid):
    base = [["➕ Add Pair", "📋 My Pairs"], ["❌ Remove", "⬅ Back"]]
    if is_admin(uid): base.append(["👑 Admin"])
    return ReplyKeyboardMarkup(base, resize_keyboard=True)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(f"⛔ Not authorized\nYour ID: {uid}\nAsk admin to add you.")
        return
    await update.message.reply_text("✅ Qumbul webhook is ALIVE bro!", reply_markup=main_menu(uid))

async def id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your ID: {update.effective_user.id}")

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if not is_allowed(uid): return
    txt = update.message.text.strip()
    state = user_state.get(uid)

    # ADMIN
    if txt == "👑 Admin" and is_admin(uid):
        await update.message.reply_text("Admin Panel:", reply_markup=ReplyKeyboardMarkup([["Add User","Remove User"],["List Users","⬅ Back"]], resize_keyboard=True)); return
    if txt == "Add User" and is_admin(uid):
        user_state[uid]="add_user"; await update.message.reply_text("Send Telegram ID to add:"); return
    if txt == "Remove User" and is_admin(uid):
        user_state[uid]="del_user"; await update.message.reply_text("Send Telegram ID to remove:"); return
    if txt == "List Users" and is_admin(uid):
        await update.message.reply_text("Users:\n" + "\n".join(map(str, USERS)), reply_markup=main_menu(uid)); return
    if state == "add_user":
        USERS.append(int(txt)); save_json(USERS_FILE, list(set(USERS))); user_state[uid]=None
        await update.message.reply_text("✅ User added", reply_markup=main_menu(uid)); return
    if state == "del_user":
        if int(txt) in USERS: USERS.remove(int(txt)); save_json(USERS_FILE, USERS)
        user_state[uid]=None; await update.message.reply_text("✅ User removed", reply_markup=main_menu(uid)); return

    # PAIRS (for display only now)
    if txt == "➕ Add Pair":
        user_state[uid]="adding"; await update.message.reply_text("Send pair name like: XAUUSD", reply_markup=main_menu(uid)); return
    if txt == "📋 My Pairs":
        pairs = "\n".join([f"{k}" for k in PAIRS.keys()])
        await update.message.reply_text(f"Tracked pairs:\n{pairs}", reply_markup=main_menu(uid)); return
    if txt == "❌ Remove":
        user_state[uid]="removing"; await update.message.reply_text("Send NAME to remove", reply_markup=main_menu(uid)); return
    if txt == "⬅ Back":
        await update.message.reply_text("Back", reply_markup=main_menu(uid)); return
    if state == "adding":
        PAIRS[txt.upper()] = {"yf": txt.upper()}; save_pairs(); user_state[uid]=None
        await update.message.reply_text(f"✅ Added {txt.upper()}", reply_markup=main_menu(uid))
    elif state == "removing":
        PAIRS.pop(txt.upper(), None); save_pairs(); user_state[uid]=None
        await update.message.reply_text("✅ Removed", reply_markup=main_menu(uid))

# === FLASK WEBHOOK ===
app_flask = Flask(__name__)
loop = None

@app_flask.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    ticker = data.get('ticker', 'UNKNOWN')
    signal = data.get('signal', 'ALERT')
    msg = f"🚨 {ticker}\n{signal}\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    print(f"Webhook: {msg}")
    for uid in USERS:
        asyncio.run_coroutine_threadsafe(app_bot.bot.send_message(uid, msg), loop)
    return "ok", 200

def run_flask():
    app_flask.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

# === MAIN ===
if __name__ == "__main__":
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("id", id_cmd))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_flask, daemon=True).start()
    print("Bot started with webhook")
    app_bot.run_polling()