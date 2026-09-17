/* 약국 마감 스크린샷 3장 고정밀 OCR 및 순익 자동 계산 스크립트 */

// 텍스트 클리닝 및 숫자 보정
function cleanOcrText(raw) {
    if (!raw) return '';
    return raw
        .replace(/[%]/g, '9')      // OCR에서 9나 8이 %로 자주 오인식됨
        .replace(/[B]/g, '8')      // B -> 8
        .replace(/[oO]/g, '0')     // o -> 0
        .replace(/[lI|]/g, '1');   // l -> 1
}

// 텍스트에서 금액 숫자 패턴 추출
function extractAllMoney(rawText) {
    const text = cleanOcrText(rawText);
    // 콤마 또는 점이 들어간 3~8자리 숫자
    const matches = text.match(/\b\d{1,3}[,\.]\d{3}(?:[,\.]\d{3})?\b/g) || [];
    const nums = [];
    for (let m of matches) {
        const n = parseInt(m.replace(/[^0-9]/g, ''));
        if (n && n > 1000) nums.push(n);
    }
    return nums;
}

// 날짜 추출 (YYYY-MM-DD)
function extractDate(text) {
    const m = text.match(/(\d{4})[-.\/](\d{2})[-.\/](\d{2})/);
    if (m) {
        return `${m[1]}-${m[2]}-${m[3]}`;
    }
    return null;
}

/**
 * 스샷 1: [매출구분: 전체] 화면
 * 목표 컬럼: '조제료(이익금)'
 * 표 구조: 매출건수, 매출액, 청구액, 본인부담금, 약가, [조제료(이익금)], 현금, 카드...
 * 조제료(이익금)은 보통 20만~300만 사이이며, 매출액(가장 큰 금액)이나 본인부담금/약가보다 작음
 */
function parseShot1(rawText) {
    const detectedDate = extractDate(rawText);
    const nums = extractAllMoney(rawText);
    
    if (nums.length === 0) return { value: 0, date: detectedDate };

    // 내림차순 정렬
    const sorted = [...nums].sort((a, b) => b - a);
    const maxVal = sorted[0]; // 매출액 (예: 3,881,200)

    // 매출액의 10% ~ 45% 범위에 있는 값이 통상 조제료(이익금)
    // 예: 388만 중 705,907원 (약 18%)
    let candidate = 0;
    for (let n of sorted) {
        if (n < maxVal && n >= 50000 && n <= 3000000) {
            // 본인부담금(약 200~300만), 약가(약 200~300만)보다 작은 값 우선
            if (n <= maxVal * 0.45 && n >= maxVal * 0.08) {
                candidate = n;
                break;
            }
        }
    }

    if (candidate === 0) {
        // 50만~150만 사이의 값
        candidate = sorted.find(n => n >= 200000 && n <= 1500000) || sorted[sorted.length - 1];
    }

    return { value: candidate, date: detectedDate };
}

/**
 * 스샷 2: [매출구분: 조제] 화면
 * 목표 컬럼: '조제료'
 * 표 구조: 매출건수, 매출액, 청구액, 본인부담금, 비보험가, 약가, [조제료], 현금...
 * 조제료는 전체 조제료(이익금)보다 작고 통상 10만~200만 원 사이
 */
function parseShot2(rawText) {
    const detectedDate = extractDate(rawText);
    const nums = extractAllMoney(rawText);
    
    if (nums.length === 0) return { value: 0, date: detectedDate };

    const sorted = [...nums].sort((a, b) => b - a);
    const maxVal = sorted[0]; // 조제 매출액 (예: 3,201,700)

    // 조제료는 매출액의 약 8% ~ 30% 범위 (예: 320만 중 386,100원)
    let candidate = 0;
    for (let n of sorted) {
        if (n < maxVal && n >= 50000 && n <= 2000000) {
            if (n <= maxVal * 0.35 && n >= maxVal * 0.05) {
                candidate = n;
                break;
            }
        }
    }

    if (candidate === 0) {
        candidate = sorted.find(n => n >= 100000 && n <= 1000000) || sorted[sorted.length - 1];
    }

    return { value: candidate, date: detectedDate };
}

/**
 * 스샷 3: [조제내역현황 - 조제약품별] 화면
 * 목표 컬럼: '이익금' (비보험 약가차액)
 * 표 구조: 《합계》, 종수, 조제수량, [이익금], 조제건수, 약가합계...
 * 이익금은 약가합계보다 작고 통상 10만~200만 사이
 */
function parseShot3(rawText) {
    const detectedDate = extractDate(rawText);
    const nums = extractAllMoney(rawText);
    
    if (nums.length === 0) return { value: 0, date: detectedDate };

    const sorted = [...nums].sort((a, b) => b - a);
    const maxVal = sorted[0]; // 약가합계 (예: 2,815,626)

    // 이익금(예: 556,561원)은 약가합계보다 작고 5만~200만 사이
    let candidate = 0;
    for (let n of sorted) {
        if (n < maxVal && n >= 50000 && n <= 2000000) {
            candidate = n;
            break;
        }
    }

    if (candidate === 0 && sorted.length > 0) {
        candidate = sorted.find(n => n >= 50000 && n <= 1500000) || sorted[0];
    }

    return { value: candidate, date: detectedDate };
}

/**
 * Tesseract.js OCR 실행 함수 (숫자 전용 초고속 정밀 모드)
 */
async function runOCR(imageSource, progressBarId, statusTextId) {
    const statusEl = document.getElementById(statusTextId);
    const progressEl = document.getElementById(progressBarId);
    
    if (statusEl) statusEl.textContent = '숫자 인식 엔진 가동 중...';
    if (progressEl) {
        progressEl.style.width = '30%';
        progressEl.classList.remove('d-none');
    }
    
    try {
        // 숫자 인식에는 eng + whitelist 가 한글 모드보다 10배 정확하고 빠름
        const worker = await Tesseract.createWorker('eng');
        await worker.setParameters({
            tessedit_char_whitelist: '0123456789,.-',
            tessedit_pageseg_mode: '6' // 단일 텍스트 블록 모드
        });

        if (statusEl) statusEl.textContent = '숫자 정밀 스캔 중...';
        if (progressEl) progressEl.style.width = '70%';
        
        const ret = await worker.recognize(imageSource);
        await worker.terminate();
        
        if (statusEl) statusEl.textContent = '스캔 완료';
        if (progressEl) progressEl.style.width = '100%';
        setTimeout(() => { if (progressEl) progressEl.classList.add('d-none'); }, 800);
        
        return ret.data.text || '';
    } catch (err) {
        console.error('OCR 실패:', err);
        if (statusEl) statusEl.textContent = '수동 입력 가능';
        if (progressEl) progressEl.classList.add('d-none');
        return '';
    }
}
