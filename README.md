--

## 📊 Bybit Multi-Timeframe Signal Scanner

This Python script scans the **top 100 USDT Perpetual Futures** on Bybit every 15 minutes, using multi-timeframe technical analysis. It generates high-confidence trading signals, exports them as a PDF report, and optionally sends the top 5 picks to **Discord**.

---

### 🚀 Features

* ✅ Scans top 100 USDT perpetual futures by 24h volume
* 📈 Multi-timeframe analysis (`15m`, `1h`, `4h`)
* 📊 Uses popular indicators:

  * EMA 9, EMA 21, SMA 20
  * RSI, MACD, Bollinger Bands
  * ATR-based volatility filter
* 🧠 Classifies signals into `Trend`, `Swing`, or `Scalp`
* 📤 Sends Top 5 signals to Discord
* 📄 Exports Top 20 signals to a styled PDF report
* 🔂 Rescans every 15 minutes (looping mode)

---

### 📦 Requirements

* Python 3.8+
* `requests`
* `fpdf`
* `pytz`

Install dependencies:

```bash
pip install requests fpdf pytz
```

---

### ⚙️ Configuration

You can customize the following constants in the script:

```python
RISK_PCT = 0.15                 # Risk % per trade
ACCOUNT_BALANCE = 100          # Account balance in USD
LEVERAGE = 20                  # Leverage used
ENTRY_BUFFER_PCT = 0.002       # Buffer for trailing entries
MIN_VOLUME = 1000              # Minimum 1h volume
MIN_ATR_PCT = 0.001            # Minimum ATR % filter
RSI_ZONE = (20, 80)            # RSI inclusion zone
INTERVALS = ['15', '60', '240']# Timeframes to evaluate
MAX_SYMBOLS = 100              # Max number of symbols to scan
DISCORD_WEBHOOK_URL = "..."    # Discord webhook (optional)
```

---

### 📂 Output

* PDF file: `signals_HHMM.pdf` (updated every scan)
* Discord message: Top 5 signals with full metadata

---

### 📋 Signal Fields

Each signal includes:

* **Symbol**: e.g., BTCUSDT
* **Type**: Trend, Swing, or Scalp
* **Side**: LONG or SHORT
* **Score**: Confidence score (0-100)
* **Entry**: Optimal entry price
* **TP/SL**: Take profit and stop loss
* **Trail**: Trailing price for entry
* **Market**: Current market price
* **BB Slope**: Bollinger Band direction (Up/Down/No)
* **Margin/Liq**: Calculated using leverage
* **Time**: Timestamp in UTC+3

---

### 🛠️ How It Works

1. Gets top 100 Bybit USDT pairs sorted by volume.
2. For each symbol:

   * Fetches latest 200 candles per interval.
   * Computes indicators.
   * Filters based on volume, ATR, and RSI.
   * Confirms trend alignment across timeframes.
   * Assigns signal score.
3. Displays top 5 in terminal.
4. Exports top 20 to a PDF.
5. Sends top 5 to Discord.
6. Waits 15 minutes and repeats.

---

### 🖥️ Running the Script

```bash
python signal_scanner.py
```

You’ll see terminal logs like:

```
🔍 Scanning Bybit USDT Futures for filtered signals...

==================== BTCUSDT ====================
📊 TYPE: Trend     📈 SIDE: LONG     🏆 SCORE: 87.0%
💵 ENTRY: 58652.12 🎯 TP: 59541.91   🛡️ SL: 57762.33
💱 MARKET: 58723.0 📍 BB: Up         🔄 Trail: 58539.22
⚖️ MARGIN: 0.75    ⚠️ LIQ: 55719.51
⏰ TIME: 2025-07-18 13:00 UTC+3
```

---

### 🌐 Discord Notifications

To enable, replace `DISCORD_WEBHOOK_URL` in the script with your own webhook URL.

You can create a webhook from your Discord server under:
**Server Settings → Integrations → Webhooks → New Webhook**

---

### 📌 Notes

* Runs indefinitely, every 15 minutes.
* If no valid signals are found, it will wait and retry.
* PDF report and Discord alert are only generated if at least one signal passes the filters.

---

### 📜 License

This project is open source and free to use under the MIT License.

---
