import fs from 'fs';
import axios from 'axios';
import { Hyperliquid } from 'hyperliquid';
import { ethers } from 'ethers';
import { FPDF } from 'fpdf-lite';

// === CONFIG ===
const SIGNAL_FOLDER = './signals';
const TRADE_FOLDER = './trades';
const SIGNAL_PDF = 'all_signals.pdf';
const TRADE_PDF = 'opened_trades.pdf';

const RISK_AMOUNT = 1;
const LEVERAGE = 20;
const TP_PERCENT = 0.25;
const SL_PERCENT = 0.10;

// === UTILS ===
function ensureDir(dir) { if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true }); }
function saveJSON(obj, folder, symbol) {
  ensureDir(folder);
  fs.writeFileSync(`${folder}/${symbol}.json`, JSON.stringify(obj, null, 2));
}

// PDF formatting (similar to previous)
function formatSignal(s, i) { /* ... */ }
function savePDF(signals, filename, title) { /* ... */ }

// === INDICATORS & SIGNAL LOGIC ===

// === INDICATORS ===
function ema(values, period) {
  const k = 2 / (period + 1);
  const emas = [values.slice(0, period).reduce((a, x) => a + x, 0) / period];
  for (let i = period; i < values.length; i++) {
    const next = values[i] * k + emas[emas.length - 1] * (1 - k);
    emas.push(next);
  }
  return Array(period - 1).fill(null).concat(emas);
}

function sma(values, period) {
  return values.map((_, i) => i < period - 1 ? null : values.slice(i + 1 - period, i + 1).reduce((a, x) => a + x, 0) / period);
}

function computeRsi(closes, period = 14) {
  const deltas = closes.slice(1).map((c, i) => c - closes[i]);
  const gains = [], losses = [];
  for (const d of deltas) {
    gains.push(Math.max(d, 0));
    losses.push(Math.max(-d, 0));
  }
  if (gains.length < period) return 50;
  const avgGain = gains.slice(-period).reduce((a, x) => a + x, 0) / period;
  const avgLoss = losses.slice(-period).reduce((a, x) => a + x, 0) / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return Math.round((100 - (100 / (1 + rs))) * 100) / 100;
}

// === FETCHING DATA ===
async function getSymbols(limit = 50) {
  try {
    const { data } = await axios.get('https://fapi.binance.com/fapi/v1/exchangeInfo', { timeout: 5000 });
    return data.symbols
      .filter(s => s.contractType === 'PERPETUAL' && s.symbol.endsWith('USDT'))
      .map(s => s.symbol)
      .slice(0, limit);
  } catch (e) {
    console.error('Error fetching symbols:', e);
    return [];
  }
}

async function fetchOhlcv(symbol) {
  try {
    const { data } = await axios.get(`https://fapi.binance.com/fapi/v1/klines?symbol=${symbol}&interval=1h&limit=100`, { timeout: 5000 });
    return data.map(x => [parseFloat(x[2]), parseFloat(x[3]), parseFloat(x[4]), parseFloat(x[5])]);
  } catch (e) {
    console.error(`Error fetching OHLCV for ${symbol}:`, e);
    return [];
  }
}

// === SIGNAL GENERATION ===
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
    const qty = riskPer ? +(RISK_AMOUNT / riskPer).toFixed(6) : 0;
    const sig = {
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
    };
    sigs.push(sig);
  };

  build('Trend', regime === 'trend' && ema9.at(-1) > ema21.at(-1), 90);
  build('Mean-Reversion', regime === 'mean_reversion' && (rsi < 40), 85);
  if (regime === 'scalp' && volumes.at(-1) > volumes.slice(-20).reduce((a, v) => a + v, 0) / 20 * 1.5) {
    build('Scalp', true, 80);
  }

  return sigs;
}

// === ONBOARD & EXECUTION ===
async function main() {
  // 1) Connect Web3 wallet via Ethers (e.g. MetaMask)
  if (!window.ethereum) throw new Error('Install MetaMask');
  const provider = new ethers.providers.Web3Provider(window.ethereum);
  const signer = provider.getSigner();
  await provider.send('eth_requestAccounts', []);
  console.log('Connected:', await signer.getAddress());

  // 2) Init Hyperliquid SDK with wallet signer
  const sdk = new Hyperliquid({ provider, signer, testnet: false, enableWs: true });

  // 3) Generate and save trading signals
  const allSignals = []; 
  const symbols = await fetchSymbols(); // port get_symbols logic via REST
  for (const sym of symbols) {
    const signals = await analyze(sym);
    for (const sig of signals) {
      saveJSON(sig, SIGNAL_FOLDER, sig.symbol);
      allSignals.push(sig);
    }
  }

  // 4) Execute top-5 trades
  const top5 = allSignals.sort((a, b) => b.score - a.score).slice(0, 5);
  const openedTrades = [];

  for (const sig of top5) {
    try {
      // Place leveraged limit IOC as "market-like" trade :contentReference[oaicite:7]{index=7}
      const side = sig.side === 'LONG' ? 'buy' : 'sell';
      const price = side === 'buy' ? await sdk.info.getBestAsk(sig.symbol + '-PERP') :
                                      await sdk.info.getBestBid(sig.symbol + '-PERP');
      const order = await sdk.exchange.placeOrder({
        marketName: sig.symbol + '-PERP',
        side,
        price,
        size: sig.position_size,
        tif: 'IOC',
        leverage: LEVERAGE
      });

      sig.order = order;
      sig.trade_status = 'OPENED';
      saveJSON(sig, TRADE_FOLDER, sig.symbol);
      openedTrades.push(sig);
      console.log('Executed:', sig.symbol);

    } catch (err) {
      console.error('Trade failed:', sig.symbol, err);
    }
  }

  // 5) Create PDF reports
  savePDF(allSignals, SIGNAL_PDF, 'All Signals');
  savePDF(openedTrades, TRADE_PDF, 'Opened Trades');
}

main().catch(console.error);
