import os
import json
import requests
from datetime import datetime, timezone
from binance.client import Client
from binance.enums import *
from fpdf import FPDF

# === CONFIG ===
API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
client = Client(API_KEY, API_SECRET)

RISK_AMOUNT = 1
LEVERAGE = 20
TP_PERCENT = 0.25
SL_PERCENT = 0.10

SIGNAL_FOLDER = "signals"
TRADE_FOLDER = "trades"
SIGNAL_PDF = "all_signals.pdf"
TRADE_PDF = "opened_trades.pdf"

# === UTILS ===
def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def current_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def save_json(data, folder, symbol):
    ensure_dir(folder)
    path = os.path.join(folder, f"{symbol.lower()}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

def format_signal(s, i=None):
    head = f"{i}. {s['symbol']} [{s['timeframe']}] | {s['side']} | {s['strategy']}" if i else s['symbol']
    return "\n".join([
        head,
        "-" * 60,
        f"Entry        : {s['entry']}",
        f"SL / TP      : {s['sl']} / {s['tp']}",
        f"Qty          : {s['position_size']}",
        f"Forecast PnL : {s['forecast_pn']:.2f}% | Confidence: {s['confidence']}%",
        f"RSI          : {s['rsi']} | Trend: {s['trend']} | Regime: {s['regime']}",
        f"Score        : {s['score']}",
        f"Timestamp    : {s['timestamp']}"
    ])

def save_pdf(signals, filename, title):
    if not signals:
        return
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, title, ln=True, align="C")
    pdf.set_font("Courier", size=9)
    for i, s in enumerate(signals, 1):
        pdf.multi_cell(0, 5, format_signal(s, i))
        pdf.ln(1)
    pdf.output(filename)
    print(f"📄 PDF saved: {filename}")

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

# === DATA ===
def get_symbols(limit=50):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5)
        return [s['symbol'] for s in r.json()['symbols']
                if s['contractType'] == 'PERPETUAL' and 'USDT' in s['symbol']][:limit]
    except Exception as e:
        print(f"[ERROR] get_symbols: {e}")
        return []

def fetch_ohlcv(symbol, interval='1h', limit=100):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=5)
        return [[float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in r.json()]
    except Exception as e:
        print(f"[ERROR] fetch_ohlcv {symbol}: {e}")
        return []

# === SIGNAL GENERATION ===
def analyze(symbol, tf="1h"):
    data = fetch_ohlcv(symbol, tf)
    if len(data) < 60: return []

    closes = [x[2] for x in data]
    volumes = [x[3] for x in data]
    close = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ma20 = sma(closes, 20)
    ma200 = sma(closes, 50)
    rsi = compute_rsi(closes)
    daily_change = round(((closes[-1] - closes[-2]) / closes[-2]) * 100, 2)

    trend = "bullish" if ma20[-1] > ma200[-1] else "bearish"
    regime = "trend" if abs(ma20[-1] - ma200[-1]) / ma200[-1] > 0.01 else (
        "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"
    )

    def build_signal(name, condition, confidence):
        if not condition:
            return None
        side = "long" if trend == "bullish" else "short"
        entry = close
        liquidation = entry * (1 - 1 / LEVERAGE) if side == "long" else entry * (1 + 1 / LEVERAGE)
        sl = max(entry * (1 - SL_PERCENT), liquidation * 1.05) if side == "long" else min(entry * (1 + SL_PERCENT), liquidation * 0.95)
        tp = entry * (1 + TP_PERCENT) if side == "long" else entry * (1 - TP_PERCENT)
        risk = abs(entry - sl)
        qty = round(RISK_AMOUNT / risk, 6) if risk > 0 else 0

        return {
            "symbol": symbol,
            "timeframe": tf,
            "side": side.upper(),
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "rsi": rsi,
            "trend": trend,
            "regime": regime,
            "confidence": confidence,
            "position_size": qty,
            "forecast_pn": round(TP_PERCENT * 100 * confidence / 100, 2),
            "score": round(confidence + rsi / 2, 2),
            "strategy": name,
            "daily_change": daily_change,
            "timestamp": current_timestamp()
        }

    signals = []
    if regime == "trend":
        sig = build_signal("Trend", ema9[-1] > ema21[-1], 90)
        if sig: signals.append(sig)
    if regime == "mean_reversion":
        sig = build_signal("Mean-Reversion", rsi < 40 or close < ma20[-1], 85)
        if sig: signals.append(sig)
    if regime == "scalp":
        if volumes[-1] > (sum(volumes[-20:]) / 20) * 1.5:
            sig = build_signal("Scalp", True, 80)
            if sig: signals.append(sig)
    return signals

# === TRADE EXECUTION ===
def place_trade(signal):
    try:
        client.futures_change_leverage(symbol=signal["symbol"], leverage=LEVERAGE)
        side = SIDE_BUY if signal["side"] == "LONG" else SIDE_SELL

        order = client.futures_create_order(
            symbol=signal["symbol"],
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=signal["position_size"]
        )

        # TP
        client.futures_create_order(
            symbol=signal["symbol"],
            side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
            type=ORDER_TYPE_LIMIT,
            quantity=signal["position_size"],
            price=str(signal["tp"]),
            timeInForce=TIME_IN_FORCE_GTC
        )

        # SL
        client.futures_create_order(
            symbol=signal["symbol"],
            side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=str(signal["sl"]),
            quantity=signal["position_size"],
            timeInForce=TIME_IN_FORCE_GTC
        )

        signal["binance_order_id"] = order["orderId"]
        signal["trade_status"] = "OPENED"
        print(f"✅ TRADE OPENED: {signal['symbol']}")
        return signal

    except Exception as e:
        print(f"❌ FAILED: {signal['symbol']} - {e}")
        return None

# === MAIN ===
def main():
    ensure_dir(SIGNAL_FOLDER)
    ensure_dir(TRADE_FOLDER)
    all_signals = []

    for symbol in get_symbols():
        signals = analyze(symbol)
        for s in signals:
            save_json(s, SIGNAL_FOLDER, s['symbol'])  # Save every signal
            all_signals.append(s)

    if not all_signals:
        print("No signals found.")
        return

    top5 = sorted(all_signals, key=lambda x: x['score'], reverse=True)[:5]
    opened_trades = []

    for signal in top5:
        placed = place_trade(signal)
        if placed:
            save_json(placed, TRADE_FOLDER, placed["symbol"])
            opened_trades.append(placed)

    save_pdf(all_signals, SIGNAL_PDF, "All Signals")
    save_pdf(opened_trades, TRADE_PDF, "Opened Trades")

if __name__ == "__main__":
    main()
