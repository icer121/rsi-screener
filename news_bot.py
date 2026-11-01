# -*- coding: utf-8 -*-
"""
Автономный бот-ассистент для Telegram.
Следит за позициями, присылает новости, даёт советы (DeepSeek / Mercury).
"""
import json
import os
import sys
import time
import requests
from urllib.parse import quote as _urlquote

# Читаем .env рядом со скриптом
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GNEWS_KEY = os.environ.get("GNEWS_API_KEY", "")
NEWSAPI_KEY = os.environ.get("NEWSAPI_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
INCEPTION_KEY = os.environ.get("INCEPTION_API_KEY", "")

STATE_FILE = "state.json"
TICKERS_FILE = "tickers.txt"
MARKET = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json?interval=24&iss.meta=off&iss.only=candles&from={dfrom}&till={till}"


# ---------- Состояние ----------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tickers": [], "last_news": {}, "portfolio": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_portfolio():
    return load_state().get("portfolio", {})


def save_portfolio(portfolio):
    state = load_state()
    state["portfolio"] = portfolio
    save_state(state)


def load_tickers():
    state = load_state()
    return state.get("tickers", [])


def save_tickers(tickers):
    state = load_state()
    state["tickers"] = tickers
    save_state(state)


# ---------- Telegram ----------
def tg_send(text):
    if not TOKEN or not CHAT_ID:
        print("[!] Нет TELEGRAM_TOKEN/CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=20)
    except Exception as e:
        print("[!] tg error:", e)


def tg_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    params = {"timeout": 20}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=25)
        return r.json().get("result", [])
    except Exception:
        return []


# ---------- Новости (GNews → NewsAPI) ----------
def get_news(ticker):
    aliases = {
        "SBER": ["Sberbank", "Сбербанк", "Сбер"],
        "GAZP": ["Gazprom", "Газпром"],
        "LKOH": ["Lukoil", "Лукойл", "ЛУКОЙЛ"],
        "ROSN": ["Rosneft", "Роснефть"],
        "GMKN": ["Norilsk", "Норникель", "НорНикель"],
        "NVTK": ["Novatek", "Новатэк", "НОВАТЭК"],
        "VTBR": ["VTB", "ВТБ"],
        "TATN": ["Tatneft", "Татнефть"],
        "MTSS": ["MTS", "МТС"],
        "AFLT": ["Aeroflot", "Аэрофлот"],
        "SNGSP": ["Surgutneftegas", "Сургутнефтегаз", "Сургут"],
        "ALRS": ["Alrosa", "АЛРОСА"],
        "CHMF": ["Severstal", "Северсталь"],
        "NLMK": ["NLMK", "НЛМК"],
        "MAGN": ["MMK", "ММК"],
        "PLZL": ["Polyus", "Полюс"],
        "RUAL": ["Rusal", "Русал"],
        "PHOR": ["PhosAgro", "Фосагро"],
    }
    names = aliases.get(ticker, [ticker])
    titles = []

    def good_title(t):
        t_low = t.lower()
        if len(t) < 15:
            return False
        # имя компании обязательно
        has_name = any(n.lower() in t_low for n in names)
        if not has_name:
            return False
        # события/важность
        important = (
            "отчёт", "отчет", "прибыль", "выручка", "мсфо", "рсбу", "результат",
            "дивиденд", "выплат", "реестр", "доходность",
            "собрание", "акционер", "сплит", "делистинг", "дата", "закрытие реестра",
            "инсайд", "инсайдер", "внутренн",
            "сделка", "приобрет", "покупк", "продаж", "слияние", "поглощ", "buy", "acquisition", "merger",
            "капитализация", "оценка", "доля", "санкци", "иск", "суд", "штраф",
            "регулятор", "цб", "минфин", "правительство", "повышение", "понижение",
            "рейтинг", "прогноз", "целевая цена", "инвестици", "инвестор",
        )
        return any(w in t_low for w in important)

    # GNews: по русскому имени
    russian = next((n for n in names if any('а' <= c <= 'я' for c in n.lower())), names[0])
    if GNEWS_KEY:
        try:
            q = _urlquote(russian)
            url = f"https://gnews.io/api/v4/search?q={q}&max=10&token={GNEWS_KEY}"
            r = requests.get(url, timeout=15)
            if r.ok:
                for a in r.json().get("articles", []):
                    t = a.get("title") or ""
                    if good_title(t):
                        titles.append(t)
        except Exception:
            pass

    # NewsAPI: по латинице
    if NEWSAPI_KEY and not titles:
        try:
            q = _urlquote(names[0])
            url = f"https://newsapi.org/v2/everything?q={q}&language=ru&sortBy=publishedAt&pageSize=10&apiKey={NEWSAPI_KEY}"
            r = requests.get(url, timeout=15)
            if r.ok:
                for a in r.json().get("articles", []):
                    t = a.get("title") or ""
                    if good_title(t):
                        titles.append(t)
        except Exception:
            pass

    return titles[:5]


# ---------- Цена ----------
def get_price(ticker):
    try:
        till = time.strftime("%Y-%m-%d")
        frm = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
        url = MARKET.format(t=ticker, dfrom=frm, till=till)
        r = requests.get(url, timeout=15)
        data = r.json().get("candles", {}).get("data", [])
        if data:
            # колонки свечей MOEX: open, close, high, low, value, volume, begin, end
            return float(data[-1][1])  # close
    except Exception:
        pass
    return None


# ---------- Совет через Edge Function (Mercury) ----------
def ask_advice(ticker, price, news):
    prompt = (
        f"Ты автономный финансовый ассистент. По акции {ticker} "
        f"текущая цена {price} руб. Свежие новости:\n"
        + "\n".join(news) +
        f"\nДай короткий совет: держать/выходить, куда поставить стоп. 2-3 предложения."
    )
    res = ask_edge(prompt)
    if res:
        return res
    return "Не удалось получить совет."


# ---------- Обработка команд ----------
def handle_command(text):
    text = text.strip().lower()
    tickers = load_tickers()

    if text.startswith("/track"):
        parts = text.split()
        new = [p.upper() for p in parts[1:] if p.isalpha()]
        if new:
            merged = list(dict.fromkeys(tickers + new))
            save_tickers(merged)
            tg_send(f"✅ Отслеживаю: {', '.join(merged)}")
        else:
            tg_send("Пример: /track SBER GAZP")
        return

    if text.startswith("/untrack"):
        parts = text.split()
        rem = [p.upper() for p in parts[1:] if p.isalpha()]
        rest = [t for t in tickers if t not in rem]
        save_tickers(rest)
        tg_send(f"Убрал: {', '.join(rem) if rem else '—'}. Осталось: {', '.join(rest) if rest else 'нет'}")
        return

    if text.startswith("/positions") or text.startswith("/portfolio"):
        if not tickers:
            tg_send("Список пуст. Добавь: /track SBER GAZP")
            return
        lines = ["📊 Мои позиции:"]
        for t in tickers:
            p = get_price(t)
            if p is not None:
                lines.append(f"• {t}: {p:.2f} ₽")
            else:
                lines.append(f"• {t}: цена недоступна")
        tg_send("\n".join(lines))
        return

    if text.startswith("/advice"):
        parts = text.split()
        if len(parts) < 2:
            tg_send("Пример: /advice SBER")
            return
        t = parts[1].upper()
        p = get_price(t)
        n = get_news(t)
        if p is None:
            tg_send(f"Цена {t} недоступна")
            return
        advice = ask_advice(t, p, n)
        tg_send(f"💡 Совет по {t}:\n{advice}")
        return

    if text.startswith("/news"):
        if not tickers:
            tg_send("Список пуст. Добавь: /track SBER")
            return
        for t in tickers[:5]:
            n = get_news(t)
            if n:
                tg_send(f"📰 {t}:\n" + "\n".join("• " + x[:120] for x in n[:3]))
        return

    if text.startswith("/events") or text.startswith("/события") or text.startswith("/dividends") or text.startswith("/дивиденды"):
        parts = text.split()
        if len(parts) < 2:
            tg_send("Пример: /events SBER")
            return
        t = parts[1].upper()
        res = ask_edge(f"события {t}")
        if res:
            tg_send(res)
        else:
            tg_send("Не удалось получить события.")
        return

    if text.startswith("/start") or text.startswith("/help"):
        tg_send(
            "🤖 Автономный ассистент\n\n"
            "/track SBER GAZP — следить за акциями\n"
            "/untrack SBER — убрать\n"
            "/positions — мои позиции\n"
            "/advice SBER — совет (держать/выходить/стоп)\n"
            "/news — новости по всем\n\n"
            "Можно писать свободно: «следи за Газпромом», «что с Сбером?»\n\n"
            "Автоматически: новости по позициям + утренняя сводка в 10:00"
        )
        return

    # --- Свободный текст (не команда) ---
    if text.startswith("/"):
        tg_send("Не понял команду. Напиши /help")
        return

    # 1) «следи за / добавь / отслеживай ...» — добавление тикеров
    watch_words = ("следи", "следить", "добавь", "отслеживай", "добавить", "следи за")
    if any(w in text for w in watch_words):
        found = []
        for t in tickers + ["SBER", "GAZP", "LKOH", "ROSN", "GMKN", "NVTK", "VTBR", "TATN", "MTSS", "AFLT", "SNGSP", "ALRS", "CHMF", "NLMK", "MAGN", "PLZL", "RUAL", "PHOR"]:
            if t.lower() in text.lower() and t not in found:
                found.append(t)
        # также по русским именам
        names_map = {
            "сбер": "SBER", "сбербанк": "SBER", "газпром": "GAZP", "лукойл": "LKOH",
            "роснефть": "ROSN", "норникель": "GMKN", "новатэк": "NVTK", "втб": "VTBR",
            "татнефть": "TATN", "мтс": "MTSS", "аэрофлот": "AFLT", "сургут": "SNGSP",
            "алроса": "ALRS", "северсталь": "CHMF", "нлмк": "NLMK", "ммк": "MAGN",
            "полюс": "PLZL", "русал": "RUAL", "фосагро": "PHOR",
        }
        for ru, tk in names_map.items():
            if ru in text and tk not in found:
                found.append(tk)
        if found:
            merged = list(dict.fromkeys(tickers + found))
            save_tickers(merged)
            tg_send(f"✅ Отслеживаю: {', '.join(merged)}")
        else:
            tg_send("Не нашёл тикеры в сообщении. Пример: «следи за Сбером и Газпромом»")
        return

    # 2) «что с X / совет по X / держать/выходить» — совет
    advice_words = ("что с", "совет", "держать", "выходить", "стоп", "анализ", "стоит ли")
    if any(w in text for w in advice_words):
        target = None
        for t in tickers:
            if t.lower() in text.lower():
                target = t
                break
        if not target:
            names_map = {
                "сбер": "SBER", "газпром": "GAZP", "лукойл": "LKOH", "роснефть": "ROSN",
                "норникель": "GMKN", "новатэк": "NVTK", "втб": "VTBR", "татнефть": "TATN",
                "мтс": "MTSS", "аэрофлот": "AFLT", "сургут": "SNGSP", "алроса": "ALRS",
                "северсталь": "CHMF", "нлмк": "NLMK", "ммк": "MAGN", "полюс": "PLZL",
            }
            for ru, tk in names_map.items():
                if ru in text:
                    target = tk
                    break
        if target:
            p = get_price(target)
            n = get_news(target)
            if p is not None:
                advice = ask_advice(target, p, n)
                tg_send(f"💡 Совет по {target}:\n{advice}")
            else:
                tg_send(f"Цена {target} недоступна")
        else:
            tg_send("По какой акции? Напиши, например: «что с Сбером?»")
        return

    # 3) «новости / сводка / события»
    if any(w in text for w in ("новост", "сводк", "событ")):
        if not tickers:
            tg_send("Список пуст. Добавь: «следи за Сбером»")
            return
        for t in tickers[:5]:
            n = get_news(t)
            if n:
                tg_send(f"📰 {t}:\n" + "\n".join("• " + x[:120] for x in n[:3]))
        return

    # 4) Позиции
    if any(w in text for w in ("позиц", "портфель", "что у меня", "мои акции")):
        if not tickers:
            tg_send("Список пуст. Добавь: «следи за Сбером»")
            return
        lines = ["📊 Мои позиции:"]
        for t in tickers:
            p = get_price(t)
            lines.append(f"• {t}: {p:.2f} ₽" if p else f"• {t}: цена недоступна")
        tg_send("\n".join(lines))
        return

    # 5) Иначе — свободный вопрос модели
    answer = ask_free(text)
    tg_send(answer)


# ---------- Утр. сводка ----------
def morning_summary():
    tickers = load_tickers()
    if not tickers:
        tg_send("☀️ Утренняя сводка " + time.strftime("%d.%m.%Y") + "\nСписок позиций пуст. Добавь: /track SBER")
        return
    lines = ["☀️ Утренняя сводка " + time.strftime("%d.%m.%Y"), ""]
    for t in tickers:
        p = get_price(t)
        n = get_news(t)
        lines.append(f"📊 {t}:")
        if p is not None:
            lines.append(f"   Цена: {p:.2f} ₽")
        if n:
            lines.append("   📰 Новости:")
            for x in n[:2]:
                lines.append("     • " + x[:110])
        # События/дивиденды через Edge Function
        res = ask_edge(f"события {t}")
        if res and "События" in res:
            lines.append("   📅 События:")
            lines.append("     " + res.replace("\n", "\n     "))
        lines.append("")
    tg_send("\n".join(lines))


# ---------- Фоновый мониторинг ----------
def monitor_loop():
    state = load_state()
    last_news = state.get("last_news", {})
    tickers = load_tickers()
    for t in tickers:
        n = get_news(t)
        if not n:
            continue
        prev = last_news.get(t, [])
        new_items = [x for x in n if x not in prev]
        if new_items:
            tg_send(f"📰 {t}:\n" + "\n".join("• " + x[:120] for x in new_items[:2]))
            last_news[t] = n[:5]
    state["last_news"] = last_news
    save_state(state)


EDGE_URL = "https://cehlmcffjhcigpspvvzm.supabase.co/functions/v1/ask-gemini"
EDGE_KEY = os.environ.get("SUPABASE_ANON_KEY", "sb_publishable_c7c_rkXrjUQTiJQWoq91xA_OgHg50MJ")


def ask_edge(text, provider="auto"):
    try:
        r = requests.post(
            EDGE_URL,
            headers={"Content-Type": "application/json", "apikey": EDGE_KEY},
            json={"task": text, "provider": provider},
            timeout=50,
        )
        if r.ok:
            j = r.json()
            content = j.get("content")
            if content:
                return content
    except Exception:
        pass
    return None


# ---------- Свободный вопрос ----------
def ask_free(text):
    prompt = (
        "Ты автономный финансовый ассистент в Telegram. Отвечай кратко (2-4 предложения), "
        "на русском, по делу. Вопрос: " + text
    )
    res = ask_edge(prompt)
    if res:
        return res
    return "Не удалось получить ответ."


# ---------- Main ----------
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"

    if mode == "--morning":
        morning_summary()
        return

    if mode == "--once":
        monitor_loop()
        return

    # watch: слушаем команды + мониторинг
    offset = 0
    while True:
        try:
            updates = tg_get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message")
                if msg and msg.get("text"):
                    handle_command(msg["text"])
            monitor_loop()
        except Exception as e:
            print("[!]", e)
        time.sleep(3)


if __name__ == "__main__":
    main()
