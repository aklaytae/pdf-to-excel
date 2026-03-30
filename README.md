# KTB Statement PDF → Excel Converter

แปลง Statement ธนาคารกรุงไทย (PDF) เป็น Excel สรุปรายรับ-รายจ่ายรายวัน แยกทุกเดือนอัตโนมัติ

## Requirements

- Node.js >= 14
- Python 3 พร้อม libraries:
  - `pdfplumber` (`pip install pdfplumber`)
  - `openpyxl` (`pip install openpyxl`)

## Quick Start

```bash
npm start
# แล้วเปิด http://localhost:3000
```

## Output Excel Format

ไฟล์ Excel จะมี:
- **Sheet สรุปรวมทุกเดือน** — ยอดรวมแต่ละเดือนทั้งหมด
- **Sheet แต่ละเดือน** (เช่น มี.ค.68, เม.ย.68 ...) — สรุปรายวันภายในเดือนนั้น

แต่ละ Sheet แสดง:
| วันที่ | วัน | จำนวนรายการ | รายจ่าย | รายรับ | ยอดสุทธิ |
|--------|-----|------------|---------|--------|---------|

- 🔴 แถวสีแดง = วันที่มีรายจ่ายมากกว่ารายรับ
- 🟢 แถวสีเขียว = วันที่มีรายรับมากกว่า
