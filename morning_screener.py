"""Утренний скрининг RSI: запуск + отправка в Telegram.

Запускается через GitHub Actions по расписанию.
Токены берёт из переменных окружения.
"""
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

SCRIPT_PATH = Path(__file__).parent / "screen_history_week.py"
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
        print(f"Ошибка отправки в Telegram: {e}")
        return False


def run_screener():
    if not SCRIPT_PATH.exists():
        print(f"Файл {SCRIPT_PATH} не найден")
        return None
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=1200,
            cwd=str(SCRIPT_PATH.parent),
        )
        return r.stdout
    except subprocess.TimeoutExpired:
        print("Скрипт работал больше 20 минут и был остановлен")
        return None
    except Exception as e:
        print(f"Ошибка: {e}")
        return None


def parse_signals(stdout):
    if not stdout:
        return []
    signals = []
    for line in stdout.split('\n'):
        line = line.strip()
        if '|' not in line:
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 4:
            continue
        ticker = parts[0]
        if not re.match(r'^[A-Z][A-Z0-9]{2,8}$', ticker):
            continue
        try:
            rsi = float(parts[2].replace(',', '.'))
            price = float(parts[3].replace(',', '.'))
        except (ValueError, IndexError):
            continue
        signals.append({
            'ticker': ticker,
            'datetime': parts[1],
            'rsi': rsi,
            'price': price,
        })
    return signals


def format_message(signals):
    msk = timezone(timedelta(hours=3))
    now = datetime.now(msk).strftime('%d.%m.%Y %H:%M')

    if not signals:
        return (f"📊 *RSI Screener MOEX* — утренний отчёт\n"
                f"_{now} МСК_\n\n"
                f"🔍 Сигналов RSI < 27 на 4H за неделю не найдено.")

    seen = {}
    for s in signals:
        if s['ticker'] not in seen:
            seen[s['ticker']] = s

    unique_list = list(seen.values())

    header = (f"📊 *RSI Screener MOEX* — утренний отчёт\n"
              f"_{now} МСК_\n\n"
              f"Сигналов: *{len(signals)}* | "
              f"Уникальных тикеров: *{len(unique_list)}*\n\n")

    lines = []
    chunk = []
    for s in unique_list:
        chunk.append(f"`{s['ticker']}` {s['rsi']:.1f}")
        if len(chunk) == 4:
            lines.append(" · ".join(chunk))
            chunk = []
    if chunk:
        lines.append(" · ".join(chunk))

    return header + "\n".join(lines)


def send_long_message(token, chat_id, message):
    """Разбивает длинное сообщение на части по 4000 символов."""
    if len(message) <= 4000:
        send_telegram(token, chat_id, message)
        return

    lines = message.split('\n')
    parts = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 4000:
            parts.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current)

    for i, part in enumerate(parts):
        if len(parts) > 1:
            part += f"\n\n_Часть {i+1}/{len(parts)}_"
        send_telegram(token, chat_id, part)


def main():
    print(f"Запуск утреннего скрининга: {datetime.now()}")

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ОШИБКА: TELEGRAM_TOKEN или TELEGRAM_CHAT_ID не заданы")
        sys.exit(1)

    print("Запуск screen_history_week.py...")
    stdout = run_screener()
    signals = parse_signals(stdout)
    print(f"Найдено сигналов: {len(signals)}")

    message = format_message(signals)
    send_long_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, message)
    print("Готово")


if __name__ == "__main__":
    main()