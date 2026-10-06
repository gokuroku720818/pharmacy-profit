/* 목표 계산만 수행한다. 서버 호출이나 정산 기록 변경은 없다. */
(function () {
  'use strict';

  function calculateGoal(raw, current, days, ready) {
    const text = String(raw).trim();
    if (!text) return { status: 'empty' };
    if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(text)) return { status: 'invalid' };
    const target = Number(text.replace(/,/g, ''));
    if (!Number.isSafeInteger(target) || target <= 0 || target > 1000000000000 ||
        !Number.isSafeInteger(current) || !Number.isInteger(days) || days < 0) {
      return { status: 'invalid' };
    }
    if (!ready) return { status: 'incomplete' };
    const gap = target - current;
    if (gap <= 0) return { status: 'achieved', surplus: -gap };
    if (days === 0) return { status: 'ended', gap: gap };
    return { status: 'required', gap: gap, daily: Math.ceil(gap / days) };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { calculateGoal };
  if (typeof document === 'undefined') return;

  function initialize() {
    const panel = document.getElementById('managementGoal');
    if (!panel) return;
    const input = document.getElementById('managementGoalAmount');
    const basis = document.getElementById('managementGoalBasis');
    const output = document.getElementById('managementGoalResult');
    const key = panel.dataset.storageKey;
    const format = value => value.toLocaleString('ko-KR');
    let saveTimer;
    try {
      const saved = JSON.parse(localStorage.getItem(key));
      if (saved && typeof saved.amount === 'string') input.value = saved.amount.slice(0, 17);
      if (saved && (saved.basis === 'base' || saved.basis === 'total')) basis.value = saved.basis;
    } catch (_) { /* 저장소 사용이 불가능해도 계산은 동작한다. */ }

    function update() {
      const current = Number(panel.dataset[basis.value === 'total' ? 'total' : 'base']);
      const result = calculateGoal(input.value, current, Number(panel.dataset.days), panel.dataset.ready === '1');
      const messages = {
        empty: '목표를 입력하면 필요한 하루 평균을 계산합니다.',
        invalid: '목표는 1원~1조원 범위의 정수로 입력해 주세요.',
        incomplete: '미입력일·휴무 설정·일별 합계를 확인한 뒤 필요한 하루 평균을 계산합니다.'
      };
      if (result.status === 'required') {
        output.textContent = `목표까지 ${format(result.gap)}원 · 남은 영업일 하루 평균 ${format(result.daily)}원 필요`;
      } else if (result.status === 'achieved') {
        output.textContent = `입력 실적 기준 목표 달성 · 목표 대비 +${format(result.surplus)}원`;
      } else if (result.status === 'ended') {
        output.textContent = `남은 예정 영업일 없음 · 목표까지 ${format(result.gap)}원`;
      } else {
        output.textContent = messages[result.status];
      }
      clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        try {
          if (!input.value.trim()) localStorage.removeItem(key);
          else if (result.status !== 'invalid') localStorage.setItem(key, JSON.stringify({ amount: input.value, basis: basis.value }));
        } catch (_) { /* 목표 저장 실패는 정산 입력이나 계산에 영향을 주지 않는다. */ }
      }, 400);
    }
    input.addEventListener('input', update);
    basis.addEventListener('change', update);
    update();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
  else initialize();
})();
