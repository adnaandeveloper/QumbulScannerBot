import os, json, asyncio, yfinance as yf, threading
from datetime import datetime, timezone
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) or CHAT_ID
DATA_FILE = "/tmp/pairs.json"
USERS_FILE = "/tmp/users.json"

def load_json(path, default): return json.load(open(path)) if os.path.exists(path) else default
def save_json(path, data): json.dump(data, open(path, "w"))

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

def save_pairs(pairs): save_json(DATA_FILE, pairs)
PAIRS = load_pairs()
USERS = load_json(USERS_FILE, [ADMIN_ID])
last_signals = {}
user_state = {}

def is_admin(uid): return uid == ADMIN_ID
def is_allowed(uid): return uid in USERS

# === CLEAN DATA FETCHER - NO curl_cffi ===
def get_data(symbol, interval, period="7d"):
    try:
        df = yf.download(
            symbol,
            interval=interval,
            period=period,
            progress=False,
            threads=False,
            auto_adjust=False,
            prepost=True
        )
        if df is None or df.empty:
            return None
        return df.dropna()
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None

def bias_htf(df):
    if df is None or len(df) < 2: return 0
    return 1 if df['Close'].iloc[-1] > df['High'].iloc[-2] else -1 if df['Close'].iloc[-1] < df['Low'].iloc[-2] else 0

def cisd_state(df):
    if df is None or len(df) < 6: return "—"
    h, l, c = df['High'].iloc[-6:-1].max(), df['Low'].iloc[-6:-1].min(), df['Close'].iloc[-1]
    return "BUY ✓" if c > h else "SELL ✓" if c < l else "—"

def is_crossover(df, direction="buy"):
    if df is None or len(df) < 7: return False
    prev_highest = df['High'].iloc[-7:-2].max()
    curr_highest = df['High'].iloc[-6:-1].max()
    prev_lowest = df['Low'].iloc[-7:-2].min()
    curr_lowest = df['Low'].iloc[-6:-1].min()
    prev_close = df['Close'].iloc[-2]
    curr_close = df['Close'].iloc[-1]
    if direction == "buy":
        return prev_close <= prev_highest and curr_close > curr_highest
    else:
        return prev_close >= prev_lowest and curr_close < curr_lowest

def check_pair(name, cfg):
    sym = cfg["yf"]
    h4 = get_data(sym, "60m", "30d")
    if h4 is not None: h4 = h4.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
    h1 = get_data(sym, "60m", "7d")
    m15 = get_data(sym, "15m", "2d")
    m5 = get_data(sym, "5m", "2d")
    b4, b1 = bias_htf(h4), bias_htf(h1)
    s15, s5 = cisd_state(m15), cisd_state(m5)
    buy_cross = is_crossover(m5, "buy")
    sell_cross = is_crossover(m5, "sell")
    print(f"{name} | 4H:{b4} 1H:{b1} buyX:{buy_cross} sellX:{sell_cross}")
    sig = None
    if b1 == 1 and b1 == b4 and buy_cross: sig = "FRACTAL BUY"
    if b1 == -1 and b1 == b4 and sell_cross: sig = "FRACTAL SELL"
    if not sig or last_signals.get(name) == sig: return None
    last_signals[name] = sig
    return f"🚨 {name}\n{sig}\n\n4H: {'BUY ✓' if b4==1 else 'SELL ✓' if b4==-1 else '—'}\n1H: {'BUY ✓' if b1==1 else 'SELL ✓' if b1==-1 else '—'}\n15m: {s15}\n5m: {s5}\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"

async def scanner(app):
    while True:
        for n,c in PAIRS.items():
            msg = check_pair(n,c)
            if msg:
                for uid in USERS:
                    try: await app.bot.send_message(uid, msg)
                    except: pass
        await asyncio.sleep(60)

def main_menu(uid):
    base = [["➕ Add Pair", "📋 My Pairs"], ["❌ Remove", "⬅ Back"]]
    if is_admin(uid): base.append(["👑 Admin"])
    return ReplyKeyboardMarkup(base, resize_keyboard=True)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(f"⛔ Not authorized\nYour ID: {uid}\nAsk admin to add you.")
        return
    await update.message.reply_text("✅ Qumbul is ALIVE bro!", reply_markup=main_menu(uid))

async def id_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your ID: {update.effective_user.id}")

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if not is_allowed(uid): return
    txt = update.message.text.strip()
    state = user_state.get(uid)
    if txt == "👑 Admin" and is_admin(uid):
        await update.message.reply_text("Admin Panel:", reply_markup=ReplyKeyboardMarkup([["Add User","Remove User"],["List Users","⬅ Back"]], resize_keyboard=True)); return
    if txt == "Add User" and is_admin(uid): user_state[uid]="add_user"; await update.message.reply_text("Send Telegram ID to add:"); return
    if txt == "Remove User" and is_admin(uid): user_state[uid]="del_user"; await update.message.reply_text("Send Telegram ID to remove:"); return
    if txt == "List Users" and is_admin(uid): await update.message.reply_text("Users:\n" + "\n".join(map(str, USERS)), reply_markup=main_menu(uid)); return
    if state == "add_user": USERS.append(int(txt)); save_json(USERS_FILE, list(set(USERS))); user_state[uid]=None; await update.message.reply_text("✅ User added", reply_markup=main_menu(uid)); return
    if state == "del_user": USERS.remove(int(txt)) if int(txt) in USERS else None; save_json(USERS_FILE, USERS); user_state[uid]=None; await update.message.reply_text("✅ User removed", reply_markup=main_menu(uid)); return
    if txt == "➕ Add Pair": user_state[uid]="adding"; await update.message.reply_text("Send pair like: XAUUSD=X", reply_markup=main_menu(uid)); return
    if txt == "📋 My Pairs": pairs = "\n".join([f"{k} → {v['yf']}" for k,v in PAIRS.items()]); await update.message.reply_text(f"Your pairs:\n{pairs}", reply_markup=main_menu(uid)); return
    if txt == "❌ Remove": user_state[uid]="removing"; await update.message.reply_text("Send NAME to remove (e.g. XAUUSD)", reply_markup=main_menu(uid)); return
    if txt == "⬅ Back": await update.message.reply_text("Back", reply_markup=main_menu(uid)); return
    if state == "adding": sym=txt.upper(); name=sym.split("=")[0].split("-")[0]; PAIRS[name]={"yf":sym,"corr":""}; save_pairs(PAIRS); user_state[uid]=None; await update.message.reply_text(f"✅ Added {sym}", reply_markup=main_menu(uid))
    elif state == "removing": PAIRS.pop(txt.upper(),None); save_pairs(PAIRS); user_state[uid]=None; await update.message.reply_text("✅ Removed", reply_markup=main_menu(uid))

if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    print("Bot started")
    app.run_polling()