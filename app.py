"""Streamlit-интерфейс. Использует единый движок scan_moex.py."""
import os

import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

from scan_moex import scan, RSI_THRESHOLD, LOOKBACK_DAYS

st.set_page_config(page_title="RSI Screener MOEX", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #131722; }
    section[data-testid="stSidebar"] { background-color: #1e222d; }
    h1, h2, h3 { color: #d1d4dc; }
</style>
""", unsafe_allow_html=True)


# ============ ПАРОЛЬ ============
try:
    required_password = st.secrets["app"]["password"]
except Exception:
    required_password = ""

if required_password:
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        st.title("🔒 RSI Screener MOEX")
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            pwd = st.text_input("Пароль", type="password")
            if st.button("Войти", type="primary", use_container_width=True):
                if pwd == required_password:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("❌ Неверный пароль")
        st.stop()


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True, "✅ Отправлено"
    except requests.exceptions.RequestException as e:
        return False, f"❌ Ошибка: {e}"


st.sidebar.title("⚙️ Управление")

with st.sidebar.expander("📨 Настройки Telegram", expanded=False):
    default_token = os.environ.get("TELEGRAM_TOKEN", "")
    default_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not default_token:
        try:
            default_token = st.secrets["telegram"]["token"]
            default_chat = st.secrets["telegram"]["chat_id"]
        except Exception:
            pass
    telegram_token = st.text_input("Токен бота", type="password",
                                    value=default_token)
    telegram_chat_id = st.text_input("Chat ID", value=default_chat)

st.sidebar.markdown("---")
st.sidebar.caption(f"Порог RSI: {RSI_THRESHOLD} · Период: {LOOKBACK_DAYS} дней")

if st.sidebar.button("🚀 Запустить скрининг", type="primary",
                      use_container_width=True):
    progress = st.progress(0.0, text="Загрузка...")

    def cb(i, total, secid):
        progress.progress(i / total, text=f"{i}/{total} — {secid}")

    result = scan(progress_callback=cb)
    progress.empty()
    st.session_state.data = result

    if result['signals'] and telegram_token and telegram_chat_id:
        seen = {}
        for s in result['signals']:
            if s['ticker'] not in seen:
                seen[s['ticker']] = s
        lines, chunk = [], []
        for s in seen.values():
            chunk.append(f"`{s['ticker']}` {s['rsi']:.1f}")
            if len(chunk) == 4:
                lines.append(" · ".join(chunk))
                chunk = []
        if chunk:
            lines.append(" · ".join(chunk))
        msg = (f"📊 *RSI Screener MOEX (веб)*\n\n"
               f"Сигналов: *{len(result['signals'])}* | "
               f"Уникальных: *{len(seen)}*\n\n" + "\n".join(lines))
        ok, info = send_telegram(telegram_token, telegram_chat_id, msg)
        (st.sidebar.success if ok else st.sidebar.error)(info)

if st.sidebar.button("🗑 Очистить", use_container_width=True):
    st.session_state.pop('data', None)
    st.rerun()

if st.sidebar.button("🚪 Выйти", use_container_width=True):
    st.session_state.authenticated = False
    st.rerun()


st.title("📈 RSI Screener MOEX")
st.caption(f"Сигналы RSI < {RSI_THRESHOLD} на 4H за {LOOKBACK_DAYS} дней")

if 'data' not in st.session_state:
    st.info("👈 Нажмите **«Запустить скрининг»**")
else:
    data = st.session_state.data
    signals = data['signals']

    c1, c2, c3 = st.columns(3)
    c1.metric("Проверено", data['checked'])
    c2.metric("Сигналов", len(signals))
    c3.metric("Уникальных", len(set(s['ticker'] for s in signals)))

    if not signals:
        st.warning("Сигналы не найдены.")
    else:
        rows = []
        for s in signals:
            chg = ((s['current_price'] - s['price']) / s['price']) * 100
            rows.append({
                'Тикер': s['ticker'],
                'Дата/время': s['datetime'],
                'RSI тогда': round(s['rsi'], 2),
                'Цена тогда': round(s['price'], 2),
                'Цена сейчас': round(s['current_price'], 2),
                'Изм. %': round(chg, 2),
                'Текущий RSI': round(s['current_rsi'], 2),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True, height=350)

        st.markdown("---")
        uniq = sorted(set(s['ticker'] for s in signals))
        selected = st.selectbox("График", uniq)
        if selected:
            widget = f"""
            <iframe
                src="https://s.tradingview.com/widgetembed/?symbol=MOEX%3A{selected}&interval=240&theme=dark&style=1&locale=ru&hide_side_toolbar=0&allow_symbol_change=1&withdateranges=1&studies=RSI%40tv-basicstudies&range=84"
                width="100%" height="750" frameborder="0"
                allowtransparency="true" scrolling="no">
            </iframe>
            """
            components.html(widget, height=780, scrolling=False)