import requests
from datetime import datetime, timezone
from fpdf import FPDF

TIMEFRAMES = ["4h"]

def ema(values, period):
    emas = []
    k = 2 / (period + 1)
    ema_prev = sum(values[:period]) / period
    emas.append(ema_prev)
    for price in values[period:]:
        ema_new = price * k + ema_prev * (1 - k)
        emas.append(ema_new)
        ema_prev = ema_new
    return [None]*(period-1) + emas

def sma(values, period):
    return [None if i < period-1 else sum(values[i+1-period:i+1]) / period for i in range(len(values))]

def compute_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    if len(gains) < period:
        return 50
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def macd_diff(closes, fast=12, slow=26, signal=9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd = [f - s if f and s else None for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema([m for m in macd if m is not None], signal)
    full_signal_line = [None] * (len(macd) - len(signal_line)) + signal_line
    return [m - s if m and s else None for m, s in zip(macd, full_signal_line)]

def bollinger_bands(closes, period=20):
    ma = sma(closes, period)
    upper, lower = [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None)
            lower.append(None)
            continue
        std = (sum((closes[j] - ma[i]) ** 2 for j in range(i - period + 1, i + 1)) / period) ** 0.5
        upper.append(ma[i] + 2 * std)
        lower.append(ma[i] - 2 * std)
    return upper, lower

def get_symbols(limit=200):
    try:
        res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5)
        data = res.json()
        return [
            s['symbol'] for s in data['symbols']
            if s['contractType'] == 'PERPETUAL' and 'USDT' in s['symbol']
        ][:limit]
    except Exception as e:
        print(f"[ERROR] Fetching symbols: {e}")
        return []

def fetch_ohlcv(symbol, interval='15m', limit=100):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()
        return [[float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in data]  # high, low, close, volume
    except Exception as e:
        print(f"[ERROR] {symbol} {interval}: {e}")
        return []

def analyze(symbol, tf='15m'):
    data = fetch_ohlcv(symbol, tf)
    if len(data) < 60:
        return []

    highs = [x[0] for x in data]
    lows = [x[1] for x in data]
    closes = [x[2] for x in data]
    volumes = [x[3] for x in data]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ma20 = sma(closes, 20)
    ma200 = sma(closes, 50)  # fallback for 200
    macd = macd_diff(closes)
    rsi = compute_rsi(closes)
    bb_upper, bb_lower = bollinger_bands(closes)
    atr = max(highs[-14:]) - min(lows[-14:])
    vol_spike = volumes[-1] > sum(volumes[-20:]) / 20 * 1.5
    close = closes[-1]

    trend = "bullish" if ma20[-1] > ma200[-1] else "bearish"
    regime = "trend" if abs(ma20[-1] - ma200[-1]) / ma200[-1] > 0.01 else \
             "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"

    def build_signal(name, condition, sl_mult, tp_mult, base_conf):
        if not condition:
            return None
        side = "long" if trend == "bullish" else "short"
        sl = round(close - atr * sl_mult if side == "long" else close + atr * sl_mult, 4)
        tp = round(close + atr * tp_mult if side == "long" else close - atr * tp_mult, 4)
        return {
            "symbol": symbol,
            "timeframe": tf,
            "side": side.upper(),
            "entry": round(close, 4),
            "sl": sl,
            "tp": tp,
            "confidence": base_conf,
            "strategy": name,
            "trend": trend,
            "regime": regime,
            "rsi": rsi,
            "close": round(close, 4),
            "daily_high": round(max(highs[-96:]), 4),
            "daily_low": round(min(lows[-96:]), 4),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        }

    signals = []
    if regime == "trend":
        sig = build_signal("Trend", ema9[-1] > ema21[-1], 1.5, 3.0, 90)
        if sig: signals.append(sig)
    if regime == "mean_reversion":
        sig = build_signal("Mean-Reversion", rsi < 30 or close < bb_lower[-1], 1.2, 2.0, 85)
        if sig: signals.append(sig)
    if regime == "scalp":
        sig = build_signal("Scalp", vol_spike, 0.8, 1.2, 80)
        if sig: signals.append(sig)

    return signals

def format_signal_text(s, index=None):
    entry = s['entry']
    tp = s['tp']
    side = s['side']
    high = s['daily_high']
    low = s['daily_low']
    pct_change = ((high - low) / low) * 100
    pnl_pct = ((tp - entry) / entry * 100) if side == "LONG" else ((entry - tp) / entry * 100)
    lines = [
        f"{index}. {s['symbol']} | {s['timeframe']} | {s['side']} | {s['strategy']}" if index else
        f"{s['symbol']} | {s['timeframe']} | {s['side']} | {s['strategy']}",
        "-" * 46,
        f"Close        : {s['close']}",
        f"Entry        : {entry}",
        f"SL / TP      : {s['sl']} / {tp}",
        f"RSI          : {s['rsi']}",
        f"High / Low   : {high} / {low}",
        f"% Change     : {pct_change:+.2f}%",
        f"Forecast PnL : {pnl_pct:+.2f}%",
        f"Trend        : {s['trend']} | Regime: {s['regime']}",
        f"Confidence   : {s['confidence']}%",
        f"Timestamp    : {s['timestamp']}"
    ]
    return "\n".join(lines)

def save_pdf(all_signals, top_signals):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, "Top 3 Signals (All Timeframes)", ln=True, align="C")
    pdf.set_font("Courier", size=11)

    for i, s in enumerate(top_signals, 1):
        pdf.multi_cell(0, 7, format_signal_text(s, i))

    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, "All Valid Signals by Timeframe", ln=True, align="C")
    pdf.set_font("Courier", size=11)

    grouped = {}
    for s in all_signals:
        grouped.setdefault(s['timeframe'], []).append(s)

    for tf in TIMEFRAMES:
        if tf not in grouped:
            continue
        pdf.ln(5)
        pdf.set_font("Arial", style='B', size=12)
        pdf.cell(0, 10, f"Timeframe: {tf}", ln=True)
        pdf.set_font("Courier", size=11)
        for i, s in enumerate(grouped[tf], 1):
            pdf.multi_cell(0, 7, format_signal_text(s, i))

    pdf.output("top_signals.pdf")
    print("\n[✓] PDF saved as top_signals.pdf")

def forecast_pnl(s):
    entry = s['entry']
    tp = s['tp']
    if s['side'] == "LONG":
        return (tp - entry) / entry * 100
    else:
        return (entry - tp) / entry * 100

def main():
    print("📊 Scanning Binance Futures Trade Signals across timeframes pls wait...\n")
    all_signals = []
    for tf in TIMEFRAMES:
        for symbol in get_symbols():
            signals = analyze(symbol, tf)
            all_signals.extend(signals)

    all_signals = sorted(all_signals, key=forecast_pnl, reverse=True)
    top5 = all_signals[:5]

    for s in top5:
        print(f"{s['symbol']} [{s['timeframe']}] | {s['side']} | {s['strategy']} | PnL: {forecast_pnl(s):.2f}%")

    save_pdf(all_signals, top5)


if __name__ == "__main__":
    main()
