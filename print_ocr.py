import json, sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

with open(r'C:\Users\user\.gemini\antigravity\scratch\sales-manager\ocr_test_out.json', encoding='utf-8') as f:
    data = json.load(f)

for item in data:
    print(f"=== IMAGE {item['idx']} ===")
    print(item['text'])
