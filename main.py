import os, json, asyncio, threading, pandas as pd, time
from datetime import datetime, timezone
from yahooquery import Ticker
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === CONFIG ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE = "pairs.json"
USERS_FILE = "users.json"

def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default
def save_json(path, data):
    json.dump(data, open(path, "w"), indent=2)

PAIRS = load_json(DATA_FILE, {
    "XAUUSD": {"tv": "XAUUSD", "ex": "OANDA"},
    "NAS100": {"tv": "NAS100", "ex": "CME"},
    "EURUSD": {"tv": "EURUSD", "ex": "OANDA"},
    "GBPUSD": {"tv": "GBPUSD", "ex": "OANDA"},
    "US30": {"tv": "US30", "ex": "CBOT"},
})
USERS = load_json(USERS_FILE, [ADMIN_ID] if ADMIN_ID else [])
last_signals = {}
user_state = {}

def is_admin(uid): return uid == ADMIN_ID
def is_allowed(uid): return uid in USERS
def save_pairs(): save_json(DATA_FILE, PAIRS)

# === YAHOOQUERY ===
YF_MAP = {
    "XAUUSD": "XAUUSD=X", # spot gold - not futures
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GBPJPY": "GBPJPY=X",
    "EURJPY": "EURJPY=X",
    "USDJPY": "JPY=X",
    "NAS100": "^NDX", # Nasdaq 100
    "US30": "^DJI", # Dow Jones
    "SPX500": "^GSPC",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}
print("YahooQuery ready")

def get_data(tv_symbol, exchange, interval, n_bars=300):
    yf_sym = YF_MAP.get(tv_symbol, tv_symbol)
    tf_map = {'5m':'5m', '15m':'15m', '1h':'1h', '4h':'1h'}
    yf_interval = tf_map.get(interval, '15m')
    try:
        t = Ticker(yf_sym)
        df = t.history(period='60d', interval=yf_interval)
        if df is None or df.empty: return None
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level=0, drop=True)
        df = df.reset_index()
        df = df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})
        df = df[['Open','High','Low','Close']].dropna().tail(n_bars*2)
        if interval == '4h':
            df = df.set_index(pd.to_datetime(df.index) if not isinstance(df.index, pd.DatetimeIndex) else df.index)
            df = df.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
        return df.tail(n_bars)
    except Exception as e:
        print(f"YQ ERR {yf_sym}: {e}")
        return None

def get_current_price(tv_symbol):
    # SLOW BUT STABLE - 1.2 sec delay to avoid Yahoo rate limit
    yf_sym = YF_MAP.get(tv_symbol, tv_symbol)
    try:
        t = Ticker(yf_sym)
        time.sleep(1.2) # <--- this stops the dashes
        for period, interval in [('5d','5m'), ('1mo','1d'), ('5d','1h')]:
            df = t.history(period=period, interval=interval)
            if df is not None and not df.empty:
                if isinstance(df.index, pd.MultiIndex):
                    df = df.reset_index(level=0, drop=True)
                price = float(df['close'].iloc[-1])
                # sanity check - filter bad data
                if tv_symbol == "XAUUSD" and price > 3500: continue
                if tv_symbol == "NAS100" and price < 5000: continue
                return round(price, 5)
        return None
    except Exception as e:
        print(f"Price ERR {yf_sym}: {e}")
        return None

# === STRATEGY ===
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
    return (prev_close <= prev_highest and curr_close > curr_highest) if direction=="buy" else (prev_close >= prev_lowest and curr_close < curr_lowest)

def check_pair(name, cfg):
    tv_sym, ex = cfg["tv"], cfg["ex"]
    h1 = get_data(tv_sym, ex, '1h', 200)
    h4 = get_data(tv_sym, ex, '4h', 200)
    m15 = get_data(tv_sym, ex, '15m', 200)
    m5 = get_data(tv_sym, ex, '5m', 200)
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
                    except Exception as e: print(f"TG err {e}")
        now = datetime.now(timezone.utc)
        secs_to_next = 300 - ((now.minute % 5) * 60 + now.second)
        await asyncio.sleep(max(5, secs_to_next))

# === TELEGRAM ===
def main_menu(uid):
    base = [["➕ Add Pair", "📋 My Pairs"], ["💰 Prices", "❌ Remove"], ["⬅ Back"]]
    if is_admin(uid): base.append(["👑 Admin"])
    return ReplyKeyboardMarkup(base, resize_keyboard=True)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(f"⛔ Not authorized\nYour ID: {uid}")
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
        await update.message.reply_text("Admin:", reply_markup=ReplyKeyboardMarkup([["Add User","Remove User"],["List Users","⬅ Back"]], resize_keyboard=True)); return
    if txt == "Add User" and is_admin(uid): user_state[uid]="add_user"; await update.message.reply_text("Send ID:"); return
    if txt == "Remove User" and is_admin(uid): user_state[uid]="del_user"; await update.message.reply_text("Send ID:"); return
    if txt == "List Users" and is_admin(uid): await update.message.reply_text("Users:\n"+"\n".join(map(str,USERS)), reply_markup=main_menu(uid)); return
    if state=="add_user": USERS.append(int(txt)); save_json(USERS_FILE, list(set(USERS))); user_state[uid]=None; await update.message.reply_text("✅ Added", reply_markup=main_menu(uid)); return
    if state=="del_user":
        if int(txt) in USERS: USERS.remove(int(txt)); save_json(USERS_FILE, USERS)
        user_state[uid]=None; await update.message.reply_text("✅ Removed", reply_markup=main_menu(uid)); return

    if txt=="➕ Add Pair": user_state[uid]="adding"; await update.message.reply_text("Send: NAME TVSYMBOL EXCHANGE\nExample: XAGUSD XAGUSD OANDA", reply_markup=main_menu(uid)); return
    if txt=="📋 My Pairs": await update.message.reply_text("Pairs:\n"+"\n".join([f"{k} → {v['tv']} ({v['ex']})" for k,v in PAIRS.items()]), reply_markup=main_menu(uid)); return
    if txt=="💰 Prices":
        await update.message.reply_text("⏳ Fetching... (takes 6 sec)")
        msg = "💰 LIVE PRICES\n\n"
        for name, cfg in PAIRS.items():
            p = get_current_price(cfg["tv"])
            msg += f"{name}: {p if p else '—'}\n"
        msg += f"\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        await update.message.reply_text(msg, reply_markup=main_menu(uid))
        return
    if txt=="❌ Remove": user_state[uid]="removing"; await update.message.reply_text("Send NAME:", reply_markup=main_menu(uid)); return
    if txt=="⬅ Back": await update.message.reply_text("Back", reply_markup=main_menu(uid)); return

    if state=="adding":
        parts = txt.split()
        if len(parts)>=3:
            name, tv_sym, ex = parts[0].upper(), parts[1].upper(), parts[2].upper()
            PAIRS[name] = {"tv": tv_sym, "ex": ex}; save_pairs()
            await update.message.reply_text(f"✅ {name} added", reply_markup=main_menu(uid))
        user_state[uid]=None
    elif state=="removing":
        PAIRS.pop(txt.upper(), None); save_pairs(); user_state[uid]=None
        await update.message.reply_text("✅ Removed", reply_markup=main_menu(uid))

if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    print("Bot started with YahooQuery data")
    app.run_polling(drop_pending_updates=True)