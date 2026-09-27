"""Единый движок скрининга MOEX. Используется везде: локально, в Actions, в Streamlit."""
import json
import time
import urllib.request
from datetime import datetime, timedelta

# === НАСТРОЙКИ (единые для всех мест) ===
RSI_THRESHOLD = 30
LOOKBACK_DAYS = 7
HISTORY_DAYS = 30
PAUSE_SEC = 0.1


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
    from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
           f"securities/{secid}/candles.json"
           f"?interval=60&from={from_date}")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.loads(resp.read().decode('utf-8'))
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


def scan(progress_callback=None):
    """Основная функция. Возвращает dict с signals, checked, charts.

    progress_callback(i, total, secid) — опциональный колбэк для UI.
    """
    tickers = get_tickers()
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    signals = []
    charts = {}
    checked = 0

    for i, secid in enumerate(tickers):
        if progress_callback:
            progress_callback(i + 1, len(tickers), secid)

        candles, cols = fetch_candles(secid, HISTORY_DAYS)
        if not candles or len(candles) < 100:
            continue
        c4 = aggregate_4h(candles, cols)
        if len(c4) < 20:
            continue
        closes = [c['close'] for c in c4]
        rsi = calc_rsi(closes, 14)
        checked += 1

        ticker_signals = []
        for j, c in enumerate(c4):
            if rsi[j] is None:
                continue
            try:
                dt = datetime.strptime(c['begin'], '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            if dt >= cutoff and rsi[j] < RSI_THRESHOLD:
                ticker_signals.append({
                    'datetime': c['begin'],
                    'rsi': rsi[j],
                    'price': c['close'],
                })

        if ticker_signals:
            for sp in ticker_signals:
                signals.append({
                    'ticker': secid,
                    'datetime': sp['datetime'],
                    'rsi': sp['rsi'],
                    'price': sp['price'],
                    'current_rsi': rsi[-1] if rsi[-1] else 0,
                    'current_price': closes[-1],
                })
            charts[secid] = {
                'dates': [c['begin'] for c in c4],
                'close': closes,
                'rsi': rsi,
                'signals': ticker_signals,
            }
        time.sleep(PAUSE_SEC)

    signals.sort(key=lambda x: x['datetime'], reverse=True)
    return {
        'signals': signals,
        'charts': charts,
        'checked': checked,
    }


if __name__ == "__main__":
    # Локальный запуск: python scan_moex.py
    print(f"Порог RSI: {RSI_THRESHOLD}, период: {LOOKBACK_DAYS} дней")
    print("Загрузка...")

    def show_progress(i, total, secid):
        if i % 20 == 0 or i == total:
            print(f"  {i}/{total} — {secid}")

    result = scan(progress_callback=show_progress)
    print(f"\nПроверено: {result['checked']}")
    print(f"Сигналов: {len(result['signals'])}")
    print(f"Уникальных тикеров: {len(set(s['ticker'] for s in result['signals']))}")
    print()
    for s in result['signals'][:30]:
        print(f"  {s['ticker']:8} {s['datetime']} RSI={s['rsi']:.1f} Цена={s['price']:.2f}")