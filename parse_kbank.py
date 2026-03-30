#!/usr/bin/env python3
import sys, json, re
import pdfplumber
from collections import defaultdict

date_pat = re.compile(r'^\d{2}-\d{2}-\d{2}$')
num_pat  = re.compile(r'^[\d,]+\.\d{2}$')

CREDIT_KW = ['รับโอนเงิน', 'รับดอกเบี้ย']
DEBIT_KW  = ['หักบัญชี', 'ชำระเงิน', 'โอนเงิน', 'ถอนเงิน']
SKIP_KW   = ['ยอดยกมา', 'ยอดยกไป']

def parse_kbank(pdf_path):
    transactions = []
    meta = {}

    with pdfplumber.open(pdf_path) as pdf:
        text1 = pdf.pages[0].extract_text() or ''
        m = re.search(r'ชื่อบัญชี\s*(.+?)\s+เลขที่อ้างอิง', text1)
        if m: meta['account_name'] = m.group(1).strip()
        m2 = re.search(r'เลขที่บัญชีเงินฝาก\s*([\d\-]+)', text1)
        if m2: meta['account_number'] = m2.group(1)

        for page in pdf.pages:
            words = page.extract_words(x_tolerance=5, y_tolerance=5)
            rows = defaultdict(list)
            for w in words:
                y = round(w['top'] / 2) * 2
                rows[y].append(w)

            for y in sorted(rows.keys()):
                row_words = sorted(rows[y], key=lambda w: w['x0'])
                texts = [w['text'] for w in row_words]
                if not texts or not date_pat.match(texts[0]):
                    continue

                date_str  = texts[0]
                desc_text = ' '.join(texts[1:])

                if any(k in desc_text for k in SKIP_KW):
                    continue

                amount    = 0.0
                is_credit = None

                for w in row_words[1:]:
                    if num_pat.match(w['text']) and w['x0'] < 270:
                        amount    = float(w['text'].replace(',', ''))
                        is_credit = w['x0'] >= 242
                        break

                # Override with keyword (more reliable)
                if any(k in desc_text for k in CREDIT_KW):
                    is_credit = True
                elif any(k in desc_text for k in DEBIT_KW):
                    is_credit = False

                if amount == 0 or is_credit is None:
                    continue

                p = date_str.split('-')
                day, mon, yr2 = int(p[0]), int(p[1]), int(p[2])
                yr_ce = 2000 + yr2
                yr_th2 = yr_ce - 2500 + 543   # 2025 -> 68

                transactions.append({
                    'date':        f"{day:02d}/{mon:02d}/{yr_th2:02d}",
                    'day': day, 'month': mon,
                    'year_th': yr_th2, 'year_ce': yr_ce,
                    'debit':   0.0 if is_credit else amount,
                    'credit':  amount if is_credit else 0.0,
                    'description': desc_text[:80],
                })

    return {'meta': meta, 'transactions': transactions, 'bank': 'kbank'}

if __name__ == '__main__':
    result = parse_kbank(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
