#!/usr/bin/env python3
import sys
import json
import re
import os
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

def detect_bank(pdf_path: str) -> str:
    """Auto-detect bank type from PDF content"""
    try:
        import pdfplumber  # ✅ import ข้างในฟังก์ชันแทน
        with pdfplumber.open(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            text_lower = first_page_text.lower()

            if any(k in text_lower for k in ['กสิกรไทย', 'kbank', 'kasikorn']):
                return 'kbank'
            elif any(k in text_lower for k in ['ไทยพาณิชย์', 'scb', 'siam commercial']):
                return 'scb'
            elif any(k in text_lower for k in ['ธ.ก.ส', 'baac', 'เพื่อการเกษตร']):
                return 'baac'
            elif any(k in text_lower for k in [
                'ออมสิน', 'gsb', 'government savings',
                'savings account statement',
                'mymo', 'ppsdtr', 'mppoff'
            ]):
                return 'oomsin'
            else:
                return 'generic'

    except ImportError:
        print("Error: pdfplumber not installed. Run: pip install pdfplumber",
              file=sys.stderr)
        return 'generic'
    except Exception as e:
        print(f"Detection error: {e}", file=sys.stderr)
        return 'generic'


def main():
    if len(sys.argv) < 3:
        print("Usage: python parse_pdf.py <input.pdf> <output.xlsx> [bank_type]",
              file=sys.stderr)
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = sys.argv[2]
    bank_type   = sys.argv[3] if len(sys.argv) > 3 else 'auto'

    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect bank
    if bank_type == 'auto':
        bank_type = detect_bank(input_path)
        print(f"Auto-detected bank: {bank_type}")

    # Route to parser
    transactions = []

    try:
        if bank_type == 'kbank':
            from parse_kbank import parse_kbank
            transactions = parse_kbank(input_path)

        elif bank_type == 'scb':
            from parse_scb import parse_scb
            transactions = parse_scb(input_path)

        elif bank_type == 'baac':
            from parse_baac import parse_baac
            transactions = parse_baac(input_path)

        elif bank_type == 'oomsin':
            from parse_oomsin import parse_oomsin
            transactions = parse_oomsin(input_path)

        else:
            from parse_oomsin import parse_oomsin
            transactions = parse_oomsin(input_path)

        if not transactions:
            print("Warning: No transactions found", file=sys.stderr)

        from generate_excel import generate_excel
        generate_excel(transactions, output_path, bank_type)

        print(f"Success: {len(transactions)} transactions -> {output_path}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

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
