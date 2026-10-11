"""Парсер событий: smart-lab + investmint (MCP)."""
import json
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from pathlib import Path

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9',
}
PAGES = [
    ("https://smart-lab.ru/calendar/stocks/", "Событие"),
    ("https://smart-lab.ru/dividends/", "Дивиденды"),
]

MCP_URL = "https://mcp.investmint.ru/mcp"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
CACHE_FILE = Path(__file__).parent / "investmint_cache.json"
CACHE_TTL_HOURS = 12
MAX_MCP_TICKERS = 20

WARMUP_TICKERS = [
    'SBER', 'GAZP', 'LKOH', 'GMKN', 'ROSN', 'NVTK', 'TATN',
    'SNGS', 'PLZL', 'CHMF', 'NLMK', 'MAGN', 'ALRS', 'MTSS',
    'MGNT', 'MOEX', 'VTBR', 'AFKS', 'YDEX', 'OZON',
]

MONTHS_RU = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
    'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
    'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
}


def _clean_text(text, ticker):
    text = re.sub(r'(\d{2}\.\d{2}\.\d{4})\s+\1', r'\1', text)
    text = re.sub(r'\d{2}\.\d{2}\.\d{4}', '', text)
    text = text.replace('|', ' ').replace('>>>', ' ')
    text = re.sub(rf'\b{re.escape(ticker)}\b\s*[:\-]?\s*', ' ', text)
    text = re.sub(r'\s*[-–—]\s*', ' ', text)
    text = re.sub(r'\.{2,}', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' —-')
    return text[:180]


def _parse_date_sl(s):
    try:
        return datetime.strptime(s, '%d.%m.%Y')
    except Exception:
        return datetime(2099, 12, 31)


def _extract_ticker(link):
    m = re.search(r'/forum/([A-Z0-9]+)', link.get('href', ''))
    return m.group(1).upper() if m else None


def _get_smartlab_events(tickers):
    tickers_upper = {t.upper() for t in tickers}
    result = {t: [] for t in tickers_upper}
    for url, label in PAGES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table',
                                   class_=re.compile(r'(trades-table|events)'))
            for table in tables:
                for row in table.find_all('tr'):
                    fl = row.find('a', href=re.compile(r'/forum/'))
                    if not fl:
                        continue
                    t = _extract_ticker(fl)
                    if not t or t not in tickers_upper:
                        continue
                    text = row.get_text(separator=' | ', strip=True)
                    dm = re.search(r'(\d{2}\.\d{2}\.\d{4})', text)
                    if not dm:
                        continue
                    ds = dm.group(1)
                    clean = _clean_text(text, t)
                    if not clean or len(clean) < 5:
                        continue
                    result[t].append({
                        'date': ds,
                        'date_obj': _parse_date_sl(ds),
                        'type': label,
                        'text': clean,
                        'source': 'smart-lab',
                    })
        except Exception as e:
            print(f"smart-lab ошибка {url}: {e}")
    return result


def _get_smartlab_all_upcoming(days_ahead=30, max_events=50):
    url = "https://smart-lab.ru/calendar/stocks/"
    today = datetime.now()
    cutoff = today + timedelta(days=days_ahead)
    all_events = []
    seen = set()
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        tables = soup.find_all('table',
                               class_=re.compile(r'(trades-table|events)'))
        for table in tables:
            for row in table.find_all('tr'):
                fl = row.find('a', href=re.compile(r'/forum/'))
                if not fl:
                    continue
                t = _extract_ticker(fl)
                if not t:
                    continue
                text = row.get_text(separator=' | ', strip=True)
                dm = re.search(r'(\d{2}\.\d{2}\.\d{4})', text)
                if not dm:
                    continue
                ds = dm.group(1)
                try:
                    do = datetime.strptime(ds, '%d.%m.%Y')
                except Exception:
                    continue
                if do < today or do > cutoff:
                    continue
                clean = _clean_text(text, t)
                if not clean or len(clean) < 5:
                    continue
                key = (t, ds, clean[:60])
                if key in seen:
                    continue
                seen.add(key)
                all_events.append({
                    'date': ds, 'date_obj': do, 'ticker': t,
                    'text': clean, 'source': 'smart-lab',
                })
    except Exception as e:
        print(f"smart-lab ошибка: {e}")
    all_events.sort(key=lambda x: (x['date_obj'], x['ticker']))
    return all_events[:max_events]


def _decode_utf8(s):
    try:
        return s.encode('latin-1').decode('utf-8')
    except Exception:
        return s


def _parse_date_inv(s):
    m = re.match(r'(\d{1,2})\s+([а-яё]+)', s.strip().lower())
    if not m:
        return None
    try:
        day = int(m.group(1))
        month = MONTHS_RU.get(m.group(2))
        if not month:
            return None
        now = datetime.now()
        year = now.year
        if month < now.month:
            year += 1
        return datetime(year, month, day)
    except Exception:
        return None


def _mcp_call(tool_name, arguments):
    payload = {
        "jsonrpc": "2.0", "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}, "id": 1,
    }
    try:
        r = requests.post(MCP_URL, json=payload, headers=MCP_HEADERS, timeout=20)
        for line in r.text.split('\n'):
            if line.startswith('data: '):
                try:
                    data = json.loads(line[6:])
                    content = data.get("result", {}).get("content", [])
                    if content:
                        return _decode_utf8(content[0].get("text", ""))
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _serialize(d):
    out = {}
    for t, evs in d.items():
        out[t] = []
        for e in evs:
            e2 = dict(e)
            if isinstance(e2.get('date_obj'), datetime):
                e2['date_obj'] = e2['date_obj'].isoformat()
            out[t].append(e2)
    return out


def _deserialize(d):
    out = {}
    for t, evs in d.items():
        out[t] = []
        for e in evs:
            e2 = dict(e)
            if isinstance(e2.get('date_obj'), str):
                try:
                    e2['date_obj'] = datetime.fromisoformat(e2['date_obj'])
                except Exception:
                    e2['date_obj'] = datetime(2099, 1, 1)
            out[t].append(e2)
    return out


def _load_cache():
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None


def _save_cache(data):
    try:
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8')
    except Exception as e:
        print(f"Ошибка кэша: {e}")


def _cache_fresh(cache):
    if not cache:
        return False
    try:
        ts = datetime.fromisoformat(cache['timestamp'])
        return (datetime.now() - ts) < timedelta(hours=CACHE_TTL_HOURS)
    except Exception:
        return False


def _get_investmint_calendar(tickers):
    tickers = [t.upper() for t in tickers[:MAX_MCP_TICKERS]]
    if not tickers:
        return {}

    cache = _load_cache()
    cached_raw = {}
    if cache and _cache_fresh(cache):
        cached_raw = _deserialize(cache.get('data', {}))

    to_query = [t for t in tickers if t not in cached_raw]
    if to_query:
        print(f"  investmint: запрос для {len(to_query)} тикеров...")

    new_data = {}
    seen = set()
    for t in to_query:
        raw = _mcp_call("investmint_calendar", {"ticker": t})
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for ev in data.get('events', []):
            date_str = ev.get('date', '')
            title = ev.get('title', '')
            date_obj = _parse_date_inv(date_str)
            for et in (ev.get('tickers') or [t]):
                et = et.upper()
                key = (et, date_str, title)
                if key in seen:
                    continue
                seen.add(key)
                new_data.setdefault(et, []).append({
                    'date': date_str,
                    'date_obj': date_obj or datetime(2099, 1, 1),
                    'type': 'Событие (inv)',
                    'text': title,
                    'source': 'investmint',
                })

    merged = dict(cached_raw)
    for t, evs in new_data.items():
        merged.setdefault(t, []).extend(evs)

    for t in merged:
        uniq = {}
        for e in merged[t]:
            uniq[(e['date'], e['text'][:60])] = e
        merged[t] = sorted(uniq.values(),
                           key=lambda x: x.get('date_obj') or datetime(2099, 1, 1))

    _save_cache({'timestamp': datetime.now().isoformat(),
                 'data': _serialize(merged)})

    return {t: merged[t] for t in tickers if t in merged}


def warmup_investmint_cache():
    """Прогревает кэш investmint по топ-20 ликвидным тикерам."""
    try:
        _get_investmint_calendar(WARMUP_TICKERS)
    except Exception as e:
        print(f"Ошибка прогрева кэша: {e}")


def get_events(tickers, max_per_ticker=6):
    if not tickers:
        return {}
    sl = _get_smartlab_events(tickers)
    inv = _get_investmint_calendar(tickers)
    result = {}
    for t in set(list(sl.keys()) + list(inv.keys())):
        merged = []
        seen = set()
        for ev in sl.get(t, []) + inv.get(t, []):
            key = (ev.get('date'), ev.get('text', '')[:60])
            if key in seen:
                continue
            seen.add(key)
            merged.append(ev)
        merged.sort(key=lambda x: x.get('date_obj') or datetime(2099, 1, 1))
        result[t] = merged[:max_per_ticker]
    return result


def get_all_upcoming_events(days_ahead=30, max_events=100):
    # Прогрев кэша по топ-20
    warmup_investmint_cache()

    sl_events = _get_smartlab_all_upcoming(days_ahead, max_events * 2)

    inv_events = []
    cache = _load_cache()
    if cache and _cache_fresh(cache):
        cached = _deserialize(cache.get('data', {}))
        today = datetime.now()
        cutoff = today + timedelta(days=days_ahead)
        for t, evs in cached.items():
            for e in evs:
                do = e.get('date_obj')
                if not do or do < today or do > cutoff:
                    continue
                inv_events.append({
                    'date': e['date'], 'date_obj': do, 'ticker': t,
                    'text': e['text'], 'source': 'investmint',
                })

    all_ev = []
    seen = set()
    for ev in sl_events + inv_events:
        key = (ev.get('ticker'), ev.get('date'), ev.get('text', '')[:60])
        if key in seen:
            continue
        seen.add(key)
        all_ev.append(ev)

    all_ev.sort(key=lambda x: (x.get('date_obj') or datetime(2099, 1, 1),
                                x.get('ticker', '')))
    return all_ev[:max_events]