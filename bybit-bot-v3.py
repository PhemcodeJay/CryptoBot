import requests
from datetime import datetime, timezone, timedelta
from fpdf import FPDF
from tabulate import tabulate

RISK_AMOUNT = 2
LEVERAGE = 20
TP_PERCENT = 0.25
SL_PERCENT = 0.10

# === Indicators ===
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
    macd_line = [f - s if f is not None and s is not None else None for f, s in zip(ema_fast, ema_slow)]
    valid_macd = [x for x in macd_line if x is not None]
    if len(valid_macd) < signal: return macd_line, [], []
    signal_line = ema(valid_macd, signal)
    signal_line = [None] * (len(macd_line) - len(signal_line)) + signal_line
    histogram = [m - s if m is not None and s is not None else None for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, histogram

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
    if len(trs) < period: return []
    atrs = []
    atr = sum(trs[:period]) / period
    atrs.append(atr)
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        atrs.append(atr)
    return [None] * (period + 1) + atrs

def zscore(series, period=20):
    if len(series) < period: return 0
    mean = sum(series[-period:]) / period
    std = (sum((x - mean) ** 2 for x in series[-period:]) / period) ** 0.5
    return (series[-1] - mean) / std if std != 0 else 0

# === Order Book Metrics ===
def fetch_orderbook_strength(symbol):
    try:
        url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=50"
        res = requests.get(url, timeout=5).json()
        asks = res["result"]["asks"]
        bids = res["result"]["bids"]
        buy_vol = sum(float(b[1]) for b in bids)
        sell_vol = sum(float(a[1]) for a in asks)
        imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol) if (buy_vol + sell_vol) != 0 else 0
        return {
            "buy_volume": buy_vol,
            "sell_volume": sell_vol,
            "imbalance": round(imbalance, 3),
            "bias": "buy" if imbalance > 0.1 else "sell" if imbalance < -0.1 else "neutral"
        }
    except:
        return {"buy_volume": 0, "sell_volume": 0, "imbalance": 0, "bias": "neutral"}

# === Trend Detection ===
def detect_market_trend(symbol):
    def fetch_closes(symbol, tf_code):
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={tf_code}&limit=60"
        try:
            r = requests.get(url, timeout=5)
            data = r.json().get("result", {}).get("list", [])
            return [float(x[4]) for x in data[::-1]]
        except:
            return []

    trend_info = {}
    timeframes = [('60', '1h'), ('240', '4h'), ('15', '15m')]
    for tf_code, label in timeframes:
        closes = fetch_closes(symbol, tf_code)
        if len(closes) < 50:
            trend_info[label] = 'neutral'
            continue
        ema9 = ema(closes, 9)[-1]
        ema21 = ema(closes, 21)[-1]
        ma200 = sma(closes, 50)[-1]
        close = closes[-1]
        if close > ma200 and ema9 > ema21:
            trend_info[label] = 'bullish'
        elif close < ma200 and ema9 < ema21:
            trend_info[label] = 'bearish'
        else:
            trend_info[label] = 'neutral'
    return trend_info

def is_trade_allowed(side, trend_info):
    trend_votes = list(trend_info.values())
    bull = trend_votes.count('bullish')
    bear = trend_votes.count('bearish')
    if bull > bear and side == 'SHORT': return False
    if bear > bull and side == 'LONG': return False
    return True

# === Score ===
def compute_score(s, trend_info):
    score = 0
    bull = list(trend_info.values()).count('bullish')
    bear = list(trend_info.values()).count('bearish')
    score += 20 if bull == 3 or bear == 3 else 10 if bull == 2 or bear == 2 else 0

    if s['side'] == 'LONG' and 50 < s['rsi'] < 65: score += 10
    elif s['side'] == 'SHORT' and 35 < s['rsi'] < 50: score += 10

    if s['macd_hist']:
        if s['macd_hist'] > 0 and s['side'] == 'LONG': score += 10
        elif s['macd_hist'] < 0 and s['side'] == 'SHORT': score += 10

    if s["bb_breakout"] in ["UP", "DOWN"]: score += 5
    if s.get("vol_spike"): score += 10
    if s.get("atr_z") and abs(s["atr_z"]) > 1.5: score += 10
    if s.get("atr") and s["atr"] > 0: score += 5
    if s.get("orderbook_bias") == "buy" and s["side"] == "LONG": score += 10
    if s.get("orderbook_bias") == "sell" and s["side"] == "SHORT": score += 10

    score += s["confidence"] * 0.4
    rr = TP_PERCENT / SL_PERCENT
    score += 10 if rr >= 2 else 5 if rr >= 1.5 else 0

    if trend_info.get('1h') == trend_info.get('4h') == trend_info.get('15m') == s['trend']:
        score += 10
    return round(score, 2)

# === Data Fetching Helpers ===
def fetch_ohlcv(symbol, interval='60', limit=100):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json().get("result", {}).get("list", [])
        return [[float(x[2]), float(x[3]), float(x[4]), float(x[5]), float(x[1])] for x in data[::-1]]
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        return []

def get_symbols(limit=100):
    try:
        r = requests.get("https://api.bybit.com/v5/market/instruments-info?category=linear", timeout=5)
        data = r.json().get("result", {}).get("list", [])
        return [s['symbol'] for s in data if s['symbol'].endswith('USDT')][:limit]
    except Exception as e:
        print(f"[ERROR] Symbols: {e}")
        return []

# === Main Signal Analysis ===
def analyze(symbol, tf="60"):
    data = fetch_ohlcv(symbol, tf)
    if len(data) < 60: return []

    highs = [x[0] for x in data]
    lows = [x[1] for x in data]
    closes = [x[2] for x in data]
    volumes = [x[3] for x in data]
    open_prices = [x[4] for x in data]
    close = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ma20 = sma(closes, 20)
    ma200 = sma(closes, 50)
    rsi = compute_rsi(closes)
    bb_upper, bb_mid, bb_lower = zip(*calculate_bollinger_bands(closes))
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    trend_info = detect_market_trend(symbol)
    atr = calculate_atr(highs, lows, closes)
    bb_breakout = (
        "UP" if close > bb_upper[-1] else
        "DOWN" if close < bb_lower[-1] else
        "NO"
    )
    orderbook = fetch_orderbook_strength(symbol)

    signals = []

    def build(strategy, confidence, regime):
        side = "LONG" if strategy != "Short Reversal" else "SHORT"
        if not is_trade_allowed(side, trend_info): return None
        entry = close
        liquidation = entry * (1 - 1 / LEVERAGE) if side == "LONG" else entry * (1 + 1 / LEVERAGE)
        sl_price = max(entry * (1 - SL_PERCENT), liquidation * 1.05) if side == "LONG" else min(entry * (1 + SL_PERCENT), liquidation * 0.95)
        tp_price = entry * (1 + TP_PERCENT) if side == "LONG" else entry * (1 - TP_PERCENT)
        atr_val = atr[-1] if atr and atr[-1] else abs(entry - sl_price)
        size = round(RISK_AMOUNT / atr_val, 4) if atr_val else 0
        signal = {
            "symbol": symbol, "timeframe": tf, "side": side,
            "entry": entry, "sl": sl_price, "tp": tp_price, "liquidation": liquidation,
            "rsi": rsi, "macd_hist": macd_hist[-1] if macd_hist else 0, "bb_breakout": bb_breakout,
            "trend": "bullish" if side == "LONG" else "bearish", "regime": regime,
            "confidence": confidence, "position_size": size,
            "forecast_pnl": round((TP_PERCENT * 100 * confidence) / 100, 2),
            "strategy": strategy,
            "timestamp": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M UTC+3"),
            "vol_spike": volumes[-1] > sum(volumes[-20:]) / 20 * 1.5,
            "atr": round(atr_val, 4) if atr_val else None,
            "atr_z": zscore([x for x in atr if x], 20),
            "orderbook_bias": orderbook["bias"]
        }
        signal["score"] = compute_score(signal, trend_info)
        return signal

    regime = "trend" if ma20[-1] > ma200[-1] else "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"

    if regime == "trend" and (ema9[-1] > ema21[-1] or bb_breakout == "UP"):
        sig = build("Trend", 90, regime)
        if sig: signals.append(sig)

    if regime == "mean_reversion" and (rsi < 40 or close < ma20[-1] or bb_breakout == "DOWN"):
        sig = build("Mean-Reversion", 85, regime)
        if sig: signals.append(sig)

    if regime == "scalp" and volumes[-1] > sum(volumes[-20:]) / 20 * 1.5:
        sig = build("Scalp Breakout", 80, regime)
        if sig: signals.append(sig)

    if rsi > 65 and bb_breakout == "UP":
        sig = build("Short Reversal", 75, "reversal")
        if sig: signals.append(sig)

    return signals

# === PDF Export ===
class PDFReport(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 10, "Top Bybit Futures Trade Signals", border=False, ln=True, align="C")
        self.ln(5)

    def add_signal(self, s, index):
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 10, f"{index}. {s['symbol']} ({s['side']}) - {s['strategy']}", ln=True)
        self.set_font("Helvetica", "", 10)
        details = [
            f"Entry: {s['entry']:.4f}",
            f"SL: {s['sl']:.4f} | TP: {s['tp']:.4f} | Size: {s['position_size']}",
            f"RSI: {s['rsi']} | MACD Hist: {s['macd_hist']:.4f}",
            f"BB Breakout: {s['bb_breakout']} | ATR: {s.get('atr', 'N/A')}",
            f"Trend: {s['trend']} | Regime: {s['regime']}",
            f"Vol Spike: {'Yes' if s['vol_spike'] else 'No'} | Orderbook Bias: {s['orderbook_bias']}",
            f"ATR Z-Score: {s.get('atr_z', 0):.2f}",
            f"Forecast PnL: {s['forecast_pnl']}% | Confidence: {s['confidence']}%",
            f"Score: {s['score']} / 100",
            f"Timestamp: {s['timestamp']}",
        ]
        for line in details:
            self.multi_cell(0, 8, line)
        self.ln(5)

def export_signals_to_pdf(signals, filename="top_signals.pdf"):
    pdf = PDFReport()
    pdf.add_page()
    for idx, sig in enumerate(signals, 1):
        pdf.add_signal(sig, idx)
    pdf.output(filename)
    print(f"\n✅ PDF exported: {filename}")

# === MAIN ===
def main():
    print("📊 Scanning Bybit Futures Signals...\n")
    all_signals = []
    symbols = get_symbols()
    print(f"🔍 Fetched {len(symbols)} symbols\n")

    for symbol in symbols:
        signals = analyze(symbol)
        if signals:
            print(f"✅ {symbol}: {len(signals)} signal(s) generated")
        all_signals.extend(signals)

    print(f"\n🧠 Total Signals Collected: {len(all_signals)}")

    if not all_signals:
        print("❌ No trade signals found.")
        return

    # Filter and rank top 5 signals
    filtered = [
        s for s in all_signals 
        if s['rsi'] > 45 and s['regime'] in ['trend', 'scalp', 'mean_reversion']
    ]
    top5 = sorted(
        filtered, 
        key=lambda x: (x['score'] * 0.7 + x['forecast_pnl'] * 0.3), 
        reverse=True
    )[:5]

    if top5:
        print("\n🏆 Top 5 Signals:\n")
        for i, s in enumerate(top5, 1):
            print(f"📌 Rank #{i}")
            print(f"Symbol     : {s['symbol']}")
            print(f"Side       : {s['side']}")
            print(f"Strategy   : {s['strategy']} | Regime: {s['regime']}")
            print(f"Trend      : {s['trend']} | BB: {s['bb_breakout']}")
            print(f"Entry      : {round(s['entry'], 4)} | TP: {round(s['tp'], 4)} | SL: {round(s['sl'], 4)}")
            print(f"RSI        : {round(s['rsi'], 2)} | MACD: {round(s['macd_hist'], 4)} | ATR Z: {round(s.get('atr_z', 0), 2)}")
            print(f"OB Bias    : {s['orderbook_bias']}")
            print(f"Confidence : {s['confidence']}% | Forecast PnL: {s['forecast_pnl']}%")
            print(f"Score      : {s['score']}")
            print("-" * 40)
    else:
        print("\n⚠️ No top signals matched filter conditions.")

    # Export all signals (not just top 5)
    export_signals_to_pdf(all_signals)

if __name__ == "__main__":
    main()
