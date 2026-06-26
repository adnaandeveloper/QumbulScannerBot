import os, json, asyncio, threading, pandas as pd, requests, yfinance as yf
from datetime import datetime, timezone
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
        yf_sym = YF_MAP.get(sym, sym)
        tf = '5m' if interval=='5m' else '1h'
        period = '7d' if interval=='5m' else '60d' if interval=='1h' else '730d'
        df = yf.download(yf_sym, period=period, interval=tf, progress=False, auto_adjust=False, threads=False)
        if df.empty: return None
        df = df.rename(columns={'Open':'Open','High':'High','Low':'Low','Close':'Close'})
        df = df[['Open','High','Low','Close']].dropna()
        if interval == '4h':
            df = df.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
        return df.tail(200)
    except Exception as e:
        print(f"yf err {sym} {interval}: {e}")
        return None

def get_current_price(tv):
    try:
        if tv == "XAUUSD":
            return round(requests.get("https://api.gold-api.com/price/XAU", timeout=5).json().get("price",0),2)
        if tv == "EURUSD":
            return round(yf.Ticker("EURUSD=X").fast_info.last_price,5)
        if tv == "GBPUSD":
            return round(yf.Ticker("GBPUSD=X").fast_info.last_price,5)
        if tv == "NAS100":
            return round(yf.Ticker("^NDX").fast_info.last_price,2)
        if tv == "US30":
            return round(yf.Ticker("^DJI").fast_info.last_price,2)
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
    if not is_allowed(u.effective_user.id): await u.message.reply_text(f"ID: {u.effective_user.id}"); return
    await u.message.reply_text("✅ Fr Bot Ready\n/testfr = test alert\n/status = live data", reply_markup=menu(u.effective_user.id))

async def testfr(u,c):
    if not is_allowed(u.effective_user.id): return
    await u.message.reply_text(f"🚨 XAUUSD\nFr buy (TEST)\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}")

async def status(u,c):
    if not is_allowed(u.effective_user.id): return
    txt = "📊 LIVE STATUS\n\n"
    for n,cfg in PAIRS.items():
        h4 = get_data(cfg["tv"], '4h'); h1 = get_data(cfg["tv"], '1h'); m5 = get_data(cfg["tv"], '5m')
        b4 = "BUY" if bias_htf(h4)==1 else "SELL" if bias_htf(h4)==-1 else "—"
        b1 = "BUY" if bias_htf(h1)==1 else "SELL" if bias_htf(h1)==-1 else "—"
        if m5 is not None and len(m5)>2:
            h = m5['High'].iloc[-6:-1].max(); l = m5['Low'].iloc[-6:-1].min()
            txt += f"{n}: 4H {b4} | 1H {b1} | 5m {h:.2f}/{l:.2f}\n"
        else:
            txt += f"{n}: no data\n"
    await u.message.reply_text(txt)

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    print("Bot started")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)