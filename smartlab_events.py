"""Парсер событий и новостей smart-lab."""
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9',
}

PAGES = [
    ("https://smart-lab.ru/calendar/stocks/", "Событие"),
    ("https://smart-lab.ru/dividends/", "Дивиденды"),
]


def _clean_text(text, ticker):
    text = re.sub(r'(\d{2}\.\d{2}\.\d{4})\s+\1', r'\1', text)
    text = re.sub(r'\d{2}\.\d{2}\.\d{4}', '', text)
    text = text.replace('|', ' ').replace('>>>', ' ')
    text = re.sub(rf'\b{re.escape(ticker)}\b\s*[:\-]?\s*', ' ', text)
    text = re.sub(r'\s*[-–—]\s*', ' ', text)
    text = re.sub(r'\.{2,}', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' —-')
    return text[:180]


def _parse_date(s):
    try:
        return datetime.strptime(s, '%d.%m.%Y')
    except Exception:
        return datetime(2099, 12, 31)


def _extract_ticker(link):
    m = re.search(r'/forum/([A-Z0-9]+)', link.get('href', ''))
    return m.group(1).upper() if m else None


def get_events(tickers, max_per_ticker=6):
    """События по заданным тикерам."""
    tickers_upper = {t.upper() for t in tickers}
    result = {t: [] for t in tickers_upper}
    seen = set()

    for url, label in PAGES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table', class_=re.compile(r'(trades-table|events)'))
            for table in tables:
                for row in table.find_all('tr'):
                    fl = row.find('a', href=re.compile(r'/forum/'))
                    if not fl:
                        continue
                    ticker = _extract_ticker(fl)
                    if not ticker or ticker not in tickers_upper:
                        continue
                    text = row.get_text(separator=' | ', strip=True)
                    dm = re.search(r'(\d{2}\.\d{2}\.\d{4})', text)
                    if not dm:
                        continue
                    ds = dm.group(1)
                    clean = _clean_text(text, ticker)
                    if not clean or len(clean) < 5:
                        continue
                    key = (ticker, ds, clean[:60])
                    if key in seen:
                        continue
                    seen.add(key)
                    result[ticker].append({
                        'date': ds,
                        'date_obj': _parse_date(ds),
                        'type': label,
                        'text': clean,
                    })
        except Exception as e:
            print(f"Ошибка {url}: {e}")

    for t in result:
        result[t].sort(key=lambda x: x['date_obj'])
        result[t] = result[t][:max_per_ticker]
    return result


def get_all_upcoming_events(days_ahead=30, max_events=50):
    """ВСЕ предстоящие события по рынку за N дней (без фильтра)."""
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
        tables = soup.find_all('table', class_=re.compile(r'(trades-table|events)'))
        for table in tables:
            for row in table.find_all('tr'):
                fl = row.find('a', href=re.compile(r'/forum/'))
                if not fl:
                    continue
                ticker = _extract_ticker(fl)
                if not ticker:
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
                clean = _clean_text(text, ticker)
                if not clean or len(clean) < 5:
                    continue
                key = (ticker, ds, clean[:60])
                if key in seen:
                    continue
                seen.add(key)
                all_events.append({
                    'date': ds,
                    'date_obj': do,
                    'ticker': ticker,
                    'text': clean,
                })
    except Exception as e:
        print(f"Ошибка: {e}")

    all_events.sort(key=lambda x: (x['date_obj'], x['ticker']))
    return all_events[:max_events]