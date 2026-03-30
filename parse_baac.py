#!/usr/bin/env python3
import sys, json, re
import pdfplumber
from collections import defaultdict

# BAAC column x positions (from analysis):
# Date+Time: x0 ~35  (concatenated "27-06-256814:33:41")
# Transaction code: x0 ~99
# Description: x0 ~147
# ถอน (debit):  x0 ~397-420  (negative values like -500.00)
# ฝาก (credit): x0 ~448-470
# ยอดคงเหลือ:  x0 ~489-520

date_pat = re.compile(r'^(\d{2})-(\d{2})-(\d{4})')   # DD-MM-YYYY (พ.ศ.)
num_pat  = re.compile(r'^-?[\d,]+\.\d{2}$')
SKIP     = ['วันที่', 'ถอน', 'ฝาก', 'ยอดคงเหลือ', 'รายการ', 'คําอธิบาย']

def parse_baac(pdf_path):
    transactions = []
    meta = {}

    with pdfplumber.open(pdf_path) as pdf:
        text1 = pdf.pages[0].extract_text() or ''
        m = re.search(r'ชื่อเจ้าของบัญชี\s+(นาย|นาง|น\.ส\.)?(.+?)\s+เลขที่บัญชี', text1)
        if m:
            meta['account_name'] = ((m.group(1) or '') + ' ' + m.group(2)).strip()
        m2 = re.search(r'เลขที่บัญชี\s+([\d\-]+)', text1)
        if m2:
            meta['account_number'] = m2.group(1)

        for page in pdf.pages:
            words = page.extract_words(x_tolerance=5, y_tolerance=5)
            rows  = defaultdict(list)
            for w in words:
                y = round(w['top'] / 2) * 2
                rows[y].append(w)

            for y in sorted(rows.keys()):
                row_words = sorted(rows[y], key=lambda w: w['x0'])
                texts = [w['text'] for w in row_words]
                if not texts:
                    continue

                # Date is concatenated with time e.g. "27-06-256814:33:41"
                dm = date_pat.match(texts[0])
                if not dm:
                    continue
                if any(s in ' '.join(texts) for s in SKIP):
                    continue

                day    = int(dm.group(1))
                mon    = int(dm.group(2))
                yr_be  = int(dm.group(3))   # พ.ศ. full e.g. 2568
                yr_th2 = yr_be - 2500        # 68
                yr_ce  = yr_be - 543         # 2025

                debit  = 0.0
                credit = 0.0

                for w in row_words:
                    if not num_pat.match(w['text']):
                        continue
                    val_str = w['text'].replace(',', '')
                    val     = float(val_str)
                    x       = w['x0']

                    if 390 <= x <= 440:      # ถอน column (may be negative)
                        debit = abs(val)
                    elif 440 <= x <= 490:    # ฝาก column
                        credit = abs(val)
                    # x >= 490 = ยอดคงเหลือ → skip

                if debit == 0 and credit == 0:
                    continue

                transactions.append({
                    'date':    f"{day:02d}/{mon:02d}/{yr_th2:02d}",
                    'day': day, 'month': mon,
                    'year_th': yr_th2, 'year_ce': yr_ce,
                    'debit':  debit,
                    'credit': credit,
                    'description': ' '.join(t for t in texts[1:5]
                                            if not num_pat.match(t)),
                })

    return {'meta': meta, 'transactions': transactions, 'bank': 'baac'}

if __name__ == '__main__':
    result = parse_baac(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))
