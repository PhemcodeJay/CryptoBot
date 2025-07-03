import fs from 'fs';
import axios from 'axios';
import { SpotClient, FuturesClient } from '@binance/connector';
import { FPDF } from 'fpdf-lite';

// === CONFIG ===
const API_KEY = 'your_api_key';
const API_SECRET = 'your_api_secret';
const futures = new FuturesClient(API_KEY, API_SECRET);

let capital = 1; // Starting capital
const risk_pct = 0.01;
const LEVERAGE = 20;
const TP_PERCENT = 0.25;
const SL_PERCENT = 0.10;
const SIMULATION = false;

const SIGNAL_FOLDER = './signals';
const TRADE_FOLDER = './trades';
const SIGNAL_PDF = 'all_signals.pdf';
const TRADE_PDF = 'opened_trades.pdf';
const LOG_FILE = './capital_log.json';

// === UTILS ===
function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function timestamp() {
  return new Date().toISOString().replace(/T/, ' ').split('.')[0] + ' UTC';
}

function getRiskAmount() {
  return +(capital * risk_pct).toFixed(6);
}

function updateCapital(result, percentGainLoss) {
  if (result === 'win') capital *= 1 + percentGainLoss;
  else if (result === 'loss') capital *= 1 - percentGainLoss;
  capital = +capital.toFixed(6);

  const log = fs.existsSync(LOG_FILE) ? JSON.parse(fs.readFileSync(LOG_FILE)) : [];
  log.push({ capital, result, time: timestamp() });
  fs.writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
}

function saveJSON(obj, folder, symbol) {
  ensureDir(folder);
  fs.writeFileSync(`${folder}/${symbol.toLowerCase()}.json`, JSON.stringify(obj, null, 2));
}

function formatSignal(s, i) {
  return [
    i ? `${i}. ${s.symbol} [${s.timeframe}] | ${s.side} | ${s.strategy}` : s.symbol,
    '―'.repeat(50),
    `Entry      : ${s.entry}`,
    `SL / TP    : ${s.sl} / ${s.tp}`,
    `Qty        : ${s.position_size}`,
    `Forecast % : ${s.forecast_pnl.toFixed(2)}% | Conf: ${s.confidence}%`,
    `RSI        : ${s.rsi} | Trend: ${s.trend} | Regime: ${s.regime}`,
    `Score      : ${s.score}`,
    `Time       : ${s.timestamp}`,
    ''
  ].join('\n');
}

function savePDF(signals, fileName, title) {
  if (!signals.length) return;
  const pdf = new FPDF();
  pdf.AddPage();
  pdf.SetFont('Courier', '', 10);
  pdf.Text(10, 10, title);
  let y = 20;
  signals.forEach((s, i) => {
    const lines = formatSignal(s, i + 1).split('\n');
    lines.forEach(line => {
      pdf.Text(10, y, line);
      y += 5;
    });
    y += 5;
  });
  pdf.Output('F', fileName);
  console.log(`📄 PDF saved: ${fileName}`);
}

// === INDICATORS ===
function ema(values, period) {
  const k = 2 / (period + 1);
  const emas = [values.slice(0, period).reduce((a, x) => a + x, 0) / period];
  for (let i = period; i < values.length; i++) {
    emas.push(values[i] * k + emas[emas.length - 1] * (1 - k));
  }
  return Array(period - 1).fill(null).concat(emas);
}

function sma(values, period) {
  return values.map((_, i) => i < period - 1 ? null : values.slice(i + 1 - period, i + 1).reduce((a, x) => a + x, 0) / period);
}

function computeRsi(closes, period = 14) {
  const deltas = closes.slice(1).map((c, i) => c - closes[i]);
  const gains = deltas.map(d => Math.max(d, 0));
  const losses = deltas.map(d => Math.max(-d, 0));
  const avgGain = gains.slice(-period).reduce((a, x) => a + x, 0) / period;
  const avgLoss = losses.slice(-period).reduce((a, x) => a + x, 0) / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return Math.round((100 - (100 / (1 + rs))) * 100) / 100;
}

// === DATA FETCHING ===
async function getSymbols(limit = 50) {
  try {
    const { data } = await axios.get('https://fapi.binance.com/fapi/v1/exchangeInfo');
    return data.symbols
      .filter(s => s.contractType === 'PERPETUAL' && s.symbol.endsWith('USDT'))
      .map(s => s.symbol)
      .slice(0, limit);
  } catch (e) {
    console.error('Symbol fetch error:', e);
    return [];
  }
}

async function fetchOhlcv(symbol) {
  try {
    const { data } = await axios.get(`https://fapi.binance.com/fapi/v1/klines?symbol=${symbol}&interval=1h&limit=100`);
    return data.map(x => [parseFloat(x[2]), parseFloat(x[3]), parseFloat(x[4]), parseFloat(x[5])]);
  } catch (e) {
    console.error(`OHLCV error: ${symbol}`, e);
    return [];
  }
}

// === SIGNAL GEN ===
async function analyze(symbol) {
  const data = await fetchOhlcv(symbol);
  if (data.length < 60) return [];

  const closes = data.map(x => x[2]);
  const volumes = data.map(x => x[3]);
  const close = closes.at(-1);

  const ema9 = ema(closes, 9);
  const ema21 = ema(closes, 21);
  const ma20 = sma(closes, 20);
  const ma200 = sma(closes, 50);
  const rsi = computeRsi(closes);
  const dailyChange = ((close - closes.at(-2)) / closes.at(-2)) * 100;

  const trend = ma20.at(-1) > ma200.at(-1) ? 'bullish' : 'bearish';
  const regime = Math.abs((ma20.at(-1) - ma200.at(-1)) / ma200.at(-1)) > 0.01
    ? 'trend'
    : (rsi < 35 || rsi > 65 ? 'mean_reversion' : 'scalp');

  const sigs = [];
  const build = (name, cond, conf) => {
    if (!cond) return;
    const side = trend === 'bullish' ? 'long' : 'short';
    const entry = close;
    const liquidation = side === 'long' ? entry * (1 - 1 / LEVERAGE) : entry * (1 + 1 / LEVERAGE);
    const sl = side === 'long'
      ? Math.max(entry * (1 - SL_PERCENT), liquidation * 1.05)
      : Math.min(entry * (1 + SL_PERCENT), liquidation * 0.95);
    const tp = side === 'long' ? entry * (1 + TP_PERCENT) : entry * (1 - TP_PERCENT);
    const riskPer = Math.abs(entry - sl);
    const qty = riskPer ? +(getRiskAmount() / riskPer).toFixed(6) : 0;
    sigs.push({
      symbol,
      timeframe: '1h',
      side: side.toUpperCase(),
      entry: +entry.toFixed(8),
      sl: +sl.toFixed(8),
      tp: +tp.toFixed(8),
      rsi,
      trend,
      regime,
      confidence: conf,
      position_size: qty,
      forecast_pnl: +(TP_PERCENT * conf).toFixed(2),
      score: +(conf + rsi / 2).toFixed(2),
      strategy: name,
      daily_change: +dailyChange.toFixed(2),
      timestamp: timestamp()
    });
  };

  build('Trend', regime === 'trend' && ema9.at(-1) > ema21.at(-1), 90);
  build('Mean-Reversion', regime === 'mean_reversion' && rsi < 40, 85);
  if (regime === 'scalp' && volumes.at(-1) > Math.max(...volumes.slice(-20)) * 1.1) {
    build('Scalp', true, 80);
  }

  return sigs;
}

// === TRADE EXECUTION ===
async function placeTrade(signal) {
  if (SIMULATION) {
    signal.trade_status = 'SIMULATED';
    return signal;
  }

  try {
    await futures.changeLeverage(signal.symbol, LEVERAGE);
    const side = signal.side === 'LONG' ? 'BUY' : 'SELL';

    const order = await futures.newOrder(signal.symbol, side, 'MARKET', {
      quantity: signal.position_size
    });

    await futures.newOrder(signal.symbol, side === 'BUY' ? 'SELL' : 'BUY', 'LIMIT', {
      quantity: signal.position_size,
      price: signal.tp.toString(),
      timeInForce: 'GTC'
    });

    await futures.newOrder(signal.symbol, side === 'BUY' ? 'SELL' : 'BUY', 'STOP_MARKET', {
      stopPrice: signal.sl.toString(),
      quantity: signal.position_size
    });

    signal.binance_order_id = order.orderId;
    signal.trade_status = 'OPENED';
    console.log(`✅ TRADE OPENED: ${signal.symbol}`);
    return signal;
  } catch (err) {
    console.error(`❌ TRADE ERROR: ${signal.symbol}`, err);
    return null;
  }
}

// === TRADE TRACKER ===
async function checkTradeResult(signal) {
  try {
    const trades = await futures.getAccountTrades(signal.symbol);
    const last = trades.filter(t => t.orderId === signal.binance_order_id).at(-1);
    if (!last) return null;

    const price = parseFloat(last.price);
    const entry = signal.entry;
    if (signal.side === 'LONG') {
      if (price >= signal.tp) return 'win';
      if (price <= signal.sl) return 'loss';
    } else {
      if (price <= signal.tp) return 'win';
      if (price >= signal.sl) return 'loss';
    }
    return null;
  } catch (e) {
    console.error(`Result check error: ${signal.symbol}`, e);
    return null;
  }
}

// === MAIN ===
(async () => {
  ensureDir(SIGNAL_FOLDER);
  ensureDir(TRADE_FOLDER);
  const allSignals = [];

  const symbols = await getSymbols();
  for (const symbol of symbols) {
    const sigs = await analyze(symbol);
    for (const s of sigs) {
      saveJSON(s, SIGNAL_FOLDER, s.symbol);
      allSignals.push(s);
    }
  }

  if (!allSignals.length) return console.log('No signals found.');
  const top5 = allSignals.sort((a, b) => b.score - a.score).slice(0, 5);
  const opened = [];

  for (const s of top5) {
    const res = await placeTrade(s);
    if (res) {
      const result = await checkTradeResult(res);
      if (result) updateCapital(result, result === 'win' ? TP_PERCENT : SL_PERCENT);
      saveJSON(res, TRADE_FOLDER, res.symbol);
      opened.push(res);
    }
  }

  savePDF(allSignals, SIGNAL_PDF, 'All Signals');
  savePDF(opened, TRADE_PDF, 'Opened Trades');
})();
