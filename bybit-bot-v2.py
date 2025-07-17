import requests
from datetime import datetime, timezone, timedelta
from fpdf import FPDF

RISK_AMOUNT = 2
LEVERAGE = 20
TP_PERCENT = 0.25
SL_PERCENT = 0.10

# === Indicators ===
def ema(values, period):
    emas, k = [], 2 / (period + 1)
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
    histogram = [m - s if m and s else None for m, s in zip(macd_line, signal_line)]
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
    atrs = []
    atr = sum(trs[:period]) / period
    atrs.append(atr)
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        atrs.append(atr)
    return [None] * (period + 1) + atrs

def zscore(series, period=20):
    if len(series) < period:
        return 0
    mean = sum(series[-period:]) / period
    std = (sum((x - mean) ** 2 for x in series[-period:]) / period) ** 0.5
    return (series[-1] - mean) / std if std != 0 else 0
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
    if bull > bear and side == 'SHORT':
        return False
    if bear > bull and side == 'LONG':
        return False
    return True

# === Scoring ===
def compute_score(s):
    score = 0
    trend_info = detect_market_trend(s['symbol'])
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

    score += s["confidence"] * 0.4
    rr = TP_PERCENT / SL_PERCENT
    score += 10 if rr >= 2 else 5 if rr >= 1.5 else 0

    if trend_info['1h'] == trend_info['4h'] == trend_info['15m'] == s['trend']:
        score += 10
    return round(score, 2)

# === Signal Builder ===
def build_signal(name, condition, confidence, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes, highs, lows, closes, bb_breakout):
    if not condition:
        return None
    side = "long" if name != "Short Reversal" else "short"
    if not is_trade_allowed(side.upper(), trend_info): return None

    entry = close
    liquidation = entry * (1 - 1 / LEVERAGE) if side == "long" else entry * (1 + 1 / LEVERAGE)
    sl_price = max(entry * (1 - SL_PERCENT), liquidation * 1.05) if side == "long" else min(entry * (1 + SL_PERCENT), liquidation * 0.95)
    tp_price = entry * (1 + TP_PERCENT) if side == "long" else entry * (1 - TP_PERCENT)

    atr = calculate_atr(highs, lows, closes)[-1]
    risk_per_unit = atr if atr else abs(entry - sl_price)
    position_size = round(RISK_AMOUNT / risk_per_unit, 6) if risk_per_unit > 0 else 0

    forecast_pnl = round((TP_PERCENT * 100 * confidence) / 100, 2)
    signal = {
        "symbol": symbol,
        "timeframe": tf,
        "side": side.upper(),
        "entry": round(entry, 6),
        "sl": round(sl_price, 6),
        "tp": round(tp_price, 6),
        "liquidation": round(liquidation, 6),
        "rsi": rsi,
        "macd_hist": round(macd_hist[-1], 4) if macd_hist[-1] else None,
        "bb_breakout": bb_breakout,
        "trend": "bullish" if side == "long" else "bearish",
        "regime": regime,
        "confidence": confidence,
        "position_size": position_size,
        "forecast_pnl": forecast_pnl,
        "strategy": name,
        "timestamp": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M UTC+3"),
        "vol_spike": volumes[-1] > sum(volumes[-20:]) / 20 * 1.5,
        "atr": round(atr, 4) if atr else None
    }

    signal["atr_z"] = zscore([x for x in calculate_atr(highs, lows, closes) if x], 20)
    signal["score"] = compute_score(signal)
    return signal
# === Analysis Engine ===
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
    atr_z = zscore([x for x in atr if x], 20)

    # Skip low volatility conditions
    if atr[-1] < sum([x for x in atr[-20:] if x]) / 20 * 0.8:
        return []

    regime = "trend" if ma20[-1] > ma200[-1] else (
        "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"
    )
    bb_breakout = (
        "UP" if close > bb_upper[-1] else
        "DOWN" if close < bb_lower[-1] else
        "NO"
    )

    signals = []

    if regime == "trend" and (ema9[-1] > ema21[-1] or bb_breakout == "UP"):
        sig = build_signal("Trend", True, 90, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes, highs, lows, closes, bb_breakout)
        if sig: signals.append(sig)

    if regime == "mean_reversion" and (rsi < 40 or close < ma20[-1] or bb_breakout == "DOWN"):
        sig = build_signal("Mean-Reversion", True, 85, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes, highs, lows, closes, bb_breakout)
        if sig: signals.append(sig)

    if regime == "scalp" and volumes[-1] > sum(volumes[-20:]) / 20 * 1.5:
        sig = build_signal("Scalp Breakout", True, 80, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes, highs, lows, closes, bb_breakout)
        if sig: signals.append(sig)

    if rsi > 65 and bb_breakout == "UP":
        sig = build_signal("Short Reversal", True, 75, "reversal", trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes, highs, lows, closes, bb_breakout)
        if sig: signals.append(sig)

    return signals

# === Data Fetching ===
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

# === Display & PDF ===
def score_label(score):
    if score >= 85: return "Elite"
    elif score >= 70: return "Strong"
    elif score >= 50: return "Average"
    return "Weak"

def confidence_tag(conf):
    if conf >= 90: return "High Conviction"
    if conf >= 80: return "Solid Setup"
    return "Watchlist"

def format_signal(s, i=None):
    head = f"{i}. {s['symbol']} [{s['timeframe']}m] | {s['side']} | {s['strategy']}" if i else s['symbol']
    return "\n".join([
        head,
        "-" * 60,
        f"Entry        : {s['entry']:.8f}",
        f"SL / TP      : {s['sl']:.8f} / {s['tp']:.8f}",
        f"Liquidation  : {s['liquidation']:.8f}",
        f"Position Size: {s['position_size']} qty (1 USDT @ {LEVERAGE}x)",
        f"Forecast PnL : {s['forecast_pnl']}% | Confidence: {s['confidence']}%",
        f"Trend        : {s['trend']} | Regime: {s['regime']} | RSI: {s['rsi']}",
        f"MACD Hist    : {s['macd_hist']} | BB Breakout: {s['bb_breakout']}",
        f"ATR (Vol)    : {s['atr']} | Z-Score: {round(s['atr_z'],2)}",
        f"Score        : {s['score']} / 100 | Rank: {score_label(s['score'])}",
        f"Tag          : {confidence_tag(s['confidence'])}",
        f"Timestamp    : {s['timestamp']}"
    ])

def save_pdf(all_signals, top5):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, "Top 5 Signals", ln=True, align="C")
    pdf.set_font("Arial", size=9)
    for i, s in enumerate(top5, 1):
        pdf.multi_cell(0, 5, format_signal(s, i))
        pdf.ln(1)

    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, "All Signals", ln=True, align="C")
    pdf.set_font("Arial", size=9)
    for i, s in enumerate(all_signals, 1):
        pdf.multi_cell(0, 5, format_signal(s, i))
        pdf.ln(1)

    pdf.output("top_signals.pdf")
    print("✅ PDF saved successfully as 'top_signals.pdf'")

# === MAIN ===
def main():
    print("📊 Scanning Bybit Futures Signals...\n")
    all_signals = []
    for symbol in get_symbols():
        all_signals.extend(analyze(symbol))

    if not all_signals:
        print("❌ No signals found.")
        return

    filtered = [s for s in all_signals if s['rsi'] > 45 and s['regime'] in ['trend', 'scalp', 'mean_reversion']]
    top5 = sorted(filtered, key=lambda x: (x['score'] * 0.7 + x['forecast_pnl'] * 0.3), reverse=True)[:5]

    for i, s in enumerate(top5, 1):
        print(format_signal(s, i))
        print()

    save_pdf(all_signals, top5)

if __name__ == "__main__":
    main()
