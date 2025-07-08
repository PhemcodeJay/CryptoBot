import os, json, requests
from datetime import datetime, timezone, timedelta
from fpdf import FPDF
import praw # type: ignore

# === CONFIG ===
RISK_AMOUNT = 2
LEVERAGE = 20
TP_PERCENT = 0.25
SL_PERCENT = 0.10
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."  # Replace with your webhook

os.makedirs("signals", exist_ok=True)
os.makedirs("trades", exist_ok=True)

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

def detect_market_trend(symbol):
    def fetch_closes(symbol, tf):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=60"
        try:
            r = requests.get(url, timeout=5)
            return [float(x[4]) for x in r.json()]
        except:
            return []

    trend_info = {}
    for tf in ['1h', '4h', '15m']:
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

def compute_score(s):
    score = 0
    trend_info = detect_market_trend(s['symbol'])
    bull = list(trend_info.values()).count('bullish')
    bear = list(trend_info.values()).count('bearish')
    score += 10 if bull == 3 or bear == 3 else 5 if bull == 2 or bear == 2 else 0
    if s['side'] == 'LONG' and 45 < s['rsi'] < 70: score += 10
    elif s['side'] == 'SHORT' and 30 < s['rsi'] < 55: score += 10
    if s['macd_hist'] and ((s['macd_hist'] > 0 and s['side'] == 'LONG') or (s['macd_hist'] < 0 and s['side'] == 'SHORT')):
        score += 10
    if s["bb_breakout"] == "YES": score += 5
    if s.get("vol_spike"): score += 10
    score += s["confidence"] * 0.3
    rr = TP_PERCENT / SL_PERCENT
    score += 10 if rr >= 2 else 5 if rr >= 1.5 else 0
    return round(score, 2)

def format_signal(s):
    return f"""📈 {s['symbol']} [{s['side']}] | {s['strategy']}
Entry: {s['entry']} | TP: {s['tp']} | SL: {s['sl']}
Confidence: {s['confidence']}% | Score: {s['score']}
Regime: {s['regime']} | Trend: {s['trend']}
Timestamp: {s['timestamp']}"""

def post_to_discord(message):
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        print("❌ Discord error:", e)

def post_to_reddit(title, body):
    try:
        reddit = praw.Reddit(
            client_id='YOUR_CLIENT_ID',
            client_secret='YOUR_CLIENT_SECRET',
            username='YOUR_USERNAME',
            password='YOUR_PASSWORD',
            user_agent='cryptopilot_bot'
        )
        subreddit = reddit.subreddit("YourSubreddit")
        subreddit.submit(title, selftext=body)
    except Exception as e:
        print("❌ Reddit error:", e)

def demo_trade_from_signal(s):
    side = s['side']
    entry = s['entry']
    tp = s['tp']
    qty = s['position_size']
    pnl = round((tp - entry) * qty if side == "LONG" else (entry - tp) * qty, 2)
    return {
        "symbol": s["symbol"],
        "side": s["side"],
        "entry": entry,
        "exit": tp,
        "pnl": pnl,
        "strategy": s["strategy"],
        "timestamp": s["timestamp"]
    }

def save_json(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)

def save_pdf(filename, items, title):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, title, ln=True, align="C")
    pdf.ln(5)
    for item in items:
        for k, v in item.items():
            pdf.multi_cell(0, 8, f"{k}: {v}")
        pdf.ln(5)
    pdf.output(filename)
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

def build_signal(name, condition, confidence, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes):
    if not condition:
        return None
    side = "long" if name != "Short Reversal" else "short"
    if not is_trade_allowed(side.upper(), trend_info): return None
    entry = close
    liquidation = entry * (1 - 1 / LEVERAGE) if side == "long" else entry * (1 + 1 / LEVERAGE)
    sl_price = max(entry * (1 - SL_PERCENT), liquidation * 1.05) if side == "long" else min(entry * (1 + SL_PERCENT), liquidation * 0.95)
    tp_price = entry * (1 + TP_PERCENT) if side == "long" else entry * (1 - TP_PERCENT)
    risk_per_unit = abs(entry - sl_price)
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
        "bb_breakout": "YES" if close > bb_upper[-1] or close < bb_lower[-1] else "NO",
        "trend": "bullish" if side == "long" else "bearish",
        "regime": regime,
        "confidence": confidence,
        "position_size": position_size,
        "forecast_pnl": forecast_pnl,
        "strategy": name,
        "timestamp": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M UTC+3"),
        "vol_spike": volumes[-1] > sum(volumes[-20:]) / 20 * 1.5
    }
    signal["score"] = compute_score(signal)
    return signal

def analyze(symbol, tf="1h"):
    data = fetch_ohlcv(symbol, tf)
    if len(data) < 60: return []
    highs = [x[0] for x in data]
    lows = [x[1] for x in data]
    closes = [x[2] for x in data]
    volumes = [x[3] for x in data]
    close = closes[-1]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ma20 = sma(closes, 20)
    ma200 = sma(closes, 50)
    rsi = compute_rsi(closes)
    bb_upper, bb_mid, bb_lower = zip(*calculate_bollinger_bands(closes))
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    trend_info = detect_market_trend(symbol)
    regime = "trend" if ma20[-1] > ma200[-1] else (
        "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"
    )
    signals = []
    if regime == "trend":
        sig = build_signal("Trend", ema9[-1] > ema21[-1], 90, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes)
        if sig: signals.append(sig)
    if regime == "mean_reversion":
        sig = build_signal("Mean-Reversion", rsi < 40 or close < ma20[-1], 85, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes)
        if sig: signals.append(sig)
    if regime == "scalp" and volumes[-1] > sum(volumes[-20:]) / 20 * 1.5:
        sig = build_signal("Scalp Breakout", True, 80, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes)
        if sig: signals.append(sig)
    if rsi > 65 and close > bb_upper[-1]:
        sig = build_signal("Short Reversal", True, 75, "reversal", trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes)
        if sig: signals.append(sig)
    return signals

# === MAIN ===
def main():
    print("🚀 Scanning Binance Futures...")
    all_signals, trades = [], []
    for symbol in get_symbols():
        sigs = analyze(symbol)
        all_signals.extend(sigs)
    filtered = [s for s in all_signals if s['score'] > 50]
    top5 = sorted(filtered, key=lambda x: x['score'], reverse=True)[:5]
    for s in top5:
        save_json(s, f"signals/{s['symbol']}.json")
        trade = demo_trade_from_signal(s)
        save_json(trade, f"trades/{trade['symbol']}.json")
        trades.append(trade)
        post_to_discord(format_signal(s))
        post_to_reddit(f"CryptoPilot Signal - {s['symbol']}", format_signal(s))
    save_pdf("signals_all.pdf", all_signals, "All Signals")
    save_pdf("trades_all.pdf", trades, "All Trades")
    print("✅ Done. Signals and trades posted, PDFs exported.")

if __name__ == "__main__":
    main()
