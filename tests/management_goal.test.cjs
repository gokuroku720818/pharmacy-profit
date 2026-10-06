// 실제 목표 계산이 누락·종료된 월·잘못된 금액을 오해하게 표시하지 않는지 검증한다.
const test = require('node:test');
const assert = require('node:assert/strict');
const { calculateGoal } = require('../static/js/management_goal.js');

test('원 단위로 올림한 하루 목표를 계산한다', () => {
  assert.deepEqual(calculateGoal('1,000', 401, 4, true),
    { status: 'required', gap: 599, daily: 150 });
});
test('누락 기록을 확인하기 전에는 숫자를 제시하지 않는다', () => {
  assert.deepEqual(calculateGoal('1000', 401, 4, false), { status: 'incomplete' });
});
test('남은 영업일이 없으면 0으로 나누지 않는다', () => {
  assert.deepEqual(calculateGoal('1000', 401, 0, true), { status: 'ended', gap: 599 });
});
test('이미 달성한 목표는 음수 하루 목표를 표시하지 않는다', () => {
  assert.deepEqual(calculateGoal('1000', 1100, 0, true), { status: 'achieved', surplus: 100 });
});
test('빈칸·음수·소수·잘못된 쉼표·안전 범위 초과 입력을 거부한다', () => {
  assert.deepEqual(calculateGoal('', 100, 3, true), { status: 'empty' });
  for (const value of ['0', '-1', '1.5', 'abc', '1e5', '1,2', '1000000000001']) {
    assert.deepEqual(calculateGoal(value, 100, 3, true), { status: 'invalid' }, value);
  }
});
