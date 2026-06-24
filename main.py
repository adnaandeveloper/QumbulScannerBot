import os, json, asyncio, yfinance as yf, pandas as pd
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("TELEGRAM_BOT")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT", "0"))
DATA_FILE = "/tmp/pairs.json"

# Load saved pairs or use defaults
def load_pairs():
    if os.path.exists(DATA_FILE):
        return json.load(open(DATA_FILE))
    return {
        "XAUUSD": {"yf": "XAUUSD=X", "corr": "XAGUSD=X"},
        "NAS100": {"yf": "NQ=F", "corr": "ES=F"},
        "SPX500": {"yf": "ES=F", "corr": "NQ=F"},
        "GBPUSD": {"yf": "GBPUSD=X", "corr": "EURUSD=X"},
        "US30": {"yf": "YM=F", "corr": "ES=F"},
        "BTCUSD": {"yf": "BTC-USD", "corr": "ETH-USD"},
        "EURUSD": {"yf": "EURUSD=X", "corr": "GBPUSD=X"},
        "ETHUSD": {"yf": "ETH-USD", "corr": "BTC-USD"},
        "GBPJPY": {"yf": "GBPJPY=X", "corr": "EURJPY=X"},
        "EURJPY": {"yf": "EURJPY=X", "corr": "USDJPY=X"},
        "USDJPY": {"yf": "USDJPY=X", "corr": "EURJPY=X"},
        "AUDJPY": {"yf": "AUDJPY=X", "corr": "USDJPY=X"},
        "XAEUR": {"yf": "XAUEUR=X", "corr": "XAUUSD=X"},
    }

def save_pairs(pairs):
    json.dump(pairs, open(DATA_FILE, "w"))

PAIRS = load_pairs()
last_signals = {}
user_state = {}

# === SCANNER LOGIC (same as before) ===
def get_data(symbol, interval, period="7d"):
    try:
        df = yf.download(symbol, interval=interval, period=period, progress=False, prepost=True)
        return df.dropna() if not df.empty else None
    except: return None

def bias_htf(df):
    if df is None or len(df) < 2: return 0
    return 1 if df['Close'].iloc[-1] > df['High'].iloc[-2] else -1 if df['Close'].iloc[-1] < df['Low'].iloc[-2] else 0

def cisd(df):
    if df is None or len(df) < 6: return "—"
    h, l, c = df['High'].iloc[-6:-1].max(), df['Low'].iloc[-6:-1].min(), df['Close'].iloc[-1]
    return "BUY ✓" if c > h else "SELL ✓" if c < l else "—"

def check_pair(name, cfg):
    sym = cfg["yf"]
    h4 = get_data(sym, "60m", "30d")
    if h4 is not None: h4 = h4.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
    h1 = get_data(sym, "60m", "7d")
    m15 = get_data(sym, "15m", "2d")
    m5 = get_data(sym, "5m", "2d")

    b4, b1 = bias_htf(h4), bias_htf(h1)
    s15, s5 = cisd(m15), cisd(m5)

    sig = None
    if b1 == 1 and b1 == b4 and s5 == "BUY ✓": sig = "FRACTAL BUY"
    if b1 == -1 and b1 == b4 and s5 == "SELL ✓": sig = "FRACTAL SELL"
    if not sig: return None

    if last_signals.get(name) == sig: return None
    last_signals[name] = sig

    return f"🚨 {name}\n{sig}\n\n4H: {'BUY ✓' if b4==1 else 'SELL ✓' if b4==-1 else '—'}\n1H: {'BUY ✓' if b1==1 else 'SELL ✓' if b1==-1 else '—'}\n15m: {s15}\n5m: {s5}\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"

async def scanner(app):
    while True:
        for n,c in PAIRS.items():
            msg = check_pair(n,c)
            if msg and CHAT_ID: await app.bot.send_message(CHAT_ID, msg)
        await asyncio.sleep(60)

# === BUTTONS ===
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("📋 My Pairs", callback_data="list")],
        [InlineKeyboardButton("❌ Remove", callback_data="remove"), InlineKeyboardButton("⏸️ Pause", callback_data="pause")]
    ])

def back_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main")]])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Qumbul Scanner v6\nChoose:", reply_markup=main_menu())

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id

    if q.data == "main":
        await q.edit_message_text("Qumbul Scanner v6\nChoose:", reply_markup=main_menu())

    elif q.data == "add":
        user_state[uid] = "adding"
        await q.edit_message_text("Send pair like: XAUUSD=X", reply_markup=back_btn())

    elif q.data == "list":
        txt = "\n".join([f"{k} → {v['yf']}" for k,v in PAIRS.items()]) or "Empty"
        await q.edit_message_text(f"Your pairs:\n{txt}", reply_markup=back_btn())

    elif q.data == "remove":
        buttons = [[InlineKeyboardButton(k, callback_data=f"del_{k}")] for k in PAIRS.keys()]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="main")])
        await q.edit_message_text("Tap to remove:", reply_markup=InlineKeyboardMarkup(buttons))

    elif q.data.startswith("del_"):
        PAIRS.pop(q.data[4:], None); save_pairs(PAIRS)
        await q.edit_message_text("✅ Removed", reply_markup=back_btn())

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if user_state.get(uid) == "adding":
        sym = update.message.text.upper().strip()
        PAIRS[sym.split("=")[0]] = {"yf": sym, "corr": ""}
        save_pairs(PAIRS); user_state[uid] = None
        await update.message.reply_text(f"✅ Added {sym}\nWriting added.", reply_markup=back_btn())

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    asyncio.create_task(scanner(app))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())