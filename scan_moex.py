"""Единый движок MOEX. Параллельная загрузка + МСК + диагностика."""
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

RSI_THRESHOLD = 30
LOOKBACK_DAYS = 7
HISTORY_DAYS = 30
MAX_WORKERS = 4  # снижено с 10 — MOEX банит за >5

MSK = timezone(timedelta(hours=3))


def get_tickers():
    url = ("https://iss.moex.com/iss/engines/stock/markets/shares/"
           "boards/TQBR/securities.json")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    sec = data['securities']
    cols = sec['columns']
    secid_i = cols.index('SECID')
    sectype_i = cols.index('SECTYPE')
    return [row[secid_i] for row in sec['data']
            if str(row[sectype_i]) in ('1', '2')]


def fetch_candles(secid, days):
    from_date = (datetime.now(MSK) - timedelta(days=days)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
           f"securities/{secid}/candles.json"
           f"?interval=60&from={from_date}")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.loads(resp.read().decode('utf-8'))
            return secid, d['candles']['data'], d['candles']['columns']
    except Exception as e:
        return secid, None, str(e)  # возвращаем текст ошибки


def aggregate_4h(candles, columns):
    if not candles or len(candles) < 4:
        return []
    idx = {c: i for i, c in enumerate(columns)}
    result = []
    for i in range(0, len(candles) - 3, 4):
        chunk = candles[i:i+4]
        result.append({
            'open': chunk[0][idx['open']],
            'close': chunk[-1][idx['close']],
            'high': max(c[idx['high']] for c in chunk),
            'low': min(c[idx['low']] for c in chunk),
            'begin': chunk[0][idx['begin']],
        })
    return result


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    rsi = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    rsi[period] = 100 if al == 0 else 100 - (100 / (1 + ag/al))
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        rsi[i+1] = 100 if al == 0 else 100 - (100 / (1 + ag/al))
    return rsi


# Диагностические счётчики
stats = {'ok': 0, 'no_data': 0, 'short': 0, 'error': 0, 'errors': []}


def process_ticker(secid, cutoff):
    result = fetch_candles(secid, HISTORY_DAYS)
    if len(result) == 3:
        secid, candles, cols = result
    else:
        secid, candles, cols = result[0], result[1], None

    if candles is None:
        stats['error'] += 1
        if len(stats['errors']) < 5:
            stats['errors'].append((secid, cols))
        return None
    if len(candles) < 100:
        stats['short'] += 1
        return None

    c4 = aggregate_4h(candles, cols)
    if len(c4) < 20:
        stats['no_data'] += 1
        return None

    closes = [c['close'] for c in c4]
    rsi = calc_rsi(closes, 14)
    stats['ok'] += 1

    ticker_signals = []
    for j, c in enumerate(c4):
        if rsi[j] is None:
            continue
        try:
            dt = datetime.strptime(c['begin'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=MSK)
        except Exception:
            continue
        if dt >= cutoff and rsi[j] < RSI_THRESHOLD:
            ticker_signals.append({
                'datetime': c['begin'],
                'rsi': rsi[j],
                'price': c['close'],
            })

    if not ticker_signals:
        return {'checked': 1, 'signals': [], 'chart': None}

    signals = [{
        'ticker': secid,
        'datetime': sp['datetime'],
        'rsi': sp['rsi'],
        'price': sp['price'],
        'current_rsi': rsi[-1] if rsi[-1] else 0,
        'current_price': closes[-1],
    } for sp in ticker_signals]

    return {
        'checked': 1,
        'signals': signals,
        'chart': {
            'dates': [c['begin'] for c in c4],
            'close': closes,
            'rsi': rsi,
            'signals': ticker_signals,
        }
    }


def scan(progress_callback=None):
    for k in stats:
        stats[k] = 0 if isinstance(stats[k], int) else []
    tickers = get_tickers()
    cutoff = datetime.now(MSK) - timedelta(days=LOOKBACK_DAYS)
    signals = []
    charts = {}
    checked = 0
    processed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_ticker, secid, cutoff): secid
                   for secid in tickers}
        for future in as_completed(futures):
            processed += 1
            if progress_callback:
                progress_callback(processed, len(tickers), futures[future])
            try:
                r = future.result()
                if r:
                    checked += r['checked']
                    signals.extend(r['signals'])
                    if r['chart'] and r['signals']:
                        charts[r['signals'][0]['ticker']] = r['chart']
            except Exception as e:
                stats['errors'].append((futures[future], str(e)))

    signals.sort(key=lambda x: x['datetime'], reverse=True)
    return {
        'signals': signals,
        'charts': charts,
        'checked': checked,
        'stats': dict(stats),
    }


if __name__ == "__main__":
    start = time.time()
    print(f"Время: {datetime.now(MSK).strftime('%Y-%m-%d %H:%M МСК')}")
    print(f"Потоков: {MAX_WORKERS}")
    print("Загрузка...")

    def show_progress(i, total, secid):
        if i % 30 == 0 or i == total:
            print(f"  {i}/{total} — {secid}")

    result = scan(progress_callback=show_progress)
    elapsed = time.time() - start

    print(f"\nВремя: {elapsed:.1f} сек")
    print(f"Проверено: {result['checked']}")
    print(f"Сигналов: {len(result['signals'])}")
    print(f"Уникальных тикеров: {len(set(s['ticker'] for s in result['signals']))}")
    print("\n=== ДИАГНОСТИКА ===")
    print(f"  OK:           {result['stats']['ok']}")
    print(f"  Мало свечей:  {result['stats']['short']}")
    print(f"  Мало 4H:      {result['stats']['no_data']}")
    print(f"  Ошибок:       {result['stats']['error']}")
    if result['stats']['errors']:
        print("  Примеры ошибок:")
        for secid, err in result['stats']['errors'][:3]:
            print(f"    {secid}: {err}")