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
            r = requests.get("https://api.gold-api.com/price/XAU", timeout=5)
            return round(r.json().get("price", 0), 2)
        if tv == "EURUSD":
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            return round(1 / r.json()["rates"]["EUR"], 5)
        if tv == "GBPUSD":
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            return round(1 / r.json()["rates"]["GBP"], 5)

        if tv == "NAS100":
            # 1) Stooq (fastest)
            try:
                r = requests.get("https://stooq.com/q/l/?s=^ndx&f=c", timeout=4)
                p = float(r.text.strip())
                if 10000 < p < 30000: return round(p, 2)
            except: pass
            # 2) Yahoo
            try:
                r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX?range=1d&interval=1m",
                                headers={"User-Agent":"Mozilla/5.0"}, timeout=4)
                p = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if 10000 < p < 30000: return round(p, 2)
            except: pass
            # 3) TwelveData
            try:
                r = requests.get("https://api.twelvedata.com/price?symbol=NDX&exchange=NASDAQ&apikey=demo", timeout=4)
                p = float(r.json()["price"])
                return round(p, 2)
            except: pass

        if tv == "US30":
            # 1) Stooq
            try:
                r = requests.get("https://stooq.com/q/l/?s=^dji&f=c", timeout=4)
                p = float(r.text.strip())
                if 20000 < p < 60000: return round(p, 2)
            except: pass
            # 2) Yahoo
            try:
                r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EDJI?range=1d&interval=1m",
                                headers={"User-Agent":"Mozilla/5.0"}, timeout=4)
                p = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if 20000 < p < 60000: return round(p, 2)
            except: pass
            # 3) TwelveData
            try:
                r = requests.get("https://api.twelvedata.com/price?symbol=DJI&exchange=DJI&apikey=demo", timeout=4)
                p = float(r.json()["price"])
                return round(p, 2)
            except: pass

    except Exception as e:
        print(f"Price err {tv}: {e}")
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
    await u.message.reply_text("✅ Fr Bot Ready", reply_markup=menu(u.effective_user.id))

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    threading.Thread(target=lambda: asyncio.run(scanner(app)), daemon=True).start()
    print("Bot started")
    app.run_polling(drop_pending_updates=True)