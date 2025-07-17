# === Bybit Signal Bot with Fibonacci, Volume Confirmation, MA20 Limit Orders ===
import requests
from datetime import datetime, timezone, timedelta
from fpdf import FPDF

RISK_AMOUNT = 10
LEVERAGE = 20
TP_PERCENT = 0.30
SL_PERCENT = 0.10

def ema(values, period):
    emas, k = [], 2 / (period + 1)
    if len(values) < period: return [None] * len(values)
    ema_prev = sum(values[:period]) / period
    emas.append(ema_prev)
    for price in values[period:]:
        ema_prev = price * k + ema_prev * (1 - k)
        emas.append(ema_prev)
    return [None] * (period - 1) + emas

def sma(values, period):
    return [None if i < period - 1 else sum(values[i+1-period:i+1]) / period for i in range(len(values))]

def compute_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    if len(gains) < period: return 50
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_macd(values, fast=12, slow=26, signal=9):
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [f - s if f and s else None for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema([x for x in macd_line if x is not None], signal)
    signal_line = [None] * (len(macd_line) - len(signal_line)) + signal_line
    hist = [m - s if m and s else None for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, hist

def calculate_bollinger_bands(values, period=20, std_dev=2):
    sma_vals = sma(values, period)
    bands = []
    for i in range(len(values)):
        if i < period - 1:
            bands.append((None, None, None))
        else:
            mean = sma_vals[i]
            std = (sum((x - mean) ** 2 for x in values[i + 1 - period:i + 1]) / period) ** 0.5
            upper = mean + std_dev * std
            lower = mean - std_dev * std
            bands.append((upper, mean, lower))
    return bands

def calculate_atr(highs, lows, closes, period=14):
    trs = [max(h - l, abs(h - c), abs(l - c)) for h, l, c in zip(highs[1:], lows[1:], closes[:-1])]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        yield atr

def zscore(series, period=20):
    if len(series) < period: return 0
    mean = sum(series[-period:]) / period
    std = (sum((x - mean) ** 2 for x in series[-period:]) / period) ** 0.5
    return (series[-1] - mean) / std if std != 0 else 0

def calculate_fib_levels(high, low, side):
    diff = high - low
    return {
        "tp": round(high + diff * 0.618, 4) if side == "LONG" else round(low - diff * 0.618, 4),
        "sl": round(high - diff * 0.382, 4) if side == "LONG" else round(low + diff * 0.382, 4)
    }

def fetch_orderbook_strength(symbol):
    try:
        url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=50"
        res = requests.get(url).json()
        asks, bids = res["result"]["asks"], res["result"]["bids"]
        buy_vol = sum(float(b[1]) for b in bids)
        sell_vol = sum(float(a[1]) for a in asks)
        imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol) if buy_vol + sell_vol else 0
        return "buy" if imbalance > 0.1 else "sell" if imbalance < -0.1 else "neutral"
    except: return "neutral"

def fetch_24h_change(symbol):
    try:
        url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
        data = requests.get(url).json()
        return float(data["result"]["list"][0]["change24hPcnt"])
    except: return 0

def detect_trend(symbol):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=60&limit=50"
    try:
        closes = [float(x[4]) for x in requests.get(url).json()["result"]["list"]]
        return "bullish" if closes[-1] > sma(closes, 50)[-1] else "bearish"
    except:
        return "neutral"

def fetch_ohlcv(symbol, interval='60', limit=100):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    r = requests.get(url)
    return [[float(x[2]), float(x[3]), float(x[4]), float(x[5]), float(x[1])] for x in r.json()["result"]["list"][::-1]]

def analyze(symbol):
    data = fetch_ohlcv(symbol)
    highs, lows, closes, volumes, opens = zip(*data)
    close = closes[-1]

    ma20 = sma(closes, 20)
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    bb_upper, bb_mid, bb_lower = zip(*calculate_bollinger_bands(closes))
    atr_series = list(calculate_atr(highs, lows, closes))
    atr = atr_series[-1] if atr_series else 0
    rsi = compute_rsi(closes)
    volume_ma = sum(volumes[-20:]) / 20
    volume_spike = volumes[-1] > volume_ma * 1.5
    bb_breakout = "UP" if close > bb_upper[-1] else "DOWN" if close < bb_lower[-1] else "NO"
    trend = detect_trend(symbol)
    change_24h = fetch_24h_change(symbol)
    orderbook_bias = fetch_orderbook_strength(symbol)

    if not volume_spike or bb_breakout == "NO":
        return []

    side = "LONG" if bb_breakout == "UP" else "SHORT"
    if (side == "LONG" and change_24h < 1.0) or (side == "SHORT" and change_24h > -1.0):
        return []

    entry = ma20[-1]
    fib = calculate_fib_levels(max(highs[-20:]), min(lows[-20:]), side)
    sl, tp = fib["sl"], fib["tp"]
    size = round(RISK_AMOUNT / abs(entry - sl), 4)

    return [{
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rsi": rsi,
        "macd_hist": macd_hist[-1] if macd_hist else 0,
        "bb_breakout": bb_breakout,
        "trend": trend,
        "position_size": size,
        "volume_spike": volume_spike,
        "orderbook_bias": orderbook_bias,
        "score": round((rsi / 100 + abs(macd_hist[-1])) * 10, 2),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    }]

class PDFReport(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 10, "Top Bybit Signals", ln=True, align="C")
        self.ln(5)

    def add_signal(self, s, idx):
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 10, f"{idx}. {s['symbol']} ({s['side']})", ln=True)
        self.set_font("Helvetica", "", 10)
        lines = [
            f"Entry: {s['entry']} | TP: {s['tp']} | SL: {s['sl']}",
            f"RSI: {s['rsi']} | MACD Hist: {s['macd_hist']}",
            f"BB Breakout: {s['bb_breakout']} | Trend: {s['trend']}",
            f"OB Bias: {s['orderbook_bias']} | Vol Spike: {'Yes' if s['volume_spike'] else 'No'}",
            f"Size: {s['position_size']} | Score: {s['score']} | Time: {s['timestamp']}"
        ]
        for line in lines:
            self.multi_cell(0, 8, line)
        self.ln(3)

def export_to_pdf(signals):
    pdf = PDFReport()
    pdf.add_page()
    for i, sig in enumerate(signals, 1):
        pdf.add_signal(sig, i)
    pdf.output("bybit_signals.pdf")
    print("✅ PDF saved as bybit_signals.pdf")

def get_symbols(limit=50):
    try:
        url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
        data = requests.get(url).json()
        return [s['symbol'] for s in data["result"]["list"] if s["symbol"].endswith("USDT")][:limit]
    except:
        return []

def main():
    print("🔍 Scanning symbols...")
    all_signals = []
    for symbol in get_symbols():
        sigs = analyze(symbol)
        if sigs:
            print(f"✅ {symbol}: {len(sigs)} signal(s)")
            all_signals.extend(sigs)

    all_signals = sorted(all_signals, key=lambda x: x['score'], reverse=True)[:5]
    if all_signals:
        export_to_pdf(all_signals)
    else:
        print("⚠️ No high-quality signals found.")

if __name__ == "__main__":
    main()
