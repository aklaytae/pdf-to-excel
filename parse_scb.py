#!/usr/bin/env python3
import sys, json, re
import pdfplumber
from collections import defaultdict

# SCB column x positions:
# Date:    x0 ~30
# Debit:   x0 ~196-230   (ลูกหนี้ = money OUT)
# Credit:  x0 ~270-310   (เจ้าหนี้ = money IN)
# Balance: x0 ~360  (concatenated with description)

date_pat = re.compile(r'^\d{2}/\d{2}/\d{2}$')   # DD/MM/YY  CE
num_pat  = re.compile(r'^[\d,]+\.\d{2}$')
SKIP     = ['BALANCE', 'BROUGHT', 'FORWARD', 'TOTAL', 'AMOUNTS']

def parse_scb(pdf_path):
    transactions = []
    meta = {}

    with pdfplumber.open(pdf_path) as pdf:
        text1 = pdf.pages[0].extract_text() or ''
        # Account name
        m = re.search(r'(?:ชื่อ\s*-\s*สกุล|Name)\s*\n?\s*(นาย|นาง|น\.ส\.)?(.+?)\n', text1)
        if m:
            prefix = (m.group(1) or '').strip()
            name   = m.group(2).strip()
            meta['account_name'] = f"{prefix} {name}".strip()
        # Account number
        m2 = re.search(r'(\d{3}-\d{6}-\d)', text1)
        if m2: meta['account_number'] = m2.group(1)

        for page in pdf.pages:
            words = page.extract_words(x_tolerance=5, y_tolerance=5)
            rows  = defaultdict(list)
            for w in words:
                y = round(w['top'] / 2) * 2
                rows[y].append(w)

            for y in sorted(rows.keys()):
                row_words = sorted(rows[y], key=lambda w: w['x0'])
                texts = [w['text'] for w in row_words]
                if not texts or not date_pat.match(texts[0]):
                    continue
                if any(s in ' '.join(texts) for s in SKIP):
                    continue

                date_str = texts[0]   # DD/MM/YY  CE

                debit  = 0.0
                credit = 0.0

                for w in row_words[1:]:
                    if not num_pat.match(w['text']):
                        continue
                    val = float(w['text'].replace(',', ''))
                    x   = w['x0']
                    if 190 <= x <= 240:      # Debit column
                        debit = val
                    elif 260 <= x <= 320:    # Credit column
                        credit = val
                    # x >= 350 = Balance  → skip

                if debit == 0 and credit == 0:
                    continue

                # Convert DD/MM/YY (CE) → DD/MM/YY_th
                p = date_str.split('/')
                day, mon, yr2 = int(p[0]), int(p[1]), int(p[2])
                yr_ce  = 2000 + yr2
                yr_th2 = yr_ce - 2500 + 543   # 2025 → 68

                transactions.append({
                    'date':    f"{day:02d}/{mon:02d}/{yr_th2:02d}",
                    'day': day, 'month': mon,
                    'year_th': yr_th2, 'year_ce': yr_ce,
                    'debit':  debit,
                    'credit': credit,
                    'description': ' '.join(t for t in texts[1:6]),
                })

    return {'meta': meta, 'transactions': transactions, 'bank': 'scb'}

if __name__ == '__main__':
    result = parse_scb(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
