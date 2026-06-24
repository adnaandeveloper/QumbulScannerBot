import os, asyncio, yfinance as yf, pandas as pd
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# === CONFIG ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT", "0"))

# Default pairs + Yahoo symbols + SMT correlations
PAIRS = {
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

last_signals = {}

def get_data(symbol, interval, period="7d"):
    try:
        df = yf.download(symbol, interval=interval, period=period, progress=False, prepost=True)
        if df.empty: return None
        df = df.dropna()
        return df
    except: return None

def bias_daily(df):
    if df is None or len(df) < 2: return 0
    pdh = df['High'].iloc[-2]; pdl = df['Low'].iloc[-2]; close = df['Close'].iloc[-1]
    return 1 if close > pdh else -1 if close < pdl else 0

def bias_htf(df):
    if df is None or len(df) < 2: return 0
    c = df['Close'].iloc[-1]; ph = df['High'].iloc[-2]; pl = df['Low'].iloc[-2]
    return 1 if c > ph else -1 if c < pl else 0

def cisd_state(df):
    if df is None or len(df) < 6: return "—"
    high5 = df['High'].iloc[-6:-1].max(); low5 = df['Low'].iloc[-6:-1].min(); c = df['Close'].iloc[-1]
    if c > high5: return "BUY ✓"
    if c < low5: return "SELL ✓"
    return "—"

def check_pair(name, cfg):
    sym = cfg["yf"]
    d1 = get_data(sym, "1d", "30d")
    h4 = get_data(sym, "60m", "30d")
    if h4 is not None: h4 = h4.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
    h1 = get_data(sym, "60m", "7d")
    m15 = get_data(sym, "15m", "2d")
    m5 = get_data(sym, "5m", "2d")

    daily = bias_daily(d1)
    b4 = bias_htf(h4); b1 = bias_htf(h1)
    s15 = cisd_state(m15); s5 = cisd_state(m5)

    # Fractal logic (same as your Pine)
    f_buy = b1 == 1 and b1 == b4 and s5 == "BUY ✓"
    f_sell = b1 == -1 and b1 == b4 and s5 == "SELL ✓"

    signal = "FRACTAL BUY" if f_buy else "FRACTAL SELL" if f_sell else None
    if not signal: return None

    # avoid repeats
    key = f"{name}_{signal}"
    if last_signals.get(key) == True: return None
    last_signals[key] = True

    msg = f"🚨 {name}\n{signal}\n\n4H: {'BUY ✓' if b4==1 else 'SELL ✓' if b4==-1 else '—'}\n1H: {'BUY ✓' if b1==1 else 'SELL ✓' if b1==-1 else '—'}\n15m: {s15}\n5m: {s5}\n\nDaily: {'BUY' if daily==1 else 'SELL' if daily==-1 else 'NEUTRAL'}\nTime: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    return msg

async def scanner(app: Application):
    while True:
        for name, cfg in PAIRS.items():
            try:
                msg = check_pair(name, cfg)
                if msg and CHAT_ID:
                    await app.bot.send_message(CHAT_ID, msg)
            except Exception as e:
                print(f"Error {name}: {e}")
        await asyncio.sleep(60)

# === TELEGRAM COMMANDS ===
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Qumbul Scanner online ✅\n/add SYMBOL, /remove SYMBOL, /list, /setcorr SYMBOL CORR")

async def add_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 1: return
    sym = ctx.args[0].upper()
    yf_sym = ctx.args[1] if len(ctx.args)>1 else sym+"=X"
    corr = ctx.args[2] if len(ctx.args)>2 else ""
    PAIRS[sym] = {"yf": yf_sym, "corr": corr}
    await update.message.reply_text(f"Added {sym} → {yf_sym}")

async def remove_pair(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = ctx.args[0].upper()
    PAIRS.pop(sym, None)
    await update.message.reply_text(f"Removed {sym}")

async def list_pairs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = "\n".join([f"{k} → {v['yf']} (corr: {v['corr']})" for k,v in PAIRS.items()])
    await update.message.reply_text(txt or "No pairs")

async def set_corr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym, corr = ctx.args[0].upper(), ctx.args[1]
    if sym in PAIRS: PAIRS[sym]["corr"] = corr
    await update.message.reply_text(f"{sym} corr set to {corr}")

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_pair))
    app.add_handler(CommandHandler("remove", remove_pair))
    app.add_handler(CommandHandler("list", list_pairs))
    app.add_handler(CommandHandler("setcorr", set_corr))
    asyncio.create_task(scanner(app))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
