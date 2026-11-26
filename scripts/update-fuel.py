"""Refresh official tax-inclusive ES/PT weekly prices and ECB EUR/CNY, stdlib only."""
import argparse
import datetime as dt
import html
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

EC = 'https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en'
ECB = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def download(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'IberiaRoadTripFuel/1.0'}), timeout=35) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)

def read_prices(raw):
    archive = zipfile.ZipFile(io.BytesIO(raw))
    strings = [''.join(si.itertext()) for si in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', NS)]
    sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    rows = []
    for row in sheet.findall('s:sheetData/s:row', NS):
        cells = {}
        for cell in row.findall('s:c', NS):
            value = cell.find('s:v', NS)
            if value is None:
                continue
            text = value.text
            if cell.get('t') == 's':
                text = strings[int(text)]
            cells[re.sub(r'\d+', '', cell.get('r'))] = text
        rows.append(cells)
    assert 'Euro-super 95' in rows[0]['B'] and '1000' in rows[1]['B'], 'Official workbook schema changed'
    assert 'gas oil' in rows[0]['C'].lower(), 'Diesel column changed'
    date = (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(rows[1]['A']))).date()
    countries = {}
    for row in rows:
        if row.get('A') in ('Spain', 'Portugal'):
            prices = {'petrol95': round(float(row['B']) / 1000, 6), 'diesel': round(float(row['C']) / 1000, 6)}
            assert all(.3 < price < 8 for price in prices.values()), 'Implausible official price'
            countries[{'Spain': 'ES', 'Portugal': 'PT'}[row['A']]] = prices
    assert len(countries) == 2
    assert abs((dt.date.today() - date).days) < 90, 'Official workbook too old or incorrectly dated'
    return date.isoformat(), countries

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='fuel-prices.json')
    parser.add_argument('--fx-file')
    args = parser.parse_args()
    output = Path(args.output)
    old = json.loads(output.read_text(encoding='utf8')) if output.exists() else {}
    page = download(EC).decode('utf8')
    start = page.index('Prices with taxes latest prices')
    tail = page[start:]
    workbook_url = urljoin(EC, html.unescape(re.search(r'href="([^"]+)"', tail).group(1)))
    price_date, prices = read_prices(download(workbook_url))
    published = re.search(r'ecl-file__primary-meta-item">([^<]+)', tail)
    fx = old.get('exchange_rate')
    try:
        raw = Path(args.fx_file).read_bytes() if args.fx_file else download(ECB)
        tree = ET.fromstring(raw)
        date = next(n.get('time') for n in tree.iter() if n.get('time'))
        rate = float(next(n.get('rate') for n in tree.iter() if n.get('currency') == 'CNY'))
        assert 3 < rate < 20
        fx = {'eur_cny': rate, 'date': date, 'source': ECB}
    except Exception as exc:
        print('ECB update unavailable; retaining dated prior rate if available:', type(exc).__name__)
    data = {
        'schema_version': 1,
        'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'price_date': price_date,
        'published_label': html.unescape(published.group(1)) if published else '',
        'source': EC,
        'workbook_url': workbook_url,
        'frequency': 'weekly',
        'tax_included': True,
        'currency': 'EUR',
        'unit': 'per_litre',
        'countries': prices,
        'exchange_rate': fx,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(data, ensure_ascii=True))

if __name__ == '__main__':
    main()