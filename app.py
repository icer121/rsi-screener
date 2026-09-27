"""RSI Screener MOEX + Telegram (работает локально и на Hugging Face)."""
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

st.set_page_config(page_title="RSI Screener MOEX", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #131722; }
    section[data-testid="stSidebar"] { background-color: #1e222d; }
    h1, h2, h3 { color: #d1d4dc; }
</style>
""", unsafe_allow_html=True)

# Относительный путь — работает и на Windows, и на Linux
SCRIPT_PATH = Path(__file__).parent / "screen_history_week.py"


# ============ TELEGRAM ============
def send_telegram_message(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True, "✅ Отправлено"
    except requests.exceptions.RequestException as e:
        return False, f"❌ Ошибка: {e}"


def send_all_tickers(token, chat_id, signals):
    """Отправляет все уникальные тикеры, разбивая на сообщения."""
    seen = {}
    for s in signals:
        if s['Тикер'] not in seen:
            seen[s['Тикер']] = s

    unique_list = list(seen.values())
    total_signals = len(signals)
    total_unique = len(unique_list)

    header = (f"📊 *RSI Screener MOEX*\n\n"
              f"Сигналов: *{total_signals}* | "
              f"Уникальных тикеров: *{total_unique}*\n\n")

    lines = []
    chunk = []
    for s in unique_list:
        chunk.append(f"`{s['Тикер']}` {s['RSI тогда']:.1f}")
        if len(chunk) == 4:
            lines.append(" · ".join(chunk))
            chunk = []
    if chunk:
        lines.append(" · ".join(chunk))

    max_len = 4000
    messages = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            messages.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        messages.append(current)

    success_count = 0
    for i, msg_part in enumerate(messages):
        if len(messages) > 1:
            msg_part += f"\n\n_Часть {i+1}/{len(messages)}_"
        ok, _ = send_telegram_message(token, chat_id, msg_part)
        if ok:
            success_count += 1

    return success_count, len(messages)


# ============ СКРИПТ ============
def run_screener():
    if not SCRIPT_PATH.exists():
        return None, f"Файл {SCRIPT_PATH} не найден"
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=900, cwd=str(SCRIPT_PATH.parent),
        )
        return r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return None, "Таймаут (>15 минут)"
    except Exception as e:
        return None, str(e)


def parse_signals(stdout):
    """Парсит строки: TICKER | дата время | RSI | цена | тек.RSI"""
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
        sig = {
            'Тикер': ticker,
            'Дата/время': parts[1],
            'RSI тогда': rsi,
            'Цена тогда': price,
        }
        if len(parts) >= 5:
            try:
                sig['Текущий RSI'] = float(parts[4].replace(',', '.'))
            except ValueError:
                pass
        signals.append(sig)
    return signals


# ============ САЙДБАР ============
st.sidebar.title("⚙️ Управление")

with st.sidebar.expander("📨 Настройки Telegram", expanded=False):
    # 1) Пробуем переменные окружения (Hugging Face / облако)
    default_token = os.environ.get("TELEGRAM_TOKEN", "")
    default_chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    # 2) Если пусто — пробуем secrets.toml (локальный запуск)
    if not default_token:
        try:
            default_token = st.secrets["telegram"]["token"]
            default_chat = st.secrets["telegram"]["chat_id"]
        except (KeyError, FileNotFoundError, Exception):
            pass

    telegram_token = st.text_input(
        "Токен бота", type="password", value=default_token,
        key='tg_token_input'
    )
    telegram_chat_id = st.text_input(
        "Chat ID", value=default_chat,
        key='tg_chat_input'
    )

    if default_token and default_chat:
        st.caption("✅ Токен загружен автоматически")
    else:
        st.caption("⚠️ Введите токен и chat_id вручную")

    if st.button("📤 Тест уведомления"):
        if telegram_token and telegram_chat_id:
            ok, info = send_telegram_message(
                telegram_token, telegram_chat_id,
                "🔔 *Тест RSI Screener*\n\n"
                "Если вы видите это сообщение — настройка работает!"
            )
            if ok:
                st.success(info)
            else:
                st.error(info)
        else:
            st.warning("Заполните токен и chat_id")

st.sidebar.markdown("---")

if st.sidebar.button("🚀 Запустить скрининг", type="primary",
                      use_container_width=True):
    with st.spinner("Скрипт работает… Подождите 2–4 минуты"):
        stdout, stderr = run_screener()
        st.session_state.stdout = stdout
        st.session_state.stderr = stderr
        st.session_state.signals = parse_signals(stdout) if stdout else []

    signals = st.session_state.signals
    if signals and telegram_token and telegram_chat_id:
        with st.spinner("Отправка в Telegram..."):
            sent, total = send_all_tickers(
                telegram_token, telegram_chat_id, signals
            )
        if sent == total:
            st.sidebar.success(f"✅ Отправлено сообщений: {sent}")
        else:
            st.sidebar.error(f"Отправлено {sent}/{total}")

if st.sidebar.button("🗑 Очистить результаты", use_container_width=True):
    for k in ['stdout', 'stderr', 'signals']:
        st.session_state.pop(k, None)
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(f"Скрипт: {SCRIPT_PATH.name}")


# ============ ОСНОВНАЯ ОБЛАСТЬ ============
st.title("📈 RSI Screener MOEX")
st.caption("Сигналы RSI < 27 на 4H за неделю + график TradingView")

if 'signals' not in st.session_state:
    st.info("👈 Заполните настройки Telegram и нажмите **«Запустить скрининг»**")
else:
    signals = st.session_state.signals
    if not signals:
        st.warning("Сигналы не найдены.")
        st.code(st.session_state.get('stdout', '')[:5000])
    else:
        c1, c2 = st.columns(2)
        c1.metric("Найдено сигналов", len(signals))
        c2.metric("Уникальных тикеров",
                  len(set(s['Тикер'] for s in signals)))

        df = pd.DataFrame(signals)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=350)

        st.markdown("---")
        st.subheader("📊 График TradingView")

        uniq = sorted(set(s['Тикер'] for s in signals))
        col1, col2 = st.columns([2, 1])
        with col1:
            selected = st.selectbox("Акция", uniq)
        with col2:
            period = st.radio("Период", ["7д", "14д", "30д"],
                              horizontal=True, index=1)

        bars = {"7д": 42, "14д": 84, "30д": 180}[period]

        if selected:
            widget_html = f"""
            <iframe
                src="https://s.tradingview.com/widgetembed/?symbol=MOEX%3A{selected}&interval=240&theme=dark&style=1&locale=ru&hide_side_toolbar=0&allow_symbol_change=1&withdateranges=1&studies=RSI%40tv-basicstudies&range={bars}"
                width="100%" height="750" frameborder="0"
                allowtransparency="true" scrolling="no">
            </iframe>
            """
            components.html(widget_html, height=780, scrolling=False)

        with st.expander("📜 Полный вывод скрипта"):
            st.code(st.session_state.get('stdout', '')[:15000])