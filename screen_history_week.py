import json
import time
import urllib.request
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

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

print(f"Total tickers to screen: {len(tickers)}")

def fetch_candles(secid):
    # Last 30 days of 1H candles
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{secid}/candles.json?interval=60&from=2026-08-25"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode('utf-8'))
            return secid, d['candles']['data']
    except Exception as e:
        return secid, []

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
        begin = group[0][6]
        end = group[-1][7]
        candles_4h.append({'open': o, 'high': h, 'low': l, 'close': c, 'volume': v, 'begin': begin, 'end': end})
    return candles_4h

def calc_rsi_series(closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    rsi_list = [None] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_list.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_list.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_list.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_list.append(100.0 - (100.0 / (1.0 + rs)))
    return rsi_list

signals = []
total_checked = 0

# 7 days ago cutoff for signals
# Current system date context: 2026-09-26
cutoff_date = datetime.strptime("2026-09-26", "%Y-%m-%d") - timedelta(days=7)

print("Fetching and scanning last 30 days for historical signals (RSI < 7 days)...")

with ThreadPoolExecutor(max_workers=16) as executor:
    future_to_secid = {executor.submit(fetch_candles, secid): secid for secid in tickers}
    for future in as_completed(future_to_secid):
        secid, candles_1h = future.result()
        if not candles_1h or len(candles_1h) < 100:
            continue
        candles_4h = aggregate_4h(candles_1h)
        if len(candles_4h) < 20:
            continue
        
        total_checked += 1
        closes = [c['close'] for c in candles_4h]
        rsi_series = calc_rsi_series(closes, 14)
        
        if not rsi_series or all(r is None for r in rsi_series):
            continue
        
        current_rsi = rsi_series[-1]
        
        # Check last 42 candles (approx 7 days)
        for i in range(max(0, len(candles_4h) - 42), len(candles_4h)):
            c = candles_4h[i]
            r = rsi_series[i]
            if r is not None and r < 27:
                dt_str = c['begin']
                # parse datetime
                try:
                    dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    if dt_obj >= cutoff_date:
                        signals.append({
                            'secid': secid,
                            'dt': dt_str,
                            'dt_obj': dt_obj,
                            'rsi_then': r,
                            'price_then': c['close'],
                            'current_rsi': current_rsi
                        })
                except Exception:
                    pass

# Sort signals by date descending (newest first)
signals.sort(key=lambda x: x['dt_obj'], reverse=True)

output_lines = []
output_lines.append("Тикер | Дата и время | RSI тогда | Цена тогда | Текущий RSI")
for s in signals:
    output_lines.append(f"{s['secid']} | {s['dt']} | {s['rsi_then']:.2f} | {s['price_then']} | {s['current_rsi']:.2f}")

print(f"\nВсего проверено акций: {total_checked}")
print(f"Найдено сигналов за последнюю неделю: {len(signals)}")
print("\n".join(output_lines))

# Save result to rsi_history_week.txt
with open("rsi_history_week.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output_lines) + "\n")

print("\nРезультат сохранен в rsi_history_week.txt")
