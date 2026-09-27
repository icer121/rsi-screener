"""Парсер событий smart-lab для тикеров MOEX."""
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
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


def get_events(tickers, pages=PAGES, max_per_ticker=4):
    """Возвращает {тикер: [{date, date_obj, type, text}, ...]}."""
    tickers_upper = {t.upper() for t in tickers}
    result = {t: [] for t in tickers_upper}
    seen = set()

    for url, label in pages:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all(
                'table',
                class_=re.compile(r'(trades-table|events)')
            )

            for table in tables:
                for row in table.find_all('tr'):
                    forum_link = row.find('a', href=re.compile(r'/forum/'))
                    if not forum_link:
                        continue
                    m = re.search(r'/forum/([A-Z0-9]+)',
                                  forum_link.get('href', ''))
                    if not m:
                        continue
                    ticker = m.group(1).upper()
                    if ticker not in tickers_upper:
                        continue

                    text = row.get_text(separator=' | ', strip=True)
                    date_match = re.search(
                        r'(\d{2}\.\d{2}\.\d{4})', text
                    )
                    if not date_match:
                        continue
                    date_str = date_match.group(1)

                    clean = _clean_text(text, ticker)
                    if not clean or len(clean) < 5:
                        continue

                    key = (ticker, date_str, clean[:60])
                    if key in seen:
                        continue
                    seen.add(key)

                    result[ticker].append({
                        'date': date_str,
                        'date_obj': _parse_date(date_str),
                        'type': label,
                        'text': clean,
                    })
        except Exception as e:
            print(f"Ошибка {url}: {e}")

    for t in result:
        result[t].sort(key=lambda x: x['date_obj'])
        result[t] = result[t][:max_per_ticker]

    return result