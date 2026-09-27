"""Единый движок скрининга MOEX. МСК + keep-alive + отскоки."""
import os
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

RSI_THRESHOLD = 30
LOOKBACK_DAYS = 7
HISTORY_DAYS = 30
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "4"))

MSK = timezone(timedelta(hours=3))

_session = requests.Session()
_session.headers.update({'User-Agent': 'Mozilla/5.0'})
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=MAX_WORKERS + 2,
    pool_maxsize=MAX_WORKERS + 2,
    max_retries=2,
)
_session.mount('https://', _adapter)
_session.mount('http://', _adapter)


def get_tickers():
    url = ("https://iss.moex.com/iss/engines/stock/markets/shares/"
           "boards/TQBR/securities.json")
    resp = _session.get(url, timeout=20)
    data = resp.json()
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
    try:
        resp = _session.get(url, timeout=20)
        d = resp.json()
        return d['candles']['data'], d['candles']['columns']
    except Exception:
        return None, None


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


# ============ ПРЯМОЙ СКАН (RSI < 30) ============
def process_ticker(secid, cutoff):
    candles, cols = fetch_candles(secid, HISTORY_DAYS)
    if not candles or len(candles) < 100:
        return None
    c4 = aggregate_4h(candles, cols)
    if len(c4) < 20:
        return None
    closes = [c['close'] for c in c4]
    rsi = calc_rsi(closes, 14)

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
    tickers = get_tickers()
    cutoff = datetime.now(MSK) - timedelta(days=LOOKBACK_DAYS)
    signals, charts = [], {}
    checked, processed = 0, 0

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
            except Exception:
                pass

    signals.sort(key=lambda x: x['datetime'], reverse=True)
    return {'signals': signals, 'charts': charts, 'checked': checked}


# ============ РАЗВОРОТЫ (RSI пересёк 30 снизу вверх) ============
def process_ticker_reverse(secid, cutoff):
    candles, cols = fetch_candles(secid, HISTORY_DAYS)
    if not candles or len(candles) < 100:
        return None
    c4 = aggregate_4h(candles, cols)
    if len(c4) < 20:
        return None
    closes = [c['close'] for c in c4]
    rsi = calc_rsi(closes, 14)

    crossovers = []
    for j in range(1, len(c4)):
        if rsi[j] is None or rsi[j-1] is None:
            continue
        try:
            dt = datetime.strptime(c4[j]['begin'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=MSK)
        except Exception:
            continue
        if rsi[j-1] < RSI_THRESHOLD and rsi[j] >= RSI_THRESHOLD:
            if dt >= cutoff:
                crossovers.append({
                    'datetime': c4[j]['begin'],
                    'rsi_before': rsi[j-1],
                    'rsi_after': rsi[j],
                    'price': closes[j],
                })

    if not crossovers:
        return {'checked': 1, 'crossovers': []}

    items = [{
        'ticker': secid,
        'datetime': cv['datetime'],
        'rsi_before': cv['rsi_before'],
        'rsi_after': cv['rsi_after'],
        'price': cv['price'],
        'current_price': closes[-1],
        'current_rsi': rsi[-1] if rsi[-1] else 0,
    } for cv in crossovers]

    return {'checked': 1, 'crossovers': items}


def scan_reverse(progress_callback=None):
    tickers = get_tickers()
    cutoff = datetime.now(MSK) - timedelta(days=LOOKBACK_DAYS)
    crossovers, checked, processed = [], 0, 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_ticker_reverse, secid, cutoff): secid
                   for secid in tickers}
        for future in as_completed(futures):
            processed += 1
            if progress_callback:
                progress_callback(processed, len(tickers), futures[future])
            try:
                r = future.result()
                if r:
                    checked += r['checked']
                    crossovers.extend(r['crossovers'])
            except Exception:
                pass

    crossovers.sort(key=lambda x: x['datetime'], reverse=True)
    return {'crossovers': crossovers, 'checked': checked}


# ============ ОТСКОКИ (RSI был < 30, стал > 33) ============
def process_ticker_rebound(secid, cutoff):
    candles, cols = fetch_candles(secid, HISTORY_DAYS)
    if not candles or len(candles) < 100:
        return None
    c4 = aggregate_4h(candles, cols)
    if len(c4) < 20:
        return None
    closes = [c['close'] for c in c4]
    rsi = calc_rsi(closes, 14)

    rebounds = []
    was_below = False
    low_info = None

    for j, c in enumerate(c4):
        if rsi[j] is None:
            continue
        try:
            dt = datetime.strptime(c['begin'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=MSK)
        except Exception:
            continue
        if dt < cutoff:
            continue

        if rsi[j] < 30:
            was_below = True
            low_info = {
                'date': c['begin'],
                'rsi': rsi[j],
                'price': c['close'],
            }
        elif was_below and rsi[j] > 33:
            rebounds.append({
                'ticker': secid,
                'low_date': low_info['date'],
                'low_rsi': low_info['rsi'],
                'low_price': low_info['price'],
                'rebound_date': c['begin'],
                'rebound_rsi': rsi[j],
                'rebound_price': c['close'],
                'current_rsi': rsi[-1] if rsi[-1] else 0,
                'current_price': closes[-1],
            })
            was_below = False

    if not rebounds:
        return {'checked': 1, 'rebounds': []}
    return {'checked': 1, 'rebounds': rebounds}


def scan_rebound(progress_callback=None):
    tickers = get_tickers()
    cutoff = datetime.now(MSK) - timedelta(days=LOOKBACK_DAYS)
    rebounds, checked, processed = [], 0, 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_ticker_rebound, secid, cutoff): secid
                   for secid in tickers}
        for future in as_completed(futures):
            processed += 1
            if progress_callback:
                progress_callback(processed, len(tickers), futures[future])
            try:
                r = future.result()
                if r:
                    checked += r['checked']
                    rebounds.extend(r['rebounds'])
            except Exception:
                pass

    rebounds.sort(key=lambda x: x['rebound_date'], reverse=True)
    return {'rebounds': rebounds, 'checked': checked}


if __name__ == "__main__":
    start = time.time()
    print(f"Время: {datetime.now(MSK).strftime('%Y-%m-%d %H:%M МСК')}")
    print(f"Потоков: {MAX_WORKERS}")

    print("\n=== Прямой скан ===")
    result = scan()
    print(f"Сигналов: {len(result['signals'])}")
    print(f"Уникальных: {len(set(s['ticker'] for s in result['signals']))}")

    print("\n=== Развороты ===")
    rev = scan_reverse()
    print(f"Разворотов: {len(rev['crossovers'])}")

    print("\n=== Отскоки ===")
    reb = scan_rebound()
    print(f"Отскоков: {len(reb['rebounds'])}")
    print(f"Уникальных: {len(set(r['ticker'] for r in reb['rebounds']))}")

    print(f"\nОбщее время: {time.time()-start:.1f} сек")