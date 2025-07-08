const fs = require('fs');
const axios = require('axios');
const PDFDocument = require('pdfkit');
const { DateTime } = require('luxon');
const snoowrap = require('snoowrap');

// === CONFIG ===
const CAPITAL_FILE = 'capital.json';
const TRADE_LOG_FILE = 'trades_history.json';
const SIGNAL_DIR = 'signals';
const TRADE_DIR = 'trades';
const START_CAPITAL = 10.0;
const MAX_LOSS_PCT = 15;
const TP_PERCENT = 0.25;
const SL_PERCENT = 0.10;
const LEVERAGE = 20;

const DISCORD_WEBHOOK_URL = 'https://discord.com/api/webhooks/...';
const REDDIT_CREDS = {
  userAgent: 'cryptopilot_bot',
  clientId: 'YOUR_CLIENT_ID',
  clientSecret: 'YOUR_CLIENT_SECRET',
  username: 'YOUR_USERNAME',
  password: 'YOUR_PASSWORD',
};

if (!fs.existsSync(SIGNAL_DIR)) fs.mkdirSync(SIGNAL_DIR);
if (!fs.existsSync(TRADE_DIR)) fs.mkdirSync(TRADE_DIR);

// === DISCORD + REDDIT ===
function postToDiscord(message) {
  axios.post(DISCORD_WEBHOOK_URL, { content: message }).catch(e => console.log('❌ Discord Error:', e.message));
}

function postToReddit(title, body) {
  try {
    const reddit = new snoowrap(REDDIT_CREDS);
    reddit.getSubreddit('YourSubreddit').submitSelfpost({ title, text: body });
  } catch (e) {
    console.log('❌ Reddit Error:', e.message);
  }
}

// === TECHNICAL INDICATORS ===
function ema(values, period) {
  let k = 2 / (period + 1);
  let emaArr = [], emaPrev = values.slice(0, period).reduce((a, b) => a + b) / period;
  emaArr.push(emaPrev);
  for (let i = period; i < values.length; i++) {
    emaPrev = values[i] * k + emaPrev * (1 - k);
    emaArr.push(emaPrev);
  }
  return Array(period - 1).fill(null).concat(emaArr);
}

function sma(values, period) {
  return values.map((_, i) =>
    i < period - 1 ? null : values.slice(i + 1 - period, i + 1).reduce((a, b) => a + b) / period
  );
}

function computeRSI(closes, period = 14) {
  let gains = [], losses = [];
  for (let i = 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    gains.push(Math.max(delta, 0));
    losses.push(Math.max(-delta, 0));
  }
  if (gains.length < period) return 50;
  const avgGain = gains.slice(-period).reduce((a, b) => a + b) / period;
  const avgLoss = losses.slice(-period).reduce((a, b) => a + b) / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return Math.round(100 - (100 / (1 + rs)) * 100) / 100;
}

function calculateMACD(values, fast = 12, slow = 26, signal = 9) {
  const emaFast = ema(values, fast);
  const emaSlow = ema(values, slow);
  const macd = emaFast.map((v, i) => v !== null && emaSlow[i] !== null ? v - emaSlow[i] : null);
  const macdFiltered = macd.filter(x => x !== null);
  const signalLine = ema(macdFiltered, signal);
  const paddedSignal = Array(macd.length - signalLine.length).fill(null).concat(signalLine);
  const histogram = macd.map((v, i) => (v !== null && paddedSignal[i] !== null ? v - paddedSignal[i] : null));
  return { macd, signal: paddedSignal, histogram };
}

function calculateBollingerBands(values, period = 20, stdDev = 2) {
  const smaVals = sma(values, period);
  return values.map((_, i) => {
    if (i < period - 1) return [null, null, null];
    const mean = smaVals[i];
    const std = Math.sqrt(values.slice(i + 1 - period, i + 1).reduce((acc, val) => acc + Math.pow(val - mean, 2), 0) / period);
    return [mean + stdDev * std, mean, mean - stdDev * std];
  });
}

// === HELPERS ===
function loadCapital() {
  if (!fs.existsSync(CAPITAL_FILE)) return START_CAPITAL;
  return JSON.parse(fs.readFileSync(CAPITAL_FILE)).balance || START_CAPITAL;
}

function saveCapital(balance) {
  fs.writeFileSync(CAPITAL_FILE, JSON.stringify({ balance: parseFloat(balance.toFixed(4)) }));
}

function logTrade(trade) {
  let trades = fs.existsSync(TRADE_LOG_FILE) ? JSON.parse(fs.readFileSync(TRADE_LOG_FILE)) : [];
  trades.push(trade);
  fs.writeFileSync(TRADE_LOG_FILE, JSON.stringify(trades, null, 2));
}

function todayLossPct() {
  if (!fs.existsSync(TRADE_LOG_FILE)) return 0;
  const trades = JSON.parse(fs.readFileSync(TRADE_LOG_FILE));
  const today = DateTime.utc().toISODate();
  const loss = trades.filter(t => t.timestamp.startsWith(today) && t.pnl < 0).reduce((sum, t) => sum + t.pnl, 0);
  const capital = loadCapital();
  return capital > 0 ? Math.round((-loss / capital) * 10000) / 100 : 100;
}

function formatSignal(s) {
  return `📈 ${s.symbol} [${s.side}] | ${s.strategy}
Entry: ${s.entry} | TP: ${s.tp} | SL: ${s.sl}
Confidence: ${s.confidence}% | Score: ${s.score}
Regime: ${s.regime} | Trend: ${s.trend}
Timestamp: ${s.timestamp}`;
}

function saveJSON(data, path) {
  fs.writeFileSync(path, JSON.stringify(data, null, 2));
}

function savePDF(filename, items, title) {
  const doc = new PDFDocument();
  doc.pipe(fs.createWriteStream(filename));
  doc.fontSize(14).text(title, { align: 'center' }).moveDown();
  items.forEach(item => {
    Object.entries(item).forEach(([k, v]) => {
      doc.fontSize(10).text(`${k}: ${v}`);
    });
    doc.moveDown();
  });
  doc.end();
}

// === DEMO TRADE ===
function simulateTrade(signal) {
  const entry = signal.entry, tp = signal.tp;
  const side = signal.side;
  let capital = loadCapital();
  const riskAmount = capital * 0.02;
  const riskPerUnit = Math.abs(entry - signal.sl);
  const qty = riskPerUnit > 0 ? parseFloat((riskAmount / riskPerUnit).toFixed(4)) : 0;
  const pnl = parseFloat(((side === 'LONG' ? tp - entry : entry - tp) * qty).toFixed(4));
  capital += pnl;
  saveCapital(capital);
  const trade = {
    symbol: signal.symbol,
    side,
    entry,
    exit: tp,
    qty,
    pnl,
    strategy: signal.strategy,
    timestamp: DateTime.utc().toISO({ suppressMilliseconds: true }),
  };
  logTrade(trade);
  return trade;
}
async function main() {
  console.log('🚀 Running CryptoPilot...');
  const balance = loadCapital();
  const lossPct = todayLossPct();

  if (lossPct >= MAX_LOSS_PCT) {
    console.log(`❌ Max daily loss of ${MAX_LOSS_PCT}% hit (${lossPct}%) — Trading paused.`);
    return;
  }

  const allSignals = [];
  const trades = [];

  const symbols = await getSymbols(30);
  for (const symbol of symbols) {
    const signals = await analyze(symbol);
    allSignals.push(...signals);
  }

  if (allSignals.length === 0) {
    console.log('⚠️ No signals found.');
    return;
  }

  const top5 = allSignals
    .sort((a, b) => b.score - a.score || b.confidence - a.confidence)
    .slice(0, 5);

  for (let i = 0; i < top5.length; i++) {
    const s = top5[i];
    console.log(`#${i + 1} ${s.symbol} ${s.side} | Score: ${s.score} | ${s.strategy}`);
    const msg = formatSignal(s);
    saveJSON(s, `${SIGNAL_DIR}/${s.symbol}.json`);
    postToDiscord(msg);
    postToReddit(`CryptoPilot Signal - ${s.symbol} #${i + 1}`, msg);

    const trade = simulateTrade(s);
    saveJSON(trade, `${TRADE_DIR}/${s.symbol}.json`);
    trades.push(trade);

    postToDiscord(`🟢 Executed Trade: ${trade.symbol} | PnL: ${trade.pnl}`);
    postToReddit(`CryptoPilot Trade - ${trade.symbol}`, `Trade Summary:\n\`\`\`\n${JSON.stringify(trade, null, 2)}\n\`\`\``);
  }

  savePDF('all_signals.pdf', allSignals, '📊 All Signals');
  savePDF('all_trades.pdf', trades, '💹 Executed Trades');

  console.log('\n✅ Run complete. Trades and signals exported.\n');
}

main();
