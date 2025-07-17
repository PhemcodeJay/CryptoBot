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
INTERVALS = ['15', '60', '240']
MAX_SYMBOLS = 100
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1392538366570135594/lwRHMnC6D1nBMnpXhxf_bdMO0PacPIZxh7-yuKv1OvQm_WhagXAwmCUR9oRpU9sIg4WH"  # Optional

tz_utc3 = timezone(timedelta(hours=3))

# === DISCORD NOTIFY ===
def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except:
        pass

# === INDICATORS ===
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
    if len(prices) < period:
        return None
    mult = 2/(period+1)
    val = sum(prices[:period])/period
    for p in prices[period:]:
        val = (p-val)*mult + val
    return val

def sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def rsi(prices, period=14):
    if len(prices) < period+1:
        return None
    gains = [max(prices[i]-prices[i-1],0) for i in range(1,period+1)]
    losses = [max(prices[i-1]-prices[i],0) for i in range(1,period+1)]
    ag, al = sum(gains)/period, sum(losses)/period
    rs = ag/(al+1e-10)
    return 100 - (100/(1+rs))

def bollinger(prices, period=20, sd=2):
    mid = sma(prices, period)
    if mid is None:
        return None, None, None
    var = sum((p-mid)**2 for p in prices[-period:]) / period
    std = var**0.5
    return mid + sd*std, mid, mid - sd*std

def atr(highs, lows, closes, period=14):
    if len(highs) < period+1:
        return None
    trs = [max(h-l, abs(h-c), abs(l-c)) for h, l, c in zip(highs[1:], lows[1:], closes[:-1])]
    val = sum(trs[:period]) / period
    for t in trs[period:]:
        val = (val*(period-1)+t)/period
    return val

def macd(prices):
    fast = ema(prices, 12)
    slow = ema(prices, 26)
    if fast is None or slow is None:
        return None
    return fast - slow

# === TREND ===
def classify_trend(ema9, ema21, sma20):
    if ema9 > ema21 > sma20:
        return "Trend"
    if ema9 > ema21:
        return "Swing"
    return "Scalp"

# === SIGNAL LOGIC ===
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
            'rsi': rsi(closes, 14),
            'macd': macd(closes),
            'bb_up': bollinger(closes)[0],
            'bb_mid': bollinger(closes)[1],
            'bb_low': bollinger(closes)[2],
            'atr': atr(highs, lows, closes),
            'volume': volumes[-1]
        }

    tf60 = data['60']
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
    entry = min((e for e in opts if e[1]), key=lambda x: abs(x[1]-price))[1]

    side = 'LONG' if sides[0] == 'LONG' else 'SHORT'
    tp = round(entry * (1.015 if side == 'LONG' else 0.985), 6)
    sl = round(entry * (0.985 if side == 'LONG' else 1.015), 6)
    trail = round(entry * (1 - ENTRY_BUFFER_PCT) if side == 'LONG' else entry * (1 + ENTRY_BUFFER_PCT), 6)
    liq = round(entry * (1 - 1/LEVERAGE if side == 'LONG' else 1 + 1/LEVERAGE), 6)
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

# === PDF ===
class SignalPDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 10)
        self.cell(0, 10, "Bybit Futures Multi-TF Signals", 0, 1, "C")

    def add_signals(self, signals):
        for s in signals:
            # Bold symbol header
            self.set_font("Arial", "B", 8)
            self.cell(0, 5, f"==================== {s['Symbol']} ====================", ln=1)

            # TYPE
            self.set_font("Arial", "", 8)
            self.cell(45, 5, f"TYPE: {s['Type']}")

            # Color-coded SIDE
            if s['Side'] == "LONG":
                self.set_text_color(0, 150, 0)  # green
            else:
                self.set_text_color(200, 0, 0)  # red
            self.cell(40, 5, f"SIDE: {s['Side']}")

            # Reset color for SCORE
            self.set_text_color(0, 0, 0)
            self.cell(0, 5, f"SCORE: {s['Score']}%", ln=1)

            # Entry details
            self.cell(65, 5, f"ENTRY: {s['Entry']}")
            self.cell(65, 5, f"TP: {s['TP']}")
            self.cell(0, 5, f"SL: {s['SL']}", ln=1)

            # Market + BB direction + Trail
            self.cell(65, 5, f"MARKET: {s['Market']}")
            self.cell(65, 5, f"BB: {s['BB Slope']}")
            self.cell(0, 5, f"TRAIL: {s['Trail']}", ln=1)

            # Margin + Liq + Time
            self.cell(65, 5, f"MARGIN: {s['Margin']}")
            self.cell(65, 5, f"LIQ: {s['Liq']}")
            self.cell(0, 5, f"TIME: {s['Time']}", ln=1)

            # Footer separator
            self.set_font("Arial", "B", 8)
            self.cell(0, 5, "=" * 57, ln=1)
            self.ln(2)


# === MAIN ===
def get_usdt_symbols():
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers?category=linear")
        data = r.json()
        tickers = [i for i in data['result']['list'] if i['symbol'].endswith("USDT")]
        top_by_volume = sorted(tickers, key=lambda x: float(x['turnover24h']), reverse=True)
        return [x['symbol'] for x in top_by_volume[:MAX_SYMBOLS]]
    except:
        return []

def main():
    while True:
        print("\n🔍 Scanning Bybit USDT Futures for filtered signals...\n")
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

            pdf = SignalPDF()
            pdf.add_page()
            pdf.add_signals(signals[:20])
            fname = f"signals_{datetime.now(tz_utc3).strftime('%H%M')}.pdf"
            pdf.output(fname)
            print(f"📄 PDF saved: {fname}")
            print("♻️ Rescanning in 15 minutes...\n")

            # Discord Notification
            top_msg = "\n\n".join([
                f"""==================== {s['Symbol']} ====================
📊 TYPE: {s['Type']}     📈 SIDE: {s['Side']}     🏆 SCORE: {s['Score']}%
💵 ENTRY: {s['Entry']}   🎯 TP: {s['TP']}         🛡️ SL: {s['SL']}
💱 MARKET: {s['Market']} 📍 BB: {s['BB Slope']}    🔄 Trail: {s['Trail']}
⚖️ MARGIN: {s['Margin']} ⚠️ LIQ: {s['Liq']}
⏰ TIME: {s['Time']}
========================================================="""
                for s in top5
            ])
            send_discord(f"📊 **Top 5 Bybit Signals**\n\n{top_msg}")
            print("♻️ Message Sent to Discord...\n")

        else:
            print("⚠️ No valid signals found")
            print("♻️ Rescanning in 15 minutes...\n")

        sleep(900)

if __name__ == "__main__":
    main()
