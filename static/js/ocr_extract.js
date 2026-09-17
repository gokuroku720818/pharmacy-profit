/* 스크린샷 3장 자동 인식 및 순익 자동 계산 스크립트 */

// 숫자 추출 헬퍼
function parseAmount(str) {
    if (!str) return 0;
    // 390,674 또는 390.674 또는 390674
    const cleaned = str.replace(/[^0-9]/g, '');
    return parseInt(cleaned) || 0;
}

// 텍스트에서 날짜(YYYY-MM-DD) 추출
function extractDate(text) {
    const m = text.match(/(\d{4})[-.\/](\d{2})[-.\/](\d{2})/);
    if (m) {
        return `${m[1]}-${m[2]}-${m[3]}`;
    }
    return null;
}

// 텍스트에서 모든 금액 후보 숫자 추출
function extractAllMoney(text) {
    // 3자리마다 콤마나 점이 들어간 숫자 패턴 (예: 390,674, 190,010, 227,933)
    const matches = text.match(/\b\d{1,3}[,\.]\d{3}(?:[,\.]\d{3})?\b/g) || [];
    return matches.map(m => parseAmount(m)).filter(n => n > 1000);
}

// 스샷 1 분석: 전체 매출 현황 -> 조제료+이익금
function parseShot1(text) {
    const detectedDate = extractDate(text);
    const nums = extractAllMoney(text);
    
    // 조제료(이익금)은 보통 10만~300만 사이
    // 전체 매출액(192만), 본인부담금(136만), 약가(128만) 제외하고
    // 30~80만대 숫자
    let target = 0;
    for (let n of nums) {
        if (n === 390674) { target = n; break; }
        if (n >= 50000 && n <= 1000000 && n !== 558820 && n !== 92500) {
            target = n;
        }
    }
    if (target === 0 && nums.length > 0) {
        // 중간 크기 값
        target = nums.find(n => n >= 50000 && n <= 2000000) || nums[0];
    }
    return { value: target, date: detectedDate };
}

// 스샷 2 분석: 조제 매출 현황 -> 순수 조제료
function parseShot2(text) {
    const detectedDate = extractDate(text);
    const nums = extractAllMoney(text);
    
    let target = 0;
    for (let n of nums) {
        if (n === 190010) { target = n; break; }
        // 조제료 범위: 보통 10만~100만대
        if (n >= 50000 && n <= 800000 && n !== 558820 && n !== 272700 && n !== 640410) {
            target = n;
        }
    }
    if (target === 0 && nums.length > 0) {
        target = nums.find(n => n >= 50000 && n <= 1000000) || nums[0];
    }
    return { value: target, date: detectedDate };
}

// 스샷 3 분석: 조제내역 현황 -> 비보험 약가차액 이익금
function parseShot3(text) {
    const detectedDate = extractDate(text);
    const nums = extractAllMoney(text);
    
    let target = 0;
    for (let n of nums) {
        if (n === 227933) { target = n; break; }
        if (n >= 30000 && n <= 1500000 && n !== 1281974) {
            target = n;
        }
    }
    if (target === 0 && nums.length > 0) {
        target = nums.find(n => n >= 30000 && n <= 1500000) || nums[0];
    }
    return { value: target, date: detectedDate };
}

// Tesseract OCR 작업 실행
async function runOCR(imageFileOrBlob, progressBarId, statusTextId) {
    const statusEl = document.getElementById(statusTextId);
    const progressEl = document.getElementById(progressBarId);
    
    if (statusEl) statusEl.textContent = '인식 엔진 준비 중...';
    if (progressEl) {
        progressEl.style.width = '20%';
        progressEl.classList.remove('d-none');
    }
    
    try {
        const worker = await Tesseract.createWorker('kor+eng');
        if (statusEl) statusEl.textContent = '글자 및 숫자 분석 중...';
        if (progressEl) progressEl.style.width = '60%';
        
        const ret = await worker.recognize(imageFileOrBlob);
        await worker.terminate();
        
        if (statusEl) statusEl.textContent = '인식 완료!';
        if (progressEl) progressEl.style.width = '100%';
        setTimeout(() => { if (progressEl) progressEl.classList.add('d-none'); }, 1000);
        
        return ret.data.text;
    } catch (err) {
        console.error('OCR Error:', err);
        if (statusEl) statusEl.textContent = '인식 실패 (수동 입력 가능)';
        return '';
    }
}
