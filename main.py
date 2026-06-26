import os, json, asyncio, threading, pandas as pd, requests
from datetime import datetime, timezone
from yahooquery import Ticker
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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

YF_MAP = {
    "XAUUSD": "XAUUSD=X", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "NAS100": "^NDX", "US30": "^DJI", "SPX500": "^GSPC",
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
}

def get_data(tv_symbol, exchange, interval, n_bars=300):
    yf_sym = YF_MAP.get(tv_symbol, tv_symbol)
    tf_map = {'5m':'5m', '15m':'15m', '1h':'1h', '4h':'1h'}
    try:
        t = Ticker(yf_sym)
        df = t.history(period='60d', interval=tf_map.get(interval,'15m'))
        if df is None or df.empty: return None
        if isinstance(df.index, pd.MultiIndex): df = df.reset_index(level=0, drop=True)
        df = df.reset_index().rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})
        df = df[['Open','High','Low','Close']].dropna().tail(n_bars*2)
        if interval == '4h':
            df = df.set_index(pd.to_datetime(df.index) if not isinstance(df.index, pd.DatetimeIndex) else df.index)
            df = df.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
        return df.tail(n_bars)
    except: return None

def get_current_price(tv_symbol):
    try:
        if tv_symbol == "XAUUSD":
            r = requests.get("https://api.gold-api.com/price/XAU", timeout=5)
            return round(r.json().get("price", 0), 2)

        if tv_symbol in ["EURUSD", "GBPUSD"]:
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            rates = r.json().get("rates", {})
            if tv_symbol == "EURUSD":
                return round(1 / rates.get("EUR", 1), 5)
            if tv_symbol == "GBPUSD":
                return round(1 / rates.get("GBP", 1), 5)

        if tv_symbol == "NAS100":
            r = requests.get("https://stooq.com/q/l/?s=ndx&f=l", timeout=5)
            price = float(r.text.strip().split()[-1])
            if 5000 < price < 30000: return round(price, 2)

        if tv_symbol == "US30":
            r = requests.get("https://stooq.com/q/l/?s=dji&f=l", timeout=5)
            price = float(r.text.strip().split()[-1])
            if 20000 < price < 60000: return round(price, 2)
    except Exception as e:
        print(f"Price ERR {tv_symbol}: {e}")
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
    ph, ch = df['High'].iloc[-7:-2].max(), df['High'].iloc[-6:-1].max()
    pl, cl = df['Low'].iloc[-7:-2].min(), df['Low'].iloc[-6:-1].min()
    pc, cc = df['Close'].iloc[-2], df['Close'].iloc[-1]
    return (pc <= ph and cc > ch) if direction=="buy" else (pc >= pl and cc < cl)

def check_pair(name, cfg):
    h1 = get_data(cfg["tv"], cfg["ex"], '1h', 200)
    h4 = get_data(cfg["tv"], cfg["ex"], '4h', 200)
    m15 = get_data(cfg["tv"], cfg["ex"], '15m', 200)
    m5 = get_data(cfg["tv"], cfg["ex"], '5m', 200)
    b4, b1 = bias_htf(h4), bias_htf(h1)
    s15, s5 = cisd_state(m15), cisd_state(m5)
    sig = None
    if b1 == 1 and b1 == b4 and is_crossover(m5,"buy"): sig = "FRACTAL BUY"
    if b1 == -1 and b1 == b4 and is_crossover(m5,"sell"): sig = "FRACTAL SELL"
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
        now = datetime.now(timezone.utc)
        await asyncio.sleep(max(5, 300 - ((now.minute % 5) * 60 + now.second)))

def main_menu(uid):
    base = [["➕ Add Pair", "📋 My Pairs"], ["💰 Prices", "❌ Remove"], ["⬅ Back"]]
    if is_admin(uid): base.append(["👑 Admin"])
    return ReplyKeyboardMarkup(base, resize_keyboard=True)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid): await update.message.reply_text(f"⛔ Not authorized\nID: {uid}"); return
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
    if txt in ["Add User","Remove User"] and is_admin(uid):
        user_state[uid] = "add_user" if "Add" in txt else "del_user"
        await update.message.reply_text("Send ID:"); return
    if txt == "List Users" and is_admin(uid):
        await update.message.reply_text("Users:\n"+"\n".join(map(str,USERS)), reply_markup=main_menu(uid)); return
    if state=="add_user": USERS.append(int(txt)); save_json(USERS_FILE, list(set(USERS))); user_state[uid]=None; await update.message.reply_text("✅ Added", reply_markup=main_menu(uid)); return
    if state=="del_user":
        if int(txt) in USERS: USERS.remove(int(txt)); save_json(USERS_FILE, USERS)
        user_state[uid]=None; await update.message.reply_text("✅ Removed", reply_markup=main_menu(uid)); return

    if txt=="➕ Add Pair": user_state[uid]="adding"; await update.message.reply_text("Send: NAME TVSYMBOL EXCHANGE", reply_markup=main_menu(uid)); return
    if txt=="📋 My Pairs": await update.message.reply_text("\n".join([f"{k}" for k in PAIRS]), reply_markup=main_menu(uid)); return
    if txt=="💰 Prices":
        msg = "💰 LIVE PRICES\n\n"
        for name, cfg in PAIRS.items():
            p = get_current_price(cfg["tv"])
            msg += f"{name}: {p if p else '—'}\n"
        msg += f"\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        await update.message.reply_text(msg, reply_markup=main_menu(uid)); return
    if txt=="❌ Remove": user_state[uid]="removing"; await update.message.reply_text("Send NAME:", reply_markup=main_menu(uid)); return
    if txt=="⬅ Back": await update.message.reply_text("Back", reply_markup=main_menu(uid)); return

    if state=="adding" and len(txt.split())>=3:
        n,t,e = txt.split()[:3]; PAIRS[n.upper()]={"tv":t.upper(),"ex":e.upper()}; save_pairs(); user_state[uid]=None
        await update.message.reply_text(f"✅ {n.upper()} added", reply_markup=main_menu(uid))
    elif state=="removing": PAIRS.pop(txt.upper(),None); save_pairs(); user_state[uid]=None; await update.message.reply_text("✅ Removed", reply_markup=main_menu(uid))

if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    print("Bot started")
    app.run_polling(drop_pending_updates=True)