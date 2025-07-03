import requests
from datetime import datetime, timezone, timedelta
from fpdf import FPDF

# === CONFIG ===
RISK_AMOUNT = 1
LEVERAGE = 20
TP_PERCENT = 0.30
SL_PERCENT = 0.15

# === INDICATORS ===
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

# === OHLCV & SYMBOL FETCH ===
def fetch_ohlcv(symbol, interval='1h', limit=100):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=5)
        return [[float(x[2]), float(x[3]), float(x[4]), float(x[5]), float(x[1])] for x in r.json()]
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        return []

def get_symbols(limit=100):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5)
        return [s['symbol'] for s in r.json()['symbols'] if s['contractType'] == 'PERPETUAL' and 'USDT' in s['symbol']][:limit]
    except Exception as e:
        print(f"[ERROR] Symbols: {e}")
        return []

# === TREND LOGIC ===
def detect_market_trend(symbol):
    def fetch_closes(symbol, tf):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=60"
        try:
            r = requests.get(url, timeout=5)
            return [float(x[4]) for x in r.json()]
        except:
            return []

    trend_info = {}
    for tf in ['1h', '4h', '1d']:
        closes = fetch_closes(symbol, tf)
        if len(closes) < 50:
            trend_info[tf] = 'neutral'
            continue
        ema9 = ema(closes, 9)[-1]
        ema21 = ema(closes, 21)[-1]
        ma200 = sma(closes, 50)[-1]
        close = closes[-1]

        if close > ma200 and ema9 > ema21:
            trend_info[tf] = 'bullish'
        elif close < ma200 and ema9 < ema21:
            trend_info[tf] = 'bearish'
        else:
            trend_info[tf] = 'neutral'
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

# === ANALYSIS ===
def analyze(symbol, tf="1h"):
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

    trend = "bullish" if ma20[-1] > ma200[-1] else "bearish"
    regime = "trend" if abs(ma20[-1] - ma200[-1]) / ma200[-1] > 0.01 else (
        "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"
    )

    def build_signal(name, condition, confidence):
        if not condition:
            return None

        side = "long" if trend == "bullish" else "short"
        if not is_trade_allowed(side.upper(), trend_info):
            return None

        entry = close
        liquidation = entry * (1 - 1 / LEVERAGE) if side == "long" else entry * (1 + 1 / LEVERAGE)
        if side == "long":
            sl_price = max(entry * (1 - SL_PERCENT), liquidation * 1.05)
            tp_price = entry * (1 + TP_PERCENT)
        else:
            sl_price = min(entry * (1 + SL_PERCENT), liquidation * 0.95)
            tp_price = entry * (1 - TP_PERCENT)

        risk_per_unit = abs(entry - sl_price)
        position_size = round(RISK_AMOUNT / risk_per_unit, 6) if risk_per_unit > 0 else 0
        forecast_pnl = round((TP_PERCENT * 100 * confidence) / 100, 2)
        score = round(confidence + rsi / 2, 2)

        return {
            "symbol": symbol,
            "timeframe": tf,
            "side": side.upper(),
            "entry": round(entry, 8),
            "sl": round(sl_price, 8),
            "tp": round(tp_price, 8),
            "liquidation": round(liquidation, 8),
            "rsi": rsi,
            "macd_hist": round(macd_hist[-1], 4) if macd_hist[-1] else None,
            "bb_breakout": "YES" if close > bb_upper[-1] or close < bb_lower[-1] else "NO",
            "trend": trend,
            "regime": regime,
            "confidence": confidence,
            "position_size": position_size,
            "forecast_pnl": forecast_pnl,
            "score": score,
            "strategy": name,
            "timestamp": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M UTC+3")

        }

    signals = []
    if regime == "trend":
        sig = build_signal("Trend", ema9[-1] > ema21[-1], 90)
        if sig: signals.append(sig)
    if regime == "mean_reversion":
        sig = build_signal("Mean-Reversion", rsi < 40 or close < ma20[-1], 85)
        if sig: signals.append(sig)
    if regime == "scalp":
        avg_vol = sum(volumes[-20:]) / 20
        if volumes[-1] > avg_vol * 1.5:
            sig = build_signal("Scalp", True, 80)
            if sig: signals.append(sig)

    return signals

# === OUTPUT FORMAT ===
def format_signal(s, i=None):
    head = f"{i}. {s['symbol']} [{s['timeframe']}] | {s['side']} | {s['strategy']}" if i else s['symbol']
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
        f"Score        : {s['score']}",
        f"Timestamp    : {s['timestamp']}"
    ])

# === PDF EXPORT ===
def save_pdf(all_signals, top5):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, "Top 5 Signals", ln=True, align="C")
    pdf.set_font("Courier", size=9)
    for i, s in enumerate(top5, 1):
        pdf.multi_cell(0, 5, format_signal(s, i))
        pdf.ln(1)
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, "All Signals", ln=True, align="C")
    pdf.set_font("Courier", size=9)
    for i, s in enumerate(all_signals, 1):
        pdf.multi_cell(0, 5, format_signal(s, i))
        pdf.ln(1)
    pdf.output("top_signals.pdf")
    print("✅ PDF saved as top_signals.pdf")

# === MAIN ===
def main():
    print("📊 Scanning Binance Futures Signals...\n")
    all_signals = []
    for symbol in get_symbols():
        all_signals.extend(analyze(symbol))

    if not all_signals:
        print("❌ No signals found.")
        return

    filtered = [s for s in all_signals if s['rsi'] > 45 and s['regime'] in ['trend', 'scalp', 'mean_reversion']]
    top5 = sorted(filtered, key=lambda x: (x['score'], x['forecast_pnl']), reverse=True)[:5]

    for i, s in enumerate(top5, 1):
        print(format_signal(s, i))
        print()

    save_pdf(all_signals, top5)

if __name__ == "__main__":
    main()
