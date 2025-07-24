import requests
from fpdf import FPDF
from datetime import datetime, timedelta, timezone
from time import sleep
import pytz

# === CONFIGURATION ===
RISK_PCT = 0.15
ACCOUNT_BALANCE = 100
LEVERAGE = 20
ENTRY_BUFFER_PCT = 0.002
MIN_VOLUME = 1000
MIN_ATR_PCT = 0.001
RSI_ZONE = (20, 80)
INTERVALS = ['15m', '1h', '4h']
MAX_SYMBOLS = 100
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1392538366570135594/lwRHMnC6D1nBMnpXhxf_bdMO0PacPIZxh7-yuKv1OvQm_WhagXAwmCUR9oRpU9sIg4WH"
TELEGRAM_BOT_TOKEN = "8160938302:AAFUmPahGk14OY8F1v5FLHGoVRD-pGTvSOY"
TELEGRAM_CHAT_ID = "5852301284"

tz_utc3 = timezone(timedelta(hours=3))

# === DISCORD / TELEGRAM NOTIFY ===
def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        print(f"Discord error: {e}")

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

# === BYBIT CANDLES FETCH ===
def get_candles(sym, interval):
    interval_map = {
        "15m": "15",
        "1h": "60",
        "4h": "240"
    }
    bybit_interval = interval_map.get(interval, "60")
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval={bybit_interval}&limit=200"
    try:
        res = requests.get(url)
        data = res.json().get('result', {}).get('list', [])
        return [{
            'high': float(c[3]),
            'low': float(c[4]),
            'close': float(c[5]),
            'volume': float(c[6])
        } for c in data]
    except Exception as e:
        print(f"Failed to fetch candles for {sym} ({interval}): {e}")
        return []

# === INDICATORS ===
def ema(prices, period):
    if len(prices) < period:
        return None
    mult = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = (p - val) * mult + val
    return val

def sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains = [max(prices[i] - prices[i - 1], 0) for i in range(1, period + 1)]
    losses = [max(prices[i - 1] - prices[i], 0) for i in range(1, period + 1)]
    ag, al = sum(gains) / period, sum(losses) / period
    rs = ag / (al + 1e-10)
    return 100 - (100 / (1 + rs))

def bollinger(prices, period=20, sd=2):
    mid = sma(prices, period)
    if mid is None:
        return None, None, None
    var = sum((p - mid) ** 2 for p in prices[-period:]) / period
    std = var ** 0.5
    return mid + sd * std, mid, mid - sd * std

def atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    trs = [max(h - l, abs(h - c), abs(l - c)) for h, l, c in zip(highs[1:], lows[1:], closes[:-1])]
    val = sum(trs[:period]) / period
    for t in trs[period:]:
        val = (val * (period - 1) + t) / period
    return val

def macd(prices):
    fast = ema(prices, 12)
    slow = ema(prices, 26)
    if fast is None or slow is None:
        return None
    return fast - slow

def classify_trend(ema9, ema21, sma20):
    if ema9 > ema21 > sma20:
        return "Trend"
    if ema9 > ema21:
        return "Swing"
    return "Scalp"

# === ANALYZE ONE SYMBOL ===
def analyze(symbol):
    data = {}
    for tf in INTERVALS:
        candles = get_candles(symbol, tf)
        if len(candles) < 30:
            return None
        closes = [c['close'] for c in candles]
        volumes = [c['volume'] for c in candles]
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]

        data[tf] = {
            'close': closes[-1],
            'ema9': ema(closes, 9),
            'ema21': ema(closes, 21),
            'sma20': sma(closes, 20),
            'rsi': rsi(closes),
            'macd': macd(closes),
            'bb_up': bollinger(closes)[0],
            'bb_mid': bollinger(closes)[1],
            'bb_low': bollinger(closes)[2],
            'atr': atr(highs, lows, closes),
            'volume': volumes[-1]
        }

    tf60 = data['1h']
    if (tf60['volume'] < MIN_VOLUME or
        tf60['atr'] / tf60['close'] < MIN_ATR_PCT or
        not (RSI_ZONE[0] < tf60['rsi'] < RSI_ZONE[1])):
        return None

    sides = []
    for d in data.values():
        if d['close'] > d['bb_up']:
            sides.append('LONG')
        elif d['close'] < d['bb_low']:
            sides.append('SHORT')
        elif d['close'] > d['ema21']:
            sides.append('LONG')
        elif d['close'] < d['ema21']:
            sides.append('SHORT')

    if len(set(sides)) != 1:
        return None

    tf = tf60
    price = tf['close']
    trend = classify_trend(tf['ema9'], tf['ema21'], tf['sma20'])
    bb_dir = "Up" if price > tf['bb_up'] else "Down" if price < tf['bb_low'] else "No"

    opts = [('sma20', tf['sma20']), ('ema9', tf['ema9']), ('ema21', tf['ema21'])]
    entry = min((e for e in opts if e[1]), key=lambda x: abs(x[1] - price))[1]

    side = 'LONG' if sides[0] == 'LONG' else 'SHORT'
    tp = round(entry * (1.015 if side == 'LONG' else 0.985), 6)
    sl = round(entry * (0.985 if side == 'LONG' else 1.015), 6)
    trail = round(entry * (1 - ENTRY_BUFFER_PCT) if side == 'LONG' else entry * (1 + ENTRY_BUFFER_PCT), 6)
    liq = round(entry * (1 - 1 / LEVERAGE) if side == 'LONG' else entry * (1 + 1 / LEVERAGE), 6)
    margin = round((ACCOUNT_BALANCE * RISK_PCT) / LEVERAGE, 6)

    score = 0
    score += 0.3 if tf['macd'] > 0 else 0
    score += 0.2 if tf['rsi'] < 30 or tf['rsi'] > 70 else 0
    score += 0.3 if bb_dir != "No" else 0.1
    score += 0.2 if trend == "Trend" else 0.1

    return {
        'Symbol': symbol,
        'Side': side,
        'Type': trend,
        'Score': round(score * 100, 1),
        'Entry': round(entry, 6),
        'TP': tp,
        'SL': sl,
        'Trail': trail,
        'Margin': margin,
        'Market': price,
        'Liq': liq,
        'BB Slope': bb_dir,
        'Time': datetime.now(tz_utc3).strftime("%Y-%m-%d %H:%M UTC+3")
    }

# === GET BYBIT SYMBOLS ===
def get_usdt_symbols():
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        res = requests.get(url)
        data = res.json().get('result', {}).get('list', [])
        tickers = [x for x in data if x['symbol'].endswith("USDT")]
        sorted_tickers = sorted(tickers, key=lambda x: float(x['turnover24h']), reverse=True)
        return [x['symbol'] for x in sorted_tickers[:MAX_SYMBOLS]]
    except Exception as e:
        print(f"Failed to get USDT symbols: {e}")
        return []

# === MAIN LOOP ===
def main():
    while True:
        print("\n🔍 Scanning Bybit USDT Perpetuals for filtered signals...\n")
        syms = get_usdt_symbols()
        signals = []

        for sym in syms:
            sig = analyze(sym)
            if sig:
                signals.append(sig)
            sleep(0.3)

        if signals:
            signals.sort(key=lambda x: x['Score'], reverse=True)
            top5 = signals[:5]

            for s in top5:
                print(f"""
==================== {s['Symbol']} ====================
📊 TYPE: {s['Type']}     📈 SIDE: {s['Side']}     🏆 SCORE: {s['Score']}%
💵 ENTRY: {s['Entry']}   🎯 TP: {s['TP']}         🛡️ SL: {s['SL']}
💱 MARKET: {s['Market']} 📍 BB: {s['BB Slope']}    🔄 Trail: {s['Trail']}
⚖️ MARGIN: {s['Margin']} ⚠️ LIQ: {s['Liq']}
⏰ TIME: {s['Time']}
=========================================================
""")
            top_msg = "\n\n".join([f"""
==================== {s['Symbol']} ====================
📊 TYPE: {s['Type']}     📈 SIDE: {s['Side']}     🏆 SCORE: {s['Score']}%
💵 ENTRY: {s['Entry']}   🎯 TP: {s['TP']}         🛡️ SL: {s['SL']}
💱 MARKET: {s['Market']} 📍 BB: {s['BB Slope']}    🔄 Trail: {s['Trail']}
⚖️ MARGIN: {s['Margin']} ⚠️ LIQ: {s['Liq']}
⏰ TIME: {s['Time']}
=========================================================""" for s in top5])

            send_discord(f"📊 **Latest Signals**\n\n{top_msg}")
            send_telegram(f"📊 **Latest Signals**\n\n{top_msg}")
            print("♻️ Signals Sent to Discord & Telegram...\n")
        else:
            print("⚠️ No valid signals found")

        print("♻️ Rescanning in 15 minutes...\n")
        sleep(900)

if __name__ == "__main__":
    main()
