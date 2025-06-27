import sys
import io

# Add this at the beginning of your script
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend
matplotlib.rcParams['figure.max_open_warning'] = 0

import matplotlib.pyplot as plt
import os, json, math, time, requests
import pandas as pd, numpy as np
from datetime import datetime, timezone
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from fpdf import FPDF
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from binance.client import Client
from binance.enums import ORDER_TYPE_MARKET, SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_TRAILING_STOP_MARKET

# === Load environment variables ===
load_dotenv()
MODE = os.getenv("MODE", "live")
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# === Risk Settings ===
TRAILING_SL_PCT = 1.5 / 100
MAX_RISK_USDT = 1.0
TAKE_PROFIT_USDT = 0.25
STOP_LOSS_USDT = 0.15
LEVERAGE = 20
CONFIDENCE_THRESHOLD = 80

# === Initialize Binance Client ===
client = Client(API_KEY, API_SECRET) if API_KEY and API_SECRET else None

# === File Paths ===
PDF_DIR = "output/reports"
TRADES_DIR = "output/trades"
for d in [PDF_DIR, TRADES_DIR]:
    os.makedirs(d, exist_ok=True)

def utcnow_iso(): return datetime.now(timezone.utc).isoformat()
def now_str(): return datetime.now().strftime("%Y%m%d_%H%M%S")

def get_symbols(limit=200):
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    response = requests.get(url).json()
    symbols = [s['symbol'] for s in response['symbols']
               if s['contractType'] == 'PERPETUAL' and s['symbol'].endswith('USDT')]
    return symbols[:limit]

def fetch_ohlcv(symbol, interval="1h", limit=300):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'num_trades',
        'taker_base_vol', 'taker_quote_vol', 'ignore'
    ])
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def prepare_indicators(df):
    df['ema9'] = EMAIndicator(df['close'], window=9).ema_indicator()
    df['ema21'] = EMAIndicator(df['close'], window=21).ema_indicator()
    df['ma200'] = df['close'].rolling(window=200).mean()
    macd = MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()
    rsi = RSIIndicator(df['close'], window=14)
    df['rsi'] = rsi.rsi()
    bb = BollingerBands(df['close'])
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['volume_ma20'] = df['volume'].rolling(20).mean()
    df['vol_spike'] = df['volume'] > df['volume_ma20'] * 1.5
    return df

             

def get_balance():
    if not client:
        return 0
    bal = client.futures_account_balance()
    usdt_bal = next((b for b in bal if b['asset'] == 'USDT'), None)
    return float(usdt_bal['balance']) if usdt_bal else 0

def place_trade(symbol, side, entry_price, stop_loss, take_profit):
    if client is None:
        print(f"[Trade Skipped] {symbol}: No API key provided, skipping live trade.")
        return

    try:
        order_side = SIDE_BUY if side == "long" else SIDE_SELL
        sl_distance = abs(entry_price - stop_loss)
        risk_amount = MAX_RISK_USDT
        qty = risk_amount / sl_distance
        qty = round(qty, 3)
        qty = max(qty, 0.001)

        client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        print(f"[TRADE] {symbol} {side.upper()} @ {entry_price} Qty: {qty}")

        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side == 'long' else SIDE_BUY,
            type=FUTURE_ORDER_TYPE_TRAILING_STOP_MARKET,
            quantity=qty,
            callbackRate=1.5,
            reduceOnly=True
        )

        trade_data = {
            "symbol": symbol,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "side": side,
            "qty": qty,
            "leverage": LEVERAGE,
            "risk_amount": MAX_RISK_USDT,
            "reward_amount": TAKE_PROFIT_USDT,
            "timestamp": utcnow_iso()
        }

        trade_path = os.path.join(TRADES_DIR, f"{symbol}_trade_{now_str()}.json")
        with open(trade_path, "w") as f:
            json.dump(trade_data, f, indent=2)

    except Exception as e:
        print(f"[Trade Error] {symbol}: {e}")



    try:
        order_side = SIDE_BUY if side == "long" else SIDE_SELL
        sl_distance = abs(entry_price - stop_loss)
        risk_amount = MAX_RISK_USDT
        qty = risk_amount / sl_distance
        qty = round(qty, 3)
        qty = max(qty, 0.001)

        client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        print(f"[TRADE] {symbol} {side.upper()} @ {entry_price} Qty: {qty}")

        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side == 'long' else SIDE_BUY,
            type=FUTURE_ORDER_TYPE_TRAILING_STOP_MARKET,
            quantity=qty,
            callbackRate=1.5,
            reduceOnly=True
        )

        trade_data = {
            "symbol": symbol,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "side": side,
            "qty": qty,
            "leverage": LEVERAGE,
            "risk_amount": MAX_RISK_USDT,
            "reward_amount": TAKE_PROFIT_USDT,
            "timestamp": utcnow_iso()
        }

        trade_path = os.path.join(TRADES_DIR, f"{symbol}_trade_{now_str()}.json")
        with open(trade_path, "w") as f:
            json.dump(trade_data, f, indent=2)

    except Exception as e:
        print(f"[Trade Error] {symbol}: {e}")



def generate_signal(symbol):
    try:
        df = fetch_ohlcv(symbol)
        if df.shape[0] < 200:
            return None
        df = prepare_indicators(df)

        last = df.iloc[-1]
        close = last['close']
        bb_upper = last['bb_upper']
        bb_lower = last['bb_lower']
        bb_width = abs(bb_upper - bb_lower)

        if bb_width == 0 or np.isnan(bb_width):
            return None

        trend = "bullish" if last['ema9'] > last['ema21'] else "bearish"
        side = "long" if trend == "bullish" else "short"

       # === Parameters ===
        SL_PCT = 0.15
        LEVERAGE = 20
        BASE_CAPITAL = 1.0  # 1 USDT per trade
        quantity = BASE_CAPITAL / close

        # === Calculate liquidation price first ===
        if side == "long":
            liquidation_price = round(close * (1 - 1 / LEVERAGE), 4)
            stop_loss = max(close * (1 - SL_PCT), liquidation_price * 1.01)  # always above liq price
            take_profit = close + (close - stop_loss) * 2  # keep 2:1 RRR
            risk = close - stop_loss
            reward = take_profit - close
        else:
            liquidation_price = round(close * (1 + 1 / LEVERAGE), 4)
            stop_loss = min(close * (1 + SL_PCT), liquidation_price * 0.99)  # always below liq price
            take_profit = close - (stop_loss - close) * 2
            risk = stop_loss - close
            reward = close - take_profit


        if risk <= 0 or reward <= 0:
            return None

        # === Calculations ===
        forecast_pnl_usdt = round(reward * quantity, 4)
        roi_pct = round((forecast_pnl_usdt / BASE_CAPITAL) * 100, 2)
        rrr = round(reward / risk, 2)
        daily_pnl = round((close - df["close"].iloc[-96]) / df["close"].iloc[-96] * 100, 2)
        confidence = 90 if (trend == "bullish" and daily_pnl > 0) or (trend == "bearish" and daily_pnl < 0) else 70

        signal = {
            "symbol": symbol,
            "entry": round(close, 4),
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "side": side,
            "confidence": confidence,
            "trend": trend,
            "regime": "trend" if trend == "bullish" else "scalp",
            "risk_reward": rrr,
            "forecast_pnl_usdt": forecast_pnl_usdt,
            "forecast_pnl_pct": roi_pct,
            "roi_pct": roi_pct,
            "daily_pnl": daily_pnl,
            "liquidation_price": liquidation_price,
            "quantity_usdt": BASE_CAPITAL,
            "close": round(close, 4),
            "timestamp": utcnow_iso()
        }

        return signal

    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        return None





def scan_and_generate_signals():
    print("📊 Scanning Binance Futures symbols for signals...")
    symbols = get_symbols(limit=200)

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(generate_signal, symbols))
        
    signals = [s for s in results if s and s["risk_reward"] >= 2 and s["confidence"] >= 80]

    top5 = sorted(signals, key=lambda x: x["roi_pct"], reverse=True)[:5]

    others = [s for s in signals if s not in top5]

    return top5, others, signals




def save_combined_pdf(top5, others, output_file, timeframe):

    class PDF(FPDF):
        def __init__(self, timeframe):
            super().__init__()
            self.timeframe = timeframe
            
        def header(self):
            self.set_font("Arial", "B", 14)
            self.cell(0, 10, f"Combined Signal Report ({self.timeframe.upper()})", ln=True, align="C")
            self.ln(10)

        def add_table(self, title, signals):
            self.set_font("Arial", "B", 12)
            self.cell(0, 12, title, ln=True)

            # Define column widths and calculate total width
            widths = [20, 12, 20, 20, 20, 20, 20, 16, 16, 16, 22]
            headers = [
                "Symbol", "Side", "Entry", "Close", "SL", "TP",
                "PnL (USDT)", "ROI %", "Conf", "RRR", "Liq. Price"
            ]
            table_width = sum(widths)
            page_width = self.w - self.l_margin - self.r_margin
            x_offset = (page_width - table_width) / 2 + self.l_margin  # true center

            # Table Header
            self.set_font("Arial", "B", 9)
            self.set_fill_color(220, 220, 220)
            self.set_x(x_offset)
            for i in range(len(headers)):
                self.cell(widths[i], 8, headers[i], 1, 0, "C", 1)
            self.ln()

            # Table Rows
            self.set_font("Arial", "", 9)
            for s in signals:
                self.set_x(x_offset)
                self.cell(widths[0], 8, s['symbol'], 1)
                self.cell(widths[1], 8, s['side'], 1)
                self.cell(widths[2], 8, str(s['entry']), 1)
                self.cell(widths[3], 8, str(s['close']), 1)
                self.cell(widths[4], 8, str(s['stop_loss']), 1)
                self.cell(widths[5], 8, str(s['take_profit']), 1)
                self.cell(widths[6], 8, str(s['forecast_pnl_usdt']), 1)
                self.cell(widths[7], 8, f"{s['roi_pct']}%", 1)
                self.cell(widths[8], 8, str(s['confidence']), 1)
                self.cell(widths[9], 8, str(s.get('risk_reward', 'N/A')), 1)
                self.cell(widths[10], 8, str(s['liquidation_price']), 1)
                self.ln()

        def add_signal(self, signal):
            self.set_font("Arial", "B", 12)
            self.cell(0, 12, f"Symbol: {signal['symbol']}", ln=True)
            self.set_font("Arial", "", 10)
            self.multi_cell(0, 7,
                f"Entry:         {signal['entry']}\n"
                f"Close:         {signal['close']}\n"
                f"Stop Loss:     {signal['stop_loss']}\n"
                f"Take Profit:   {signal['take_profit']}\n"
                f"Side:          {signal['side']}\n"
                f"Confidence:    {signal['confidence']}\n"
                f"Risk-Reward:   {signal.get('risk_reward', 'N/A')}\n"
                f"Trend:         {signal['trend']}\n"
                f"Regime:        {signal['regime']}\n"
                f"Forecast PnL:  {signal['forecast_pnl_usdt']} USDT\n"
                f"ROI:           {signal['roi_pct']}%\n"
                f"Liquidation:   {signal['liquidation_price']}\n"
                f"Daily PnL:     {signal['daily_pnl']}%\n"
                f"Time:          {signal['timestamp']}\n"
            )
            self.ln(10)

    pdf = PDF(timeframe)  # ✅ Pass the timeframe here
    pdf.add_page()
    pdf.add_table("Top 5 Signals (Ranked by ROI)", top5)
    pdf.ln(10)
    pdf.add_table("Other Signals", others)
    pdf.output(output_file)
    print(f"[PDF] Combined signal report saved to {output_file} (Timeframe: {timeframe})")




def execute_top5_trades(top5):
    for s in top5:
        place_trade(
            symbol=s['symbol'],
            side=s['side'],
            entry_price=s['entry'],
            stop_loss=s['stop_loss'],
            take_profit=s['take_profit']
        )


	
	
if __name__ == "__main__":
    # === Define timeframe for scan ===
    timeframe = "1h"

    # === Scan and generate signals ===
    top5, others, all_signals = scan_and_generate_signals()

    # === Display Ranked Top 5 Signals ===
    print(f"\n📊 ✅ Scan Complete ({timeframe}). Top 5 Signals (Ranked by Forecast PnL):")
    for i, s in enumerate(top5, 1):
        print(f"{i}. {s['symbol']:10} | Side: {s['side']:5} | Entry: {s['entry']:>8} | SL: {s['stop_loss']:>8} | TP: {s['take_profit']:>8} "
              f"| Forecast: {s['forecast_pnl_usdt']:>6} USDT ({s['forecast_pnl_pct']:>5}%) | Trend: {s['trend']}")

    # === Execute Trades If API Keys Exist ===
    if top5:
        if client:
            execute_top5_trades(top5)
        else:
            print("[INFO] API key not found — skipping trade execution.")

    # === Save PDF Report (chartless version)
    pdf_report_file = os.path.join(PDF_DIR, f"signals_report_{timeframe}_{now_str()}.pdf")
    save_combined_pdf(top5, others, pdf_report_file, timeframe)


   

    # === Config Summary ===
    print("\n🛠️  Final Configuration Summary")
    print(f"[INFO] Timeframe:          {timeframe}")
    print(f"[INFO] Mode:               {MODE}")
    print(f"[INFO] Max risk per trade: {MAX_RISK_USDT} USDT")
    print(f"[INFO] Default TP:         {TAKE_PROFIT_USDT} USDT")
    print(f"[INFO] Default SL:         {STOP_LOSS_USDT} USDT")
    print(f"[INFO] Reports directory:  {PDF_DIR}")
    print(f"[INFO] Trades directory:   {TRADES_DIR}")

    print(f"\n✅ [DONE] {len(all_signals)} signals saved | Top 5 executed (if API available) | PDF report generated ({timeframe}) |\n")
