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

def is_admin(uid): return uid == ADMIN_ID
def is_allowed(uid): return uid in USERS

YF_MAP = {"XAUUSD":"XAUUSD=X","EURUSD":"EURUSD=X","GBPUSD":"GBPUSD=X","NAS100":"^NDX","US30":"^DJI"}

def get_data(sym, interval):
    try:
        t = Ticker(YF_MAP.get(sym, sym))
        df = t.history(period='60d', interval={'5m':'5m','1h':'1h','4h':'1h'}.get(interval,'5m'))
        if df is None or df.empty: return None
        if isinstance(df.index, pd.MultiIndex): df = df.reset_index(level=0, drop=True)
        df = df.reset_index().rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})
        df = df[['Open','High','Low','Close']].dropna()
        if interval == '4h':
            df = df.set_index(pd.to_datetime(df.index))
            df = df.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
        return df.tail(200)
    except: return None

def get_current_price(tv):
    try:
        if tv == "XAUUSD": return round(requests.get("https://api.gold-api.com/price/XAU", timeout=5).json().get("price",0),2)
        if tv == "EURUSD": return round(1/requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]["EUR"],5)
        if tv == "GBPUSD": return round(1/requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]["GBP"],5)
        if tv == "NAS100":
            r = requests.get("https://financialmodelingprep.com/api/v3/quote/%5ENDX?apikey=demo", timeout=5)
            return round(r.json()[0]["price"],2)
        if tv == "US30":
            r = requests.get("https://financialmodelingprep.com/api/v3/quote/%5EDJI?apikey=demo", timeout=5)
            return round(r.json()[0]["price"],2)
    except: return None

def bias_htf(df):
    if df is None or len(df)<2: return 0
    return 1 if df['Close'].iloc[-1] > df['High'].iloc[-2] else -1 if df['Close'].iloc[-1] < df['Low'].iloc[-2] else 0

def is_crossover(df, d):
    if df is None or len(df)<7: return False
    ph,ch = df['High'].iloc[-7:-2].max(), df['High'].iloc[-6:-1].max()
    pl,cl = df['Low'].iloc[-7:-2].min(), df['Low'].iloc[-6:-1].min()
    pc,cc = df['Close'].iloc[-2], df['Close'].iloc[-1]
    return (pc <= ph and cc > ch) if d=="buy" else (pc >= pl and cc < cl)

def check_fractal(name, cfg):
    h4 = get_data(cfg["tv"], '4h')
    h1 = get_data(cfg["tv"], '1h')
    m5 = get_data(cfg["tv"], '5m')
    b4,b1 = bias_htf(h4), bias_htf(h1)
    if b1==1 and b1==b4 and is_crossover(m5,"buy"):
        if last_signals.get(name)!= "buy":
            last_signals[name]="buy"
            return f"🚨 {name}\nFr buy\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    if b1==-1 and b1==b4 and is_crossover(m5,"sell"):
        if last_signals.get(name)!= "sell":
            last_signals[name]="sell"
            return f"🚨 {name}\nFr sell\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    return None

async def scanner(app):
    while True:
        for n,c in PAIRS.items():
            msg = check_fractal(n,c)
            if msg:
                for uid in USERS:
                    try: await app.bot.send_message(uid, msg)
                    except: pass
        now = datetime.now(timezone.utc)
        await asyncio.sleep(max(5, 300 - ((now.minute%5)*60 + now.second)))

def menu(uid):
    base = [["💰 Prices","📋 Pairs"],["➕ Add","❌ Remove"]]
    if is_admin(uid): base.append(["👑 Admin"])
    return ReplyKeyboardMarkup(base, resize_keyboard=True)

async def start(u,c):
    if not is_allowed(u.effective_user.id): return
    await u.message.reply_text("✅ Fr Bot Ready", reply_markup=menu(u.effective_user.id))

async def text(u,c):
    uid=u.message.from_user.id
    if not is_allowed(uid): return
    t=u.message.text
    if t=="💰 Prices":
        msg="💰 PRICES\n\n"
        for n,cfg in PAIRS.items(): msg+=f"{n}: {get_current_price(cfg['tv']) or '—'}\n"
        await u.message.reply_text(msg, reply_markup=menu(uid))

if __name__=="__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    app.run_polling()