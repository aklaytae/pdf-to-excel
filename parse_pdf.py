#!/usr/bin/env python3
import sys
import json
import pdfplumber
import re
from collections import defaultdict

date_pattern = re.compile(r'^\d{2}/\d{2}/\d{2}$')
num_pattern = re.compile(r'^[\d,]+\.\d{2}$')

def classify_amount(x0, x1):
    mid = (x0 + x1) / 2
    if mid < 395:
        return 'debit'
    elif mid < 465:
        return 'credit'
    else:
        return 'balance'

def parse_pdf(pdf_path):
    transactions = []
    meta = {}
    with pdfplumber.open(pdf_path) as pdf:
        # Extract metadata from first page
        first_text = pdf.pages[0].extract_text() or ''
        # Try to extract account name
        name_match = re.search(r'ชื่อบัญชี\s+([\u0E00-\u0E7F\s]+?)ประเภทบัญชี', first_text)
        if name_match:
            meta['account_name'] = name_match.group(1).strip()
        acc_match = re.search(r'เลขที่บัญชี\s+(\d+)', first_text)
        if acc_match:
            meta['account_number'] = acc_match.group(1)
        
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=5, y_tolerance=5)
            rows = defaultdict(list)
            for w in words:
                y = round(w['top'] / 2) * 2
                rows[y].append(w)
            
            for y in sorted(rows.keys()):
                row_words = sorted(rows[y], key=lambda w: w['x0'])
                texts = [w['text'] for w in row_words]
                if not texts or not date_pattern.match(texts[0]):
                    continue
                
                date = texts[0]
                debit = 0.0
                credit = 0.0
                description_parts = []
                
                for w in row_words[1:]:
                    if num_pattern.match(w['text']):
                        col = classify_amount(w['x0'], w['x1'])
                        val = float(w['text'].replace(',', ''))
                        if col == 'debit':
                            debit = val
                        elif col == 'credit':
                            credit = val
                    else:
                        description_parts.append(w['text'])
                
                desc = ' '.join(description_parts)
                if 'รายการถอน' in desc or 'รายการฝาก' in desc:
                    continue
                
                if debit > 0 or credit > 0:
                    # Parse date: DD/MM/YY -> convert YY to year
                    parts = date.split('/')
                    day, month, year_th = parts[0], parts[1], parts[2]
                    year_ce = int(year_th) + 2500 - 543  # พ.ศ. -> ค.ศ. (68+2500-543=2025)
                    # Month name mapping
                    month_names = {
                        '01':'ม.ค.','02':'ก.พ.','03':'มี.ค.','04':'เม.ย.',
                        '05':'พ.ค.','06':'มิ.ย.','07':'ก.ค.','08':'ส.ค.',
                        '09':'ก.ย.','10':'ต.ค.','11':'พ.ย.','12':'ธ.ค.'
                    }
                    month_key = f"{month_names.get(month, month)}{year_th}"
                    
                    transactions.append({
                        'date': date,
                        'day': day,
                        'month': month,
                        'year_th': year_th,
                        'year_ce': year_ce,
                        'month_key': month_key,
                        'description': desc,
                        'debit': debit,
                        'credit': credit
                    })
    return {'meta': meta, 'transactions': transactions}

if __name__ == '__main__':
    pdf_path = sys.argv[1]
    result = parse_pdf(pdf_path)
    print(json.dumps(result, ensure_ascii=False))
