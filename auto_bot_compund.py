import os, json, requests
from datetime import datetime, timedelta, timezone
from fpdf import FPDF
import praw # type: ignore

# === CONFIG ===
CAPITAL_FILE = "capital.json"
TRADE_LOG_FILE = "trades_history.json"
SIGNAL_DIR = "signals"
TRADE_DIR = "trades"
START_CAPITAL = 10.0
MAX_LOSS_PCT = 15
TP_PERCENT = 0.25
SL_PERCENT = 0.10
LEVERAGE = 20

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."  # Replace with your real webhook
REDDIT_CREDS = {
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "username": "YOUR_USERNAME",
    "password": "YOUR_PASSWORD",
    "user_agent": "cryptopilot_bot"
}

for d in [SIGNAL_DIR, TRADE_DIR]:
    os.makedirs(d, exist_ok=True)

# === REDDIT + DISCORD ===
def post_to_discord(message):
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
    except Exception as e:
        print("❌ Discord Error:", e)

def post_to_reddit(title, body):
    try:
        reddit = praw.Reddit(**REDDIT_CREDS)
        subreddit = reddit.subreddit("YourSubreddit")
        subreddit.submit(title, selftext=body)
    except Exception as e:
        print("❌ Reddit Error:", e)

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
# === HELPER: CAPITAL TRACKING ===
def load_capital():
    if not os.path.exists(CAPITAL_FILE):
        return START_CAPITAL
    with open(CAPITAL_FILE) as f:
        return json.load(f).get("balance", START_CAPITAL)

def save_capital(balance):
    with open(CAPITAL_FILE, "w") as f:
        json.dump({"balance": round(balance, 4)}, f)

# === HELPER: TRADE HISTORY ===
def log_trade(trade):
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            trades = json.load(f)
    trades.append(trade)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2)

def today_loss_pct():
    if not os.path.exists(TRADE_LOG_FILE): return 0
    with open(TRADE_LOG_FILE) as f:
        trades = json.load(f)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    loss = sum(t["pnl"] for t in trades if t["timestamp"].startswith(today) and t["pnl"] < 0)
    capital = load_capital()
    return round(-loss / capital * 100, 2) if capital > 0 else 100

# === SIGNAL FORMATTING + EXPORT ===
def format_signal(s):
    return f"""📈 {s['symbol']} [{s['side']}] | {s['strategy']}
Entry: {s['entry']} | TP: {s['tp']} | SL: {s['sl']}
Confidence: {s['confidence']}% | Score: {s['score']}
Regime: {s['regime']} | Trend: {s['trend']}
Timestamp: {s['timestamp']}"""

def save_json(data, path):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

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

# === DEMO TRADE + COMPOUND ===
def simulate_trade(signal):
    entry, tp = signal['entry'], signal['tp']
    side = signal['side']
    capital = load_capital()
    risk_amount = capital * 0.02
    risk_per_unit = abs(signal['entry'] - signal['sl'])
    qty = round(risk_amount / risk_per_unit, 4) if risk_per_unit > 0 else 0
    pnl = round((tp - entry) * qty if side == "LONG" else (entry - tp) * qty, 4)
    capital += pnl
    save_capital(capital)
    trade = {
        "symbol": signal["symbol"],
        "side": side,
        "entry": entry,
        "exit": tp,
        "qty": qty,
        "pnl": pnl,
        "strategy": signal["strategy"],
        "timestamp": signal["timestamp"]
    }
    log_trade(trade)
    return trade
def fetch_ohlcv(symbol, interval='1h', limit=100):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=5)
        return [[float(x[2]), float(x[3]), float(x[4]), float(x[5]), float(x[1])] for x in r.json()]
    except:
        return []

def get_symbols(limit=100):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5)
        return [s['symbol'] for s in r.json()['symbols'] if s['contractType'] == 'PERPETUAL' and 'USDT' in s['symbol']][:limit]
    except:
        return []

def build_signal(name, condition, confidence, regime, trend_info, close, symbol, tf, rsi, macd_hist, bb_upper, bb_lower, volumes):
    if not condition:
        return None
    side = "long" if name != "Short Reversal" else "short"
    if not is_trade_allowed(side.upper(), trend_info): return None
    entry = close
    sl_price = entry * (1 - SL_PERCENT) if side == "long" else entry * (1 + SL_PERCENT)
    tp_price = entry * (1 + TP_PERCENT) if side == "long" else entry * (1 - TP_PERCENT)
    liquidation = entry * (1 - 1 / LEVERAGE) if side == "long" else entry * (1 + 1 / LEVERAGE)
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
        "score": 80 + confidence * 0.2,
        "strategy": name,
        "timestamp": (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M UTC+3"),
        "vol_spike": volumes[-1] > sum(volumes[-20:]) / 20 * 1.5
    }
    return signal

def is_trade_allowed(side, trend_info):
    trend_votes = list(trend_info.values())
    bull = trend_votes.count('bullish')
    bear = trend_votes.count('bearish')
    if bull > bear and side == 'SHORT': return False
    if bear > bull and side == 'LONG': return False
    return True

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
    regime = "trend" if ma20[-1] > ma200[-1] else "mean_reversion" if rsi < 35 or rsi > 65 else "scalp"
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
def main():
    print("🚀 Running CryptoPilot...")
    balance = load_capital()
    loss_pct = today_loss_pct()

    if loss_pct >= MAX_LOSS_PCT:
        print(f"❌ Max daily loss of {MAX_LOSS_PCT}% hit ({loss_pct}%) — Trading paused.")
        return

    all_signals, trades = [], []

    for symbol in get_symbols(limit=30):
        signals = analyze(symbol)
        all_signals.extend(signals)

    if not all_signals:
        print("⚠️ No signals found.")
        return

    top5 = sorted(all_signals, key=lambda x: (x['score'], x['confidence']), reverse=True)[:5]

    # === SAVE + POST EACH SIGNAL ===
    for i, s in enumerate(top5, 1):
        print(f"\n#{i} {s['symbol']} {s['side']} | Score: {s['score']} | {s['strategy']}")
        msg = format_signal(s)
        save_json(s, f"{SIGNAL_DIR}/{s['symbol']}.json")
        post_to_discord(msg)
        post_to_reddit(f"CryptoPilot Signal - {s['symbol']} #{i}", msg)

        # === DEMO TRADE
        trade = simulate_trade(s)
        save_json(trade, f"{TRADE_DIR}/{s['symbol']}.json")
        trades.append(trade)
        post_to_discord(f"🟢 Executed Trade: {trade['symbol']} | PnL: {trade['pnl']}")
        post_to_reddit(f"CryptoPilot Trade - {trade['symbol']}", f"Trade Summary:\n{json.dumps(trade, indent=2)}")

    # === EXPORT PDFS
    save_pdf("all_signals.pdf", all_signals, "📊 All Signals")
    save_pdf("all_trades.pdf", trades, "💹 Executed Trades")

    print("\n✅ Run complete. Trades and signals exported.\n")

if __name__ == "__main__":
    main()
