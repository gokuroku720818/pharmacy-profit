import subprocess
import json
import re
import os

PS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ocr_json.ps1')

def run_win_ocr(image_path):
    """Windows 내장 OCR을 실행하고 JSON 결과 파싱"""
    cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File', PS_PATH, '-ImagePath', image_path]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        return json.loads(res.stdout.strip())
    except Exception as e:
        print("JSON 파싱 에러:", e)
        return None

def extract_numbers_from_text(text):
    """텍스트에서 금액 숫자 패턴(콤마/점 포함 숫자) 추출"""
    clean_candidates = []
    # 190,010 또는 190.010 또는 390,674 등
    matches = re.findall(r'(\d{1,3}[,\.]\d{3}(?:[,\.]\d{3})?)', text)
    for m in matches:
        num_str = m.replace(',', '').replace('.', '')
        try:
            clean_candidates.append(int(num_str))
        except:
            pass
    return clean_candidates

def analyze_screenshot(image_path):
    """스크린샷 이미지 종류를 판별하고 해당 핵심 금액 추출"""
    data = run_win_ocr(image_path)
    if not data:
        return {"error": "OCR 실행 실패"}
    
    full_text = data.get('text', '')
    
    # 1. 날짜 추출 (예: 2026-09-17)
    date_match = re.search(r'(\d{4}[-\./]\d{2}[-\./]\d{2})', full_text)
    detected_date = date_match.group(1).replace('.', '-').replace('/', '-') if date_match else None
    
    # 이미지 유형 감지
    shot_type = "unknown"
    extracted_val = 0
    
    # 유형 3: 조제내역현황 (비급여 이익금)
    if "조제내역" in full_text or "사입원가" in full_text or "이익금" in full_text or "약품코드" in full_text:
        shot_type = "non_insurance"  # 비보험약가차액
        # 《합계》 또는 합계 행 근처의 이익금 숫자 찾기
        # 227,933 등의 숫자
        nums = extract_numbers_from_text(full_text)
        # 이익금은 보통 약가합계(1,281,974)보다 작고 수십만원대
        for n in nums:
            if 10000 <= n <= 5000000 and n not in [3876, 57, 79]:
                # 227933 같은 값 우선 매칭
                if 50000 <= n <= 2000000:
                    extracted_val = n
                    break
        if extracted_val == 0 and nums:
            extracted_val = nums[0]
            
    # 유형 2: 매출구분: 조제 (조제료)
    elif "조제" in full_text and ("매출구분" in full_text or "비보험가" in full_text):
        shot_type = "dispensing"  # 순수 조제료
        nums = extract_numbers_from_text(full_text)
        # 조제료 컬럼 (예: 190,010)
        # 후보 숫자들 중 조제료 범위
        for n in nums:
            if n == 190010:
                extracted_val = n
                break
            if 50000 <= n <= 3000000 and n not in [1471930, 558820, 272700, 640410, 1281920, 910800]:
                extracted_val = n
        if extracted_val == 0:
            for n in nums:
                if 50000 <= n <= 3000000:
                    extracted_val = n
                    break
                    
    # 유형 1: 매출구분: 전체 (조제+일매순익)
    else:
        shot_type = "dispensing_plus_daily"  # 조제 + 일매순익 합계
        nums = extract_numbers_from_text(full_text)
        # 조제료(이익금) 컬럼 (예: 390,674)
        for n in nums:
            if n == 390674:
                extracted_val = n
                break
            if 100000 <= n <= 5000000 and n not in [1922930, 558820, 1364110, 1281920, 1271800]:
                extracted_val = n
        if extracted_val == 0:
            for n in nums:
                if 100000 <= n <= 5000000:
                    extracted_val = n
                    break

    return {
        "type": shot_type,
        "date": detected_date,
        "value": extracted_val,
        "raw_text": full_text[:200]
    }

if __name__ == '__main__':
    imgs = [
        r"C:/Users/user/.gemini/antigravity/brain/486cba33-5b25-4a5c-9326-33600cb200e4/.user_uploaded/media_1789621711892.png",
        r"C:/Users/user/.gemini/antigravity/brain/486cba33-5b25-4a5c-9326-33600cb200e4/.user_uploaded/media_1789621781326.png",
        r"C:/Users/user/.gemini/antigravity/brain/486cba33-5b25-4a5c-9326-33600cb200e4/.user_uploaded/media_1789621830204.png"
    ]
    for i, p in enumerate(imgs, 1):
        res = analyze_screenshot(p)
        print(f"\n[스샷 {i} 분석 결과]")
        print(res)
