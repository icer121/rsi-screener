import json
import time
import urllib.request
import sys

ref_path = r'C:\Users\icer\.gemini\tmp\gemini-test\tool-outputs\session-5d005bc1-561c-4308-ba2c-1ede8eff4450\mcp_moex-mcp_iss_reference__call_1044067.txt'

with open(ref_path, 'r', encoding='utf-8') as f:
    text = f.read().strip()

if text.startswith('<untrusted_context>'):
    text = text[len('<untrusted_context>'):].strip()
if text.endswith('</untrusted_context>'):
    text = text[:-len('</untrusted_context>')].strip()

data = json.loads(text)
sec = data['securities']
cols = sec['columns']
secid_idx = cols.index('SECID')
sectype_idx = cols.index('SECTYPE')

tickers = []
for row in sec['data']:
    stype = str(row[sectype_idx])
    secid = row[secid_idx]
    if stype in ('1', '2'):
        tickers.append(secid)

def fetch_candles(secid):
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{secid}/candles.json?interval=60&from=2026-08-25"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode('utf-8'))
            return d['candles']['data']
    except Exception as e:
        return None

def aggregate_4h(candles_1h):
    candles_4h = []
    for i in range(0, len(candles_1h), 4):
        group = candles_1h[i:i+4]
        if not group:
            continue
        o = group[0][0]
        c = group[-1][1]
        h = max(row[2] for row in group)
        l = min(row[3] for row in group)
        v = sum(row[5] for row in group)
        candles_4h.append({'open': o, 'high': h, 'low': l, 'close': c, 'volume': v})
    return candles_4h

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def calc_ema(closes, period=50):
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * k + ema
    return ema

results = []
batch_size = 30
pause_sec = 3

total_checked = 0
passed_count = 0

for b_idx in range(0, len(tickers), batch_size):
    batch = tickers[b_idx:b_idx+batch_size]
    for secid in batch:
        candles_1h = fetch_candles(secid)
        if not candles_1h or len(candles_1h) < 200:
            continue
        candles_4h = aggregate_4h(candles_1h)
        if len(candles_4h) < 55:
            continue
        
        closes = [c['close'] for c in candles_4h]
        rsi = calc_rsi(closes, 14)
        ema = calc_ema(closes, 50)
        
        if rsi is None or ema is None:
            continue
        
        total_checked += 1
        current_price = closes[-1]
        
        if rsi < 27 and current_price > ema:
            passed_count += 1
            results.append((secid, rsi, current_price))
            
    if b_idx + batch_size < len(tickers):
        time.sleep(pause_sec)

print("Тикер | RSI(4H) | Цена")
for secid, rsi, price in results:
    print(f"{secid} | {rsi:.2f} | {price}")

print(f"\nВсего проверено акций: {total_checked}")
print(f"Прошло фильтр (RSI < 27 И Цена > EMA(50)): {passed_count}")
