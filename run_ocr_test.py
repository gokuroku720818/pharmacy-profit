import sys, os, json
sys.path.append(r'C:\Users\user\.gemini\antigravity\scratch\sales-manager')
from ocr_parser import run_win_ocr

imgs = [
    r'C:\Users\user\.gemini\antigravity\brain\486cba33-5b25-4a5c-9326-33600cb200e4\.user_uploaded\media_1789632461956.png',
    r'C:\Users\user\.gemini\antigravity\brain\486cba33-5b25-4a5c-9326-33600cb200e4\.user_uploaded\media_1789632462958.png',
    r'C:\Users\user\.gemini\antigravity\brain\486cba33-5b25-4a5c-9326-33600cb200e4\.user_uploaded\media_1789632463883.png'
]

results = []
for i, p in enumerate(imgs, 1):
    res = run_win_ocr(p)
    results.append({'idx': i, 'text': res.get('text') if res else None, 'lines': res.get('lines') if res else None})

out_path = r'C:\Users\user\.gemini\antigravity\scratch\sales-manager\ocr_test_out.json'
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print('OCR test finished successfully')
