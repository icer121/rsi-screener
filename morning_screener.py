"""Утренний отчёт: перепроданность + развороты + отскоки + календарь."""
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests
from scan_moex import (scan, scan_reverse, scan_rebound,
                       RSI_THRESHOLD, LOOKBACK_DAYS)
from smartlab_events import get_events, get_all_upcoming_events

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MSK = timezone(timedelta(hours=3))


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


def send_long(token, chat_id, message):
    if len(message) <= 4000:
        send_telegram(token, chat_id, message)
        return
    parts, cur = [], ""
    for line in message.split('\n'):
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


def events_block(tickers, limit=15):
    if not tickers:
        return ""
    events = get_events(tickers[:limit])
    lines = []
    for t in tickers[:limit]:
        evs = events.get(t, [])
        if not evs:
            continue
        lines.append(f"\n*{t}*")
        for ev in evs:
            lines.append(f"  • {ev['date']} — {ev['text']}")
    if not lines:
        return ""
    return "\n\n📅 *События по сигналам:*\n" + "\n".join(lines)


def format_oversold(result):
    now = datetime.now(MSK).strftime('%d.%m.%Y %H:%M')
    signals = result['signals']
    checked = result['checked']
    if not signals:
        return (f"📊 *RSI Screener* — перепроданность\n_{now} МСК_\n\n"
                f"🔍 Сигналов RSI < {RSI_THRESHOLD} не найдено.\n"
                f"Проверено: {checked}")

    seen = {}
    for s in signals:
        if s['ticker'] not in seen:
            seen[s['ticker']] = s
    uniq = list(seen.values())

    msg = (f"📊 *RSI Screener* — перепроданность\n_{now} МСК_\n\n"
           f"🟢 RSI < {RSI_THRESHOLD}\n"
           f"Сигналов: *{len(signals)}* | Уникальных: *{len(uniq)}*\n"
           f"Проверено: {checked}\n\n")

    lines, chunk = [], []
    for s in uniq:
        chunk.append(f"`{s['ticker']}` {s['rsi']:.1f}")
        if len(chunk) == 4:
            lines.append(" · ".join(chunk))
            chunk = []
    if chunk:
        lines.append(" · ".join(chunk))
    msg += "\n".join(lines)
    msg += events_block([s['ticker'] for s in uniq[:15]])
    return msg


def format_reverse(result):
    now = datetime.now(MSK).strftime('%d.%m.%Y %H:%M')
    crossovers = result['crossovers']
    checked = result['checked']
    if not crossovers:
        return (f"📊 *RSI Screener* — развороты\n_{now} МСК_\n\n"
                f"🔍 Разворотов не найдено.\nПроверено: {checked}")

    seen = {}
    for c in crossovers:
        if c['ticker'] not in seen:
            seen[c['ticker']] = c
    uniq = list(seen.values())

    msg = (f"📊 *RSI Screener* — развороты\n_{now} МСК_\n\n"
           f"🔴 RSI пересёк {RSI_THRESHOLD} снизу вверх\n"
           f"Сигналов: *{len(crossovers)}* | Уникальных: *{len(uniq)}*\n"
           f"Проверено: {checked}\n\n")

    for c in uniq:
        msg += (f"`{c['ticker']}` {c['rsi_before']:.1f}→"
                f"{c['rsi_after']:.1f} · {c['price']:.2f}\n")

    msg += events_block([c['ticker'] for c in uniq[:10]])
    return msg


def format_rebound(result):
    now = datetime.now(MSK).strftime('%d.%m.%Y %H:%M')
    rebounds = result['rebounds']
    checked = result['checked']
    if not rebounds:
        return (f"📊 *RSI Screener* — отскоки\n_{now} МСК_\n\n"
                f"🔍 Отскоков не найдено.\nПроверено: {checked}")

    seen = {}
    for r in rebounds:
        if r['ticker'] not in seen:
            seen[r['ticker']] = r
    uniq = list(seen.values())

    msg = (f"📊 *RSI Screener* — отскоки\n_{now} МСК_\n\n"
           f"🚀 RSI был < 30, поднялся > 33\n"
           f"Сигналов: *{len(rebounds)}* | Уникальных: *{len(uniq)}*\n"
           f"Проверено: {checked}\n\n")

    for r in uniq:
        msg += (f"`{r['ticker']}` {r['low_rsi']:.1f} → {r['rebound_rsi']:.1f}\n"
                f"  цена: {r['low_price']:.2f} → {r['rebound_price']:.2f}\n\n")

    msg += events_block([r['ticker'] for r in uniq[:10]])
    return msg


def format_all_events(events):
    now = datetime.now(MSK).strftime('%d.%m.%Y %H:%M')
    if not events:
        return (f"📅 *Календарь MOEX*\n_{now} МСК_\n\n"
                f"🔍 Событий не найдено.")

    msg = (f"📅 *Календарь MOEX* — предстоящие события\n"
           f"_{now} МСК_\n\n"
           f"Всего: *{len(events)}* (ближайшие 30 дней)\n\n")

    by_date = {}
    for ev in events:
        by_date.setdefault(ev['date'], []).append(ev)

    for date_str in sorted(by_date.keys(),
                            key=lambda d: datetime.strptime(d, '%d.%m.%Y')):
        items = by_date[date_str]
        msg += f"*{date_str}*\n"
        for ev in items:
            text = re.sub(r'\d{2}\.\d{2}\.\d{4}', '', ev['text']).strip()
            text = re.sub(r'\s+', ' ', text)[:120]
            msg += f"  `{ev['ticker']}` — {text}\n"
        msg += "\n"

    return msg


def main():
    print(f"Старт: {datetime.now(MSK).strftime('%Y-%m-%d %H:%M МСК')}")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Нет TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
        sys.exit(1)

    try:
        print("1/4 Перепроданность...")
        result = scan()
        print(f"  Сигналов: {len(result['signals'])}")
        send_long(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, format_oversold(result))

        print("2/4 Развороты...")
        rev = scan_reverse()
        print(f"  Разворотов: {len(rev['crossovers'])}")
        if rev['crossovers']:
            send_long(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, format_reverse(rev))

        print("3/4 Отскоки...")
        reb = scan_rebound()
        print(f"  Отскоков: {len(reb['rebounds'])}")
        if reb['rebounds']:
            send_long(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, format_rebound(reb))

        print("4/4 Календарь по всему рынку...")
        all_events = get_all_upcoming_events(days_ahead=30, max_events=50)
        print(f"  Событий: {len(all_events)}")
        send_long(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
                  format_all_events(all_events))

        print("Готово")

    except Exception as e:
        import traceback
        err = traceback.format_exc()[:3500]
        print(err)
        try:
            send_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
                          f"❌ *Ошибка*\n```\n{err}\n```")
        except Exception:
            pass


if __name__ == "__main__":
    main()