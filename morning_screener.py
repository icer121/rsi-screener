"""Утренний скрининг MOEX: RSI < 27 на 4H за неделю. Отправка в Telegram.

Автономный — сам качает данные с MOEX ISS, сам считает RSI,
сам находит сигналы. Не зависит от других скриптов.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

RSI_THRESHOLD = 30
LOOKBACK_DAYS = 7
HISTORY_DAYS = 30
PAUSE_SEC = 0.1


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Telegram error: {e}")
        return False


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


def scan():
    print("Получение списка тикеров...")
    tickers = get_tickers()
    print(f"Всего тикеров: {len(tickers)}")

    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    signals = []
    checked = 0

    for i, secid in enumerate(tickers, 1):
        candles, cols = fetch_candles(secid, HISTORY_DAYS)
        if not candles or len(candles) < 100:
            continue
        c4 = aggregate_4h(candles, cols)
        if len(c4) < 20:
            continue
        closes = [c['close'] for c in c4]
        rsi = calc_rsi(closes, 14)
        checked += 1

        for j, c in enumerate(c4):
            if rsi[j] is None:
                continue
            try:
                dt = datetime.strptime(c['begin'], '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            if dt >= cutoff and rsi[j] < RSI_THRESHOLD:
                signals.append({
                    'ticker': secid,
                    'datetime': c['begin'],
                    'rsi': rsi[j],
                    'price': c['close'],
                    'current_rsi': rsi[-1] if rsi[-1] else 0,
                })
        time.sleep(PAUSE_SEC)
        if i % 50 == 0:
            print(f"  {i}/{len(tickers)}...")

    signals.sort(key=lambda x: x['datetime'], reverse=True)
    print(f"Проверено: {checked}, найдено сигналов: {len(signals)}")
    return signals, checked


def format_message(signals, checked):
    msk = timezone(timedelta(hours=3))
    now = datetime.now(msk).strftime('%d.%m.%Y %H:%M')

    if not signals:
        return (f"📊 *RSI Screener MOEX* — утренний отчёт\n"
                f"_{now} МСК_\n\n"
                f"🔍 Сигналов RSI < {RSI_THRESHOLD} на 4H за "
                f"{LOOKBACK_DAYS} дней не найдено.\n\n"
                f"Проверено акций: {checked}")

    seen = {}
    for s in signals:
        if s['ticker'] not in seen:
            seen[s['ticker']] = s

    uniq = list(seen.values())
    header = (f"📊 *RSI Screener MOEX* — утренний отчёт\n"
              f"_{now} МСК_\n\n"
              f"Сигналов: *{len(signals)}* | "
              f"Уникальных тикеров: *{len(uniq)}*\n"
              f"Проверено акций: {checked}\n\n")

    lines = []
    chunk = []
    for s in uniq:
        chunk.append(f"`{s['ticker']}` {s['rsi']:.1f}")
        if len(chunk) == 4:
            lines.append(" · ".join(chunk))
            chunk = []
    if chunk:
        lines.append(" · ".join(chunk))

    return header + "\n".join(lines)


def send_long(token, chat_id, message):
    if len(message) <= 4000:
        send_telegram(token, chat_id, message)
        return
    lines = message.split('\n')
    parts, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 4000:
            parts.append(cur)
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    for i, p in enumerate(parts):
        if len(parts) > 1:
            p += f"\n\n_Часть {i+1}/{len(parts)}_"
        send_telegram(token, chat_id, p)


def main():
    print(f"Старт: {datetime.now()}")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Нет TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
        return

    try:
        signals, checked = scan()
        msg = format_message(signals, checked)
        send_long(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg)
        print("Готово")
    except Exception as e:
        import traceback
        err = traceback.format_exc()[:3500]
        print(err)
        try:
            send_telegram(
                TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
                f"❌ *Ошибка скринера*\n\n```\n{err}\n```"
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
