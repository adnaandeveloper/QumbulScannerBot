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
        if tv == "XAUUSD":
            return round(requests.get("https://api.gold-api.com/price/XAU", timeout=5).json().get("price",0),2)
        if tv == "EURUSD":
            return round(1/requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]["EUR"],5)
        if tv == "GBPUSD":
            return round(1/requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5).json()["rates"]["GBP"],5)
        if tv == "NAS100":
            r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX?range=1d&interval=1m", headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
            return round(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"],2)
        if tv == "US30":
            r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EDJI?range=1d&interval=1m", headers={"User-Agent":"Mozilla/5.0"}, timeout=5)
            return round(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"],2)
    except: return None

# === PINE-EXACT LOGIC ===
def bias_htf_pine(df):
    if df is None or len(df) < 2: return 0
    # close > high[1]? 1 : close < low[1]? -1 : 0
    if df['Close'].iloc[-1] > df['High'].iloc[-2]: return 1
    if df['Close'].iloc[-1] < df['Low'].iloc[-2]: return -1
    return 0

def check_fractal(name, cfg):
    h4 = get_data(cfg["tv"], '4h')
    h1 = get_data(cfg["tv"], '1h')
    m5 = get_data(cfg["tv"], '5m')

    if h4 is None or h1 is None or m5 is None or len(m5) < 7:
        return None

    b4 = bias_htf_pine(h4)
    b1 = bias_htf_pine(h1)

    # Pine: highest(5)[1] and lowest(5)[1]
    prev_high_5 = m5['High'].iloc[-6:-1].max()
    prev_low_5 = m5['Low'].iloc[-6:-1].min()
    prev_close = m5['Close'].iloc[-2]
    curr_close = m5['Close'].iloc[-1]

    fBuy = (b1 == 1 and b4 == 1 and prev_close <= prev_high_5 and curr_close > prev_high_5)
    fSell = (b1 == -1 and b4 == -1 and prev_close >= prev_low_5 and curr_close < prev_low_5)

    if fBuy and last_signals.get(name)!= "buy":
        last_signals[name] = "buy"
        return f"🚨 {name}\nFr buy\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    if fSell and last_signals.get(name)!= "sell":
        last_signals[name] = "sell"
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
    if not is_allowed(u.effective_user.id): await u.message.reply_text(f"ID: {u.effective_user.id}"); return
    await u.message.reply_text("✅ Fr Bot Ready (Pine v5.9.14)\n/testfr /status /forcebuy /forcesell", reply_markup=menu(u.effective_user.id))

async def testfr(u,c):
    if not is_allowed(u.effective_user.id): return
    await u.message.reply_text(f"🚨 XAUUSD\nFr buy (TEST)\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}")

async def status(u,c):
    if not is_allowed(u.effective_user.id): return
    txt = "📊 LIVE STATUS\n\n"
    for n,cfg in PAIRS.items():
        h4 = get_data(cfg["tv"], '4h'); h1 = get_data(cfg["tv"], '1h'); m5 = get_data(cfg["tv"], '5m')
        b4 = bias_htf_pine(h4); b1 = bias_htf_pine(h1)
        b4t = "BUY" if b4==1 else "SELL" if b4==-1 else "—"
        b1t = "BUY" if b1==1 else "SELL" if b1==-1 else "—"
        if m5 is not None and len(m5)>6:
            ph = m5['High'].iloc[-6:-1].max(); pl = m5['Low'].iloc[-6:-1].min()
            txt += f"{n}: 4H {b4t} | 1H {b1t} | 5m {ph:.2f}/{pl:.2f}\n"
        else:
            txt += f"{n}: no data\n"
    await u.message.reply_text(txt)

async def forcebuy(u,c):
    if not is_allowed(u.effective_user.id): return
    pair = c.args[0].upper() if c.args else "XAUUSD"
    msg = f"🚨 {pair}\nFr buy\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    for uid in USERS:
        try: await c.bot.send_message(uid, msg)
        except: pass

async def forcesell(u,c):
    if not is_allowed(u.effective_user.id): return
    pair = c.args[0].upper() if c.args else "XAUUSD"
    msg = f"🚨 {pair}\nFr sell\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    for uid in USERS:
        try: await c.bot.send_message(uid, msg)
        except: pass

async def text(u,c):
    uid=u.message.from_user.id
    if not is_allowed(uid): return
    t=u.message.text
    if t=="💰 Prices":
        msg="💰 PRICES\n\n"
        for n,cfg in PAIRS.items():
            p=get_current_price(cfg['tv'])
            msg+=f"{n}: {p if p else '—'}\n"
        msg+=f"\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        await u.message.reply_text(msg, reply_markup=menu(uid))
    elif t=="📋 Pairs":
        await u.message.reply_text("\n".join(PAIRS.keys()), reply_markup=menu(uid))

if __name__=="__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("testfr", testfr))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("forcebuy", forcebuy))
    app.add_handler(CommandHandler("forcesell", forcesell))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    print("Bot started - Pine fractal mode")
    app.run_polling(drop_pending_updates=True)