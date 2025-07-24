import requests
from fpdf import FPDF
from datetime import datetime, timedelta, timezone
from time import sleep
import pytz
import sys

# === CONFIGURATION ===
RISK_PCT = 0.015
ACCOUNT_BALANCE = 100
LEVERAGE = 20
ENTRY_BUFFER_PCT = 0.002
MIN_VOLUME = 1000
MIN_ATR_PCT = 0.001
RSI_ZONE = (20, 80)
INTERVALS = ['15', '60', '240']
MAX_SYMBOLS = 100

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1392538366570135594/lwRHMnC6D1nBMnpXhxf_bdMO0PacPIZxh7-yuKv1OvQm_WhagXAwmCUR9oRpU9sIg4WH"
TELEGRAM_BOT_TOKEN = "8160938302:AAFUmPahGk14OY8F1v5FLHGoVRD-pGTvSOY"
TELEGRAM_CHAT_ID = "5852301284"

tz_utc3 = timezone(timedelta(hours=3))

# === NOTIFICATIONS ===
def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except:
        pass

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        })
    except:
        pass

# === PDF GENERATOR ===
class SignalPDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 10)
        self.cell(0, 10, "Bybit Futures Multi-TF Signals", 0, 1, "C")

    def add_signals(self, signals):
        self.set_font("Courier", size=8)
        for s in signals:
            self.set_text_color(0, 0, 0)
            self.set_font("Courier", "B", 8)
            self.cell(0, 5, f"==================== {s['Symbol']} ====================", ln=1)

            self.set_font("Courier", "", 8)
            self.set_text_color(0, 0, 139)  # Dark blue
            self.cell(0, 4, f"TYPE: {s['Type']}    SIDE: {s['Side']}     SCORE: {s['Score']}%", ln=1)

            self.set_text_color(34, 139, 34)  # Forest green
            self.cell(0, 4, f"ENTRY: {s['Entry']}   TP: {s['TP']}         SL: {s['SL']}", ln=1)

            self.set_text_color(139, 0, 0)  # Dark red
            self.cell(0, 4, f"MARKET: {s['Market']}  BB: {s['BB Slope']}    Trail: {s['Trail']}", ln=1)

            self.set_text_color(0, 100, 100)  # Teal
            self.cell(0, 4, f"MARGIN: {s['Margin']}  LIQ: {s['Liq']}    TIME: {s['Time']}", ln=1)

            self.set_text_color(0, 0, 0)
            self.cell(0, 4, "=" * 57, ln=1)
            self.ln(1)

# === FORMATTER ===
def format_signal_block(s):
    return (
        f"==================== {s['Symbol']} ====================\n"
        f"📊 TYPE: {s['Type']}     📈 SIDE: {s['Side']}     🏆 SCORE: {s['Score']}%\n"
        f"💵 ENTRY: {s['Entry']}   🎯 TP: {s['TP']}         🛡️ SL: {s['SL']}\n"
        f"💱 MARKET: {s['Market']} 📍 BB: {s['BB Slope']}    🔄 Trail: {s['Trail']}\n"
        f"⚖️ MARGIN: {s['Margin']} ⚠️ LIQ: {s['Liq']}\n"
        f"⏰ TIME: {s['Time']}\n"
        "=========================================================\n"
    )

# === INDICATOR FUNCTIONS ===
def get_candles(sym, interval):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit=200"
    try:
        data = requests.get(url).json()
        return [{
            'high': float(c[2]),
            'low': float(c[3]),
            'close': float(c[4]),
            'volume': float(c[5])
        } for c in reversed(data['result']['list'])]
    except:
        return []

def ema(prices, period):
    if len(prices) < period: return None
    mult = 2/(period+1)
    val = sum(prices[:period])/period
    for p in prices[period:]:
        val = (p-val)*mult + val
    return val

def sma(prices, period):
    if len(prices) < period: return None
    return sum(prices[-period:]) / period

def rsi(prices, period=14):
    if len(prices) < period+1: return None
    gains = [max(prices[i]-prices[i-1],0) for i in range(1,period+1)]
    losses = [max(prices[i-1]-prices[i],0) for i in range(1,period+1)]
    ag, al = sum(gains)/period, sum(losses)/period
    rs = ag/(al+1e-10)
    return 100 - (100/(1+rs))

def bollinger(prices, period=20, sd=2):
    mid = sma(prices, period)
    if mid is None: return None, None, None
    var = sum((p-mid)**2 for p in prices[-period:]) / period
    std = var**0.5
    return mid+sd*std, mid, mid-sd*std

def atr(highs, lows, closes, period=14):
    if len(highs) < period+1: return None
    trs = [max(h-l, abs(h-c), abs(l-c)) for h,l,c in zip(highs[1:], lows[1:], closes[:-1])]
    val = sum(trs[:period]) / period
    for t in trs[period:]:
        val = (val*(period-1)+t)/period
    return val

def macd(prices):
    fast = ema(prices,12)
    slow = ema(prices,26)
    return fast-slow if fast and slow else None

def classify_trend(e9,e21,s20):
    if e9>e21>s20: return "Trend"
    if e9>e21: return "Swing"
    return "Scalp"

# === SIGNAL LOGIC ===
def analyze(symbol):
    data = {}
    for tf in INTERVALS:
        candles = get_candles(symbol, tf)
        if len(candles)<30: return None
        closes = [c['close'] for c in candles]
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        vols = [c['volume'] for c in candles]
        data[tf] = {
            'close': closes[-1],
            'ema9': ema(closes,9),
            'ema21': ema(closes,21),
            'sma20': sma(closes,20),
            'rsi': rsi(closes),
            'macd': macd(closes),
            'bb_up': bollinger(closes)[0],
            'bb_mid': bollinger(closes)[1],
            'bb_low': bollinger(closes)[2],
            'atr': atr(highs,lows,closes),
            'volume': vols[-1]
        }

    tf60 = data['60']
    if (tf60['volume']<MIN_VOLUME or tf60['atr']/tf60['close']<MIN_ATR_PCT or
        not (RSI_ZONE[0]<tf60['rsi']<RSI_ZONE[1])):
        return None

    sides=[]
    for d in data.values():
        if d['close']>d['bb_up']: sides.append('LONG')
        elif d['close']<d['bb_low']: sides.append('SHORT')
        elif d['close']>d['ema21']: sides.append('LONG')
        elif d['close']<d['ema21']: sides.append('SHORT')

    if len(set(sides))!=1: return None

    tf=tf60
    price=tf['close']
    trend=classify_trend(tf['ema9'],tf['ema21'],tf['sma20'])
    bb_dir="Up" if price>tf['bb_up'] else "Down" if price<tf['bb_low'] else "No"
    opts=[tf['sma20'],tf['ema9'],tf['ema21']]
    entry=min(opts, key=lambda x: abs(x-price))

    side = 'LONG' if sides[0]=='LONG' else 'SHORT'
    tp = round(entry*(1.015 if side=='LONG' else 0.985),6)
    sl = round(entry*(0.985 if side=='LONG' else 1.015),6)
    trail = round(entry*(1-ENTRY_BUFFER_PCT) if side=='LONG' else entry*(1+ENTRY_BUFFER_PCT),6)
    liq = round(entry*(1 - 1/LEVERAGE) if side=='LONG' else entry*(1 + 1/LEVERAGE),6)
    try:
        risk_amt = ACCOUNT_BALANCE * RISK_PCT
        sl_diff = abs(entry - sl)
        margin = round((risk_amt/sl_diff)*entry/LEVERAGE,6)
    except:
        margin=1

    score = 0
    score += 0.3 if tf['macd']>0 else 0
    score += 0.2 if tf['rsi']<30 or tf['rsi']>70 else 0
    score += 0.3 if bb_dir!="No" else 0.1
    score += 0.2 if trend=="Trend" else 0.1

    return {
        'Symbol': symbol,
        'Side': side,
        'Type': trend,
        'Score': round(score*100,1),
        'Entry': round(entry,6),
        'TP': tp,
        'SL': sl,
        'Trail': trail,
        'Margin': margin,
        'Market': price,
        'Liq': liq,
        'BB Slope': bb_dir,
        'Time': datetime.now(tz_utc3).strftime("%Y-%m-%d %H:%M UTC+3")
    }

# === FETCH SYMBOLS ===
def get_usdt_symbols():
    try:
        data = requests.get("https://api.bybit.com/v5/market/tickers?category=linear").json()
        tickers = [i for i in data['result']['list'] if i['symbol'].endswith("USDT")]
        tickers.sort(key=lambda x: float(x['turnover24h']), reverse=True)
        return [t['symbol'] for t in tickers[:MAX_SYMBOLS]]
    except:
        return []

# === MAIN LOOP ===
def main():
    while True:
        print("\n🔍 Scanning Bybit USDT Futures for filtered signals...\n")
        symbols = get_usdt_symbols()
        signals = [analyze(s) for s in symbols]
        signals = [s for s in signals if s]

        if signals:
            signals.sort(key=lambda x: x['Score'], reverse=True)
            top5 = signals[:5]
            blocks = [format_signal_block(s) for s in top5]
            agg_msg = "\n".join(blocks)

            # Terminal
            for blk in blocks:
                print(blk)

            # PDF
            pdf = SignalPDF()
            pdf.add_page()
            pdf.add_signals(signals[:20])
            fname=f"signals_{datetime.now(tz_utc3).strftime('%H%M')}.pdf"
            pdf.output(fname)
            print(f"📄 PDF saved: {fname}")

            # Notifications
            send_discord("📊 **Top 5 Bybit Signals**\n\n" + agg_msg)
            send_telegram("📊 *Top 5 Bybit Signals*\n\n" + agg_msg)
            print("✅ Notifications sent to Discord & Telegram.\n")
        else:
            print("⚠️ No valid signals found\n")

        # Countdown
        wait = 900
        print("⏳ Rescanning in 15 minutes...")
        for i in range(wait,0,-1):
            sys.stdout.write(f"\r⏱️  Next scan in {i//60:02d}:{i%60:02d}")
            sys.stdout.flush()
            sleep(1)
        print()

if __name__ == "__main__":
    main()
