/* 약국 마감 스크린샷 3장 전용 고정밀 OCR 추출 스크립트 */

// 텍스트 클리닝 및 노이즈 보정
function cleanText(raw) {
    if (!raw) return '';
    return raw
        .replace(/[\r\n]+/g, ' ')
        .replace(/[%]/g, '9')      // 9나 8이 %로 오인식되는 경우
        .replace(/[oO]/g, '0')     // o, O -> 0
        .replace(/[B]/g, '8')      // B -> 8
        .replace(/[lI|]/g, '1');   // l, I, | -> 1
}

// 텍스트에서 1,000원 이상의 모든 숫자 추출
function extractNumbers(rawText) {
    const cleaned = cleanText(rawText);
    // 콤마 또는 마침표가 들어간 금액 패턴 (예: 705,907 / 386.100 / 2,815,600)
    const matches = cleaned.match(/\b\d{1,3}[,\.]\d{3}(?:[,\.]\d{3})?\b/g) || [];
    const nums = [];
    for (let m of matches) {
        const val = parseInt(m.replace(/[^0-9]/g, ''));
        if (val && val >= 1000) nums.push(val);
    }
    return nums;
}

// 날짜 (YYYY-MM-DD) 추출
function extractDate(rawText) {
    const m = rawText.match(/(\d{4})[-.\/](\d{2})[-.\/](\d{2})/);
    return m ? `${m[1]}-${m[2]}-${m[3]}` : null;
}

/**
 * 1번 스샷: [전체 매출 현황]
 * 사용자가 찾는 값: '조제료(이익금)' 컬럼 바로 밑의 숫자 (예: 705,907)
 * 특징: 
 *   - 전체 매출액(388만), 본인부담금(289만), 약가(281만)보다 작음
 *   - 청구액(98만)과 비슷하거나 그보다 작은 20만~200만 원대의 핵심 순익 숫자
 */
function parseShot1(rawText) {
    const date = extractDate(rawText);
    const nums = extractNumbers(rawText);
    if (nums.length === 0) return { value: 0, date, raw: rawText };

    // 내림차순 정렬
    const sorted = [...nums].sort((a, b) => b - a);
    const maxVal = sorted[0]; // 총매출액 (가장 큰 금액)

    // 매출액의 10% ~ 40% 범위에서 약가/본인부담금을 제외한 '조제료(이익금)' 탐색
    let target = 0;
    for (let n of sorted) {
        if (n < maxVal * 0.45 && n >= 50000 && n <= 3000000) {
            target = n;
            break;
        }
    }
    if (target === 0) {
        target = sorted.find(n => n >= 100000 && n <= 2000000) || sorted[0];
    }
    return { value: target, date, raw: nums.slice(0, 6).join(', ') };
}

/**
 * 2번 스샷: [조제 매출 현황]
 * 사용자가 찾는 값: '조제료' 컬럼 바로 밑의 숫자 (예: 386,100)
 * 특징:
 *   - 조제 매출액(320만), 약가(281만), 비보험가(173만), 청구액(98만)보다 작음
 *   - 통상 10만~150만 원 사이의 순수 조제료 숫자
 */
function parseShot2(rawText) {
    const date = extractDate(rawText);
    const nums = extractNumbers(rawText);
    if (nums.length === 0) return { value: 0, date, raw: rawText };

    const sorted = [...nums].sort((a, b) => b - a);
    const maxVal = sorted[0]; // 조제 총매출액

    // 매출액의 8% ~ 30% 범위의 순수 조제료
    let target = 0;
    for (let n of sorted) {
        if (n < maxVal * 0.35 && n >= 50000 && n <= 2000000) {
            target = n;
            break;
        }
    }
    if (target === 0) {
        target = sorted.find(n => n >= 50000 && n <= 1000000) || sorted[0];
    }
    return { value: target, date, raw: nums.slice(0, 6).join(', ') };
}

/**
 * 3번 스샷: [조제내역현황 - 조제약품별]
 * 사용자가 찾는 값: '이익금' 컬럼 바로 밑의 숫자 (예: 556,561)
 * 특징:
 *   - 약가합계(281만)보다 작고 조제수량(8,791) 등의 노이즈를 제외한 5만~200만 원 사이의 금액
 */
function parseShot3(rawText) {
    const date = extractDate(rawText);
    const nums = extractNumbers(rawText);
    if (nums.length === 0) return { value: 0, date, raw: rawText };

    const sorted = [...nums].sort((a, b) => b - a);
    const maxVal = sorted[0]; // 약가합계

    let target = 0;
    for (let n of sorted) {
        if (n < maxVal && n >= 50000 && n <= 2500000) {
            target = n;
            break;
        }
    }
    if (target === 0) {
        target = sorted.find(n => n >= 50000 && n <= 1500000) || sorted[0];
    }
    return { value: target, date, raw: nums.slice(0, 6).join(', ') };
}

/**
 * Tesseract 단일 호출 최적화 엔진
 * 복잡한 워커 설정 없이 가장 안전하고 빠른 공식 recognize() 사용
 */
async function runOCR(imageSource, progressBarId, statusTextId) {
    const statusEl = document.getElementById(statusTextId);
    const progressEl = document.getElementById(progressBarId);
    
    if (statusEl) statusEl.innerHTML = '<span class="text-primary"><i class="fas fa-spinner fa-spin me-1"></i>엔진 스캔 중...</span>';
    if (progressEl) {
        progressEl.style.width = '40%';
        progressEl.classList.remove('d-none');
    }
    
    try {
        // Tesseract v5 공식 단일 함수 호출
        const result = await Tesseract.recognize(imageSource, 'eng', {
            logger: m => {
                if (m.status === 'recognizing text' && progressEl) {
                    progressEl.style.width = `${Math.round(m.progress * 100)}%`;
                }
            }
        });

        if (progressEl) progressEl.style.width = '100%';
        setTimeout(() => { if (progressEl) progressEl.classList.add('d-none'); }, 600);

        return result.data.text || '';
    } catch (err) {
        console.error('Tesseract OCR 실행 에러:', err);
        if (statusEl) statusEl.innerHTML = '<span class="text-muted">직접 입력 가능</span>';
        if (progressEl) progressEl.classList.add('d-none');
        return '';
    }
}
