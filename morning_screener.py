"""Утренний скрининг: использует единый движок scan_moex.py."""
import os
import sys
from datetime import datetime, timezone, timedelta

import requests
from scan_moex import scan, RSI_THRESHOLD, LOOKBACK_DAYS

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


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


def format_message(result):
    msk = timezone(timedelta(hours=3))
    now = datetime.now(msk).strftime('%d.%m.%Y %H:%M')
    signals = result['signals']
    checked = result['checked']

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


def main():
    print(f"Старт: {datetime.now()}")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Нет TELEGRAM_TOKEN или TELEGRAM_CHAT_ID")
        sys.exit(1)

    try:
        result = scan()
        print(f"Найдено сигналов: {len(result['signals'])}")
        msg = format_message(result)
        send_long(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, msg)
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