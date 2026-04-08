import pdfplumber
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# ======================================================================
# Transaction type mapping จาก PDF จริง
# ======================================================================
TRANSACTION_CODES = {
    # Credit (เงินเข้า)
    'PPSDTR': ('deposit',    'โอนเงินเข้า (Transfer SAV)'),
    'MPPSD':  ('deposit',    'MyMo PromptPay รับเงิน'),
    'MOSD':   ('deposit',    'MyMo SAV รับเงิน'),
    'ATSDC':  ('deposit',    'ATM Cash Deposit'),
    'PRSD':   ('deposit',    'Prize Saving รางวัล'),
    'MPPSD':  ('deposit',    'MyMo PromptPay'),

    # Debit (เงินออก)
    'MOPSW':  ('withdrawal', 'MyMo Payment จ่ายบิล'),
    'MPPOFF': ('withdrawal', 'MyMo Transfer โอนออก'),
    'MOSW':   ('withdrawal', 'MyMo SAV โอนออก'),
    'CBOFFS': ('withdrawal', 'C Scan B สแกนจ่าย'),
    'SPSD07': ('withdrawal', 'SPIN SAV'),
}

# รูปแบบบรรทัด transaction หลัก
# DD/MM/YYYY  CODE  Description  Amount  Tax  Balance  Branch  Operator
TX_PATTERN = re.compile(
    r'^(\d{2}/\d{2}/\d{4})\s+'          # group1: วันที่ DD/MM/YYYY (พ.ศ.)
    r'([A-Z0-9]+)\s+'                    # group2: รหัสรายการ เช่น PPSDTR
    r'([\w\s]+?)\s+'                     # group3: คำอธิบาย
    r'([\d,]+\.\d{2})\s+'               # group4: จำนวนเงิน
    r'([\d,]+\.\d{2})\s+'               # group5: ภาษี
    r'([\d,]+\.\d{2})\s+'               # group6: คงเหลือ
    r'(\d+)\s+'                          # group7: สาขา
    r'(\d+)\s*$'                         # group8: ผู้ทำรายการ
)

# รูปแบบ B/F และ C/F
BF_PATTERN = re.compile(
    r'^B/F\s+ยอดยกมา\s+([\d,]+\.\d{2})\s*$'
)
CF_PATTERN = re.compile(
    r'^C/F\s+ยอดยกไป\s+([\d,]+\.\d{2})\s*$'
)

# รูปแบบสรุปท้ายหน้า
PAGE_SUMMARY_PATTERN = re.compile(
    r'^Page\s+Dr\.\s*=\s*(\d+)\s+([\d,]+\.\d{2})\s+'
    r'Cr\.\s*=\s*(\d+)\s+([\d,]+\.\d{2})\s*$'
)
TOTAL_SUMMARY_PATTERN = re.compile(
    r'^Total\s+Dr\.\s*=\s*(\d+)\s+([\d,]+\.\d{2})\s+'
    r'Cr\.\s*=\s*(\d+)\s+([\d,]+\.\d{2})\s*$'
)


def parse_oomsin(pdf_path: str) -> List[Dict]:
    """
    Parse GSB (ธนาคารออมสิน) Savings Account Statement PDF

    รูปแบบ:
    - วันที่เป็น พ.ศ. (Buddhist Era) → แปลงเป็น ค.ศ.
    - คอลัมน์: วันที่, รายการ, คำอธิบาย, จำนวนเงิน, ภาษี, คงเหลือ, สาขา, ผู้ทำรายการ
    - ต้องดูจาก balance เปรียบเทียบเพื่อแยก deposit/withdrawal

    Returns:
        List of transaction dicts
    """
    transactions  = []
    account_info  = {}
    page_summaries = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if not text:
                print(f"  [Page {page_num}] ไม่พบข้อความ")
                continue

            # ดึงข้อมูลบัญชีจากหน้าแรก
            if page_num == 1:
                account_info = _extract_account_info(text)

            lines = text.split('\n')
            page_transactions, summary = _parse_page_lines(lines, page_num)
            transactions.extend(page_transactions)

            if summary:
                page_summaries.append({**summary, 'page': page_num})

    # ตรวจสอบด้วย balance เพื่อแก้ไข deposit/withdrawal ที่ไม่แน่ใจ
    transactions = _verify_tx_type_by_balance(transactions)

    print(f"[GSB] พบรายการทั้งหมด {len(transactions)} รายการ")
    return transactions


def _extract_account_info(text: str) -> Dict:
    """ดึงข้อมูลบัญชีจากหัวกระดาษ"""
    info = {'bank': 'GSB'}

    # เลขที่บัญชี
    acct_match = re.search(r'เลขที่บัญชี\s*(\d+)', text)
    if acct_match:
        info['account_number'] = acct_match.group(1)

    # วันที่ statement
    date_match = re.search(r'วันที่\s*(\d{2}/\d{2}/\d{4})', text)
    if date_match:
        info['statement_date'] = date_match.group(1)

    # ชื่อผู้ถือบัญชี
    name_match = re.search(r'(นาง|นาย|น\.ส\.|นางสาว)\s+([\u0E00-\u0E7F\s]+)', text)
    if name_match:
        info['account_name'] = name_match.group(0).strip()

    # สาขา
    branch_match = re.search(r'สาขา([\u0E00-\u0E7F\w]+)', text)
    if branch_match:
        info['branch'] = branch_match.group(1)

    return info


def _parse_page_lines(
    lines: List[str],
    page_num: int
) -> Tuple[List[Dict], Optional[Dict]]:
    """
    Parse ทุกบรรทัดในหน้า PDF หนึ่งหน้า

    Returns:
        (transactions, page_summary)
    """
    transactions = []
    page_summary = None
    prev_balance = None  # ใช้ track balance ก่อนหน้า

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # ---- B/F ยอดยกมา ----
        bf_match = BF_PATTERN.match(line)
        if bf_match:
            prev_balance = _clean_amount(bf_match.group(1))
            transactions.append({
                'date':           '',
                'date_ce':        '',
                'code':           'B/F',
                'description':    'ยอดยกมา',
                'amount':         prev_balance,
                'tax':            0.0,
                'balance':        prev_balance,
                'branch':         '',
                'operator':       '',
                'type':           'balance_forward',
                'withdrawal':     None,
                'deposit':        None,
                'page':           page_num,
            })
            continue

        # ---- C/F ยอดยกไป ----
        cf_match = CF_PATTERN.match(line)
        if cf_match:
            cf_balance = _clean_amount(cf_match.group(1))
            transactions.append({
                'date':           '',
                'date_ce':        '',
                'code':           'C/F',
                'description':    'ยอดยกไป',
                'amount':         cf_balance,
                'tax':            0.0,
                'balance':        cf_balance,
                'branch':         '',
                'operator':       '',
                'type':           'balance_forward',
                'withdrawal':     None,
                'deposit':        None,
                'page':           page_num,
            })
            continue

        # ---- สรุปท้ายหน้า ----
        page_sum_match = PAGE_SUMMARY_PATTERN.match(line)
        if page_sum_match:
            page_summary = {
                'dr_count':  int(page_sum_match.group(1)),
                'dr_amount': _clean_amount(page_sum_match.group(2)),
                'cr_count':  int(page_sum_match.group(3)),
                'cr_amount': _clean_amount(page_sum_match.group(4)),
            }
            continue

        total_sum_match = TOTAL_SUMMARY_PATTERN.match(line)
        if total_sum_match:
            # total summary บันทึกไว้ แต่ไม่ใส่ใน transactions
            continue

        # ---- รายการ Transaction หลัก ----
        tx = _parse_transaction_line(line, page_num, prev_balance)
        if tx:
            prev_balance = tx['balance']
            transactions.append(tx)

    return transactions, page_summary


def _parse_transaction_line(
    line: str,
    page_num: int,
    prev_balance: Optional[float]
) -> Optional[Dict]:
    """
    Parse บรรทัด transaction เดี่ยว

    Logic แยก deposit/withdrawal:
    - ถ้า balance > prev_balance  → deposit (เงินเข้า)
    - ถ้า balance < prev_balance  → withdrawal (เงินออก)
    - ถ้าไม่มี prev_balance       → ดูจาก TRANSACTION_CODES
    """
    match = TX_PATTERN.match(line)
    if not match:
        return None

    date_str     = match.group(1)   # DD/MM/YYYY (พ.ศ.)
    code         = match.group(2)   # รหัสรายการ
    description  = match.group(3).strip()
    amount_str   = match.group(4)
    tax_str      = match.group(5)
    balance_str  = match.group(6)
    branch       = match.group(7)
    operator     = match.group(8)

    # แปลงวันที่ พ.ศ. → ค.ศ.
    date_ce = _convert_be_to_ce(date_str)
    if not date_ce:
        return None

    amount  = _clean_amount(amount_str)
    tax     = _clean_amount(tax_str)
    balance = _clean_amount(balance_str)

    # แยกประเภทรายการ
    tx_type, withdrawal, deposit = _determine_type(
        code, description, amount, balance, prev_balance
    )

    return {
        'date':        date_str,          # วันที่ พ.ศ. ต้นฉบับ
        'date_ce':     date_ce,           # วันที่ ค.ศ.
        'code':        code,
        'description': description,
        'amount':      amount,
        'tax':         tax,
        'balance':     balance,
        'branch':      branch,
        'operator':    operator,
        'type':        tx_type,
        'withdrawal':  withdrawal,
        'deposit':     deposit,
        'page':        page_num,
        'bank':        'GSB',
    }


def _determine_type(
    code: str,
    description: str,
    amount: float,
    balance: float,
    prev_balance: Optional[float]
) -> Tuple[str, Optional[float], Optional[float]]:
    """
    ตัดสินใจว่าเป็น deposit หรือ withdrawal

    Priority:
    1. เปรียบเทียบ balance (แม่นยำที่สุด)
    2. ดูจาก TRANSACTION_CODES
    3. ดูจาก keyword ใน description
    """
    tx_type = None

    # 1. เปรียบเทียบ balance
    if prev_balance is not None:
        diff = balance - prev_balance
        if abs(diff) > 0.001:  # หลีกเลี่ยง floating point
            tx_type = 'deposit' if diff > 0 else 'withdrawal'

    # 2. ดูจาก transaction code
    if tx_type is None and code in TRANSACTION_CODES:
        tx_type = TRANSACTION_CODES[code][0]

    # 3. ดูจาก keyword
    if tx_type is None:
        tx_type = _guess_type_from_description(description)

    # 4. fallback
    if tx_type is None:
        tx_type = 'unknown'

    withdrawal = amount if tx_type == 'withdrawal' else None
    deposit    = amount if tx_type == 'deposit'    else None

    return tx_type, withdrawal, deposit


def _guess_type_from_description(description: str) -> str:
    """เดาประเภทจาก keyword ในคำอธิบาย"""
    desc_lower = description.lower()

    withdrawal_keywords = [
        'payment', 'transfer', 'withdraw', 'mosw', 'mopsw',
        'จ่าย', 'โอนออก', 'ถอน', 'scan', 'off'
    ]
    deposit_keywords = [
        'deposit', 'receive', 'credit', 'saving', 'sav',
        'รับ', 'ฝาก', 'โอนเข้า', 'cash', 'prize', 'atm cash'
    ]

    for kw in withdrawal_keywords:
        if kw in desc_lower:
            return 'withdrawal'

    for kw in deposit_keywords:
        if kw in desc_lower:
            return 'deposit'

    return 'unknown'


def _verify_tx_type_by_balance(transactions: List[Dict]) -> List[Dict]:
    """
    Pass ที่ 2: ตรวจสอบและแก้ไข type โดยเปรียบเทียบ balance จริง
    กรณีที่ prev_balance ไม่มีตอน parse (เช่นข้ามหน้า)
    """
    prev_bal = None

    for i, tx in enumerate(transactions):
        if tx['type'] == 'balance_forward':
            prev_bal = tx['balance']
            continue

        if prev_bal is not None and tx.get('balance') is not None:
            diff = tx['balance'] - prev_bal

            # ถ้า type ยัง unknown ให้แก้ไข
            if tx['type'] == 'unknown' and abs(diff) > 0.001:
                new_type = 'deposit' if diff > 0 else 'withdrawal'
                amount   = tx['amount']
                transactions[i]['type']       = new_type
                transactions[i]['deposit']    = amount if new_type == 'deposit'    else None
                transactions[i]['withdrawal'] = amount if new_type == 'withdrawal' else None

        prev_bal = tx.get('balance', prev_bal)

    return transactions


def _convert_be_to_ce(date_be: str) -> Optional[str]:
    """
    แปลงวันที่จาก พ.ศ. เป็น ค.ศ.
    Input:  '01/10/2568'  (DD/MM/YYYY พ.ศ.)
    Output: '01/10/2025'  (DD/MM/YYYY ค.ศ.)
    """
    try:
        parts  = date_be.split('/')
        if len(parts) != 3:
            return None

        day   = parts[0]
        month = parts[1]
        year_be = int(parts[2])

        # แปลง พ.ศ. → ค.ศ.
        year_ce = year_be - 543 if year_be > 2400 else year_be

        # ตรวจสอบ
        datetime(year_ce, int(month), int(day))

        return f"{day}/{month}/{year_ce}"

    except (ValueError, IndexError):
        return None


def _clean_amount(value: str) -> float:
    """แปลง string เงิน เช่น '1,234.56' → 1234.56"""
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, AttributeError):
        return 0.0


# ======================================================================
# ทดสอบแบบ standalone
# ======================================================================
if __name__ == '__main__':
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python parse_oomsin.py <statement.pdf>")
        sys.exit(1)

    pdf_file = sys.argv[1]
    print(f"กำลัง parse: {pdf_file}")

    results = parse_oomsin(pdf_file)

    # แสดงผล 10 รายการแรก
    print(f"\n{'='*70}")
    print(f"{'วันที่':<14} {'รหัส':<10} {'คำอธิบาย':<20} {'จำนวนเงิน':>12} {'ประเภท':<12} {'คงเหลือ':>12}")
    print(f"{'='*70}")

    for tx in results[:10]:
        if tx['type'] == 'balance_forward':
            print(f"{'':14} {'':10} {tx['description']:<20} {'':>12} {'':12} {tx['balance']:>12,.2f}")
        else:
            amount_disp = tx['deposit'] or tx['withdrawal'] or 0
            print(
                f"{tx['date_ce']:<14} "
                f"{tx['code']:<10} "
                f"{tx['description']:<20} "
                f"{amount_disp:>12,.2f} "
                f"{tx['type']:<12} "
                f"{tx['balance']:>12,.2f}"
            )

    print(f"{'='*70}")
    print(f"รวมทั้งหมด {len(results)} รายการ")

    # สรุป
    deposits    = sum(tx['deposit']    or 0 for tx in results)
    withdrawals = sum(tx['withdrawal'] or 0 for tx in results)
    print(f"เงินเข้า  (Cr.): {deposits:>15,.2f} บาท")
    print(f"เงินออก  (Dr.): {withdrawals:>15,.2f} บาท")
    print(f"สุทธิ         : {deposits - withdrawals:>15,.2f} บาท")