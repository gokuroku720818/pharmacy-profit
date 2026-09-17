/* 매출 관리 시스템 차트 로직 */

const COLORS = {
    primary: 'rgba(13, 110, 253, 0.8)',
    primaryLight: 'rgba(13, 110, 253, 0.2)',
    success: 'rgba(25, 135, 84, 0.8)',
    successLight: 'rgba(25, 135, 84, 0.2)',
    info: 'rgba(13, 202, 240, 0.8)',
    infoLight: 'rgba(13, 202, 240, 0.2)',
    warning: 'rgba(255, 193, 7, 0.8)',
    danger: 'rgba(220, 53, 69, 0.8)',
    dangerLight: 'rgba(220, 53, 69, 0.2)',
    purple: 'rgba(111, 66, 193, 0.8)',
    purpleLight: 'rgba(111, 66, 193, 0.2)',
    orange: 'rgba(253, 126, 20, 0.8)',
    orangeLight: 'rgba(253, 126, 20, 0.2)',
};

const YEAR_COLORS = [
    { border: 'rgba(13, 110, 253, 1)', bg: 'rgba(13, 110, 253, 0.15)' },
    { border: 'rgba(220, 53, 69, 1)', bg: 'rgba(220, 53, 69, 0.15)' },
    { border: 'rgba(25, 135, 84, 1)', bg: 'rgba(25, 135, 84, 0.15)' },
    { border: 'rgba(255, 193, 7, 1)', bg: 'rgba(255, 193, 7, 0.15)' },
    { border: 'rgba(111, 66, 193, 1)', bg: 'rgba(111, 66, 193, 0.15)' },
];

function formatNumber(num) {
    if (num >= 100000000) return (num / 100000000).toFixed(1) + '억';
    if (num >= 10000) return (num / 10000).toFixed(0) + '만';
    return num.toLocaleString();
}

function initDashboardCharts(monthlyData, currentMonth, yearCompare) {
    // 월별 매출 추이
    const trendCtx = document.getElementById('monthlyTrendChart');
    if (trendCtx) {
        new Chart(trendCtx, {
            type: 'line',
            data: {
                labels: monthlyData.labels,
                datasets: [{
                    label: '전체 순익',
                    data: monthlyData.totals,
                    borderColor: COLORS.primary,
                    backgroundColor: COLORS.primaryLight,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 2,
                    pointHoverRadius: 6
                }, {
                    label: '조제+일매순익',
                    data: monthlyData.dispensing_daily,
                    borderColor: COLORS.success,
                    backgroundColor: 'transparent',
                    borderDash: [5, 5],
                    tension: 0.3,
                    pointRadius: 0
                }, {
                    label: '비보험약가차액',
                    data: monthlyData.non_insurance,
                    borderColor: COLORS.orange,
                    backgroundColor: 'transparent',
                    borderDash: [3, 3],
                    tension: 0.3,
                    pointRadius: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'top' } },
                scales: {
                    y: {
                        ticks: { callback: v => formatNumber(v) }
                    }
                }
            }
        });
    }

    // 매출 구성 도넛
    const compCtx = document.getElementById('compositionChart');
    if (compCtx && currentMonth) {
        new Chart(compCtx, {
            type: 'doughnut',
            data: {
                labels: ['조제+일매순익', '비보험약가차액'],
                datasets: [{
                    data: [currentMonth.dispensing_plus_daily || 0, currentMonth.non_insurance || 0],
                    backgroundColor: [COLORS.primary, COLORS.success],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom' }
                }
            }
        });
    }

    // 전년 동월 비교
    const ycCtx = document.getElementById('yearCompareChart');
    if (ycCtx && yearCompare) {
        new Chart(ycCtx, {
            type: 'bar',
            data: {
                labels: yearCompare.labels,
                datasets: yearCompare.datasets.map((ds, i) => ({
                    label: ds.label,
                    data: ds.data,
                    backgroundColor: YEAR_COLORS[i % YEAR_COLORS.length].bg,
                    borderColor: YEAR_COLORS[i % YEAR_COLORS.length].border,
                    borderWidth: 1
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'top' } },
                scales: {
                    y: { ticks: { callback: v => formatNumber(v) } }
                }
            }
        });
    }
}

function initReportChart(reportData) {
    const ctx = document.getElementById('reportChart');
    if (!ctx || !reportData) return;

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: reportData.labels,
            datasets: [{
                label: '조제+일매순익',
                data: reportData.dpd,
                backgroundColor: COLORS.primaryLight,
                borderColor: COLORS.primary,
                borderWidth: 1
            }, {
                label: '비보험약가차액',
                data: reportData.nim,
                backgroundColor: COLORS.successLight,
                borderColor: COLORS.success,
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'top' } },
            scales: {
                x: { stacked: true },
                y: {
                    stacked: true,
                    ticks: { callback: v => formatNumber(v) }
                }
            }
        }
    });
}

function initTrendCharts(yearlyData, dayOfWeekData, growthData) {
    // 연도별 비교
    const ycCtx = document.getElementById('yearlyCompareChart');
    if (ycCtx && yearlyData) {
        new Chart(ycCtx, {
            type: 'line',
            data: {
                labels: ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'],
                datasets: yearlyData.map((yd, i) => ({
                    label: yd.year + '년',
                    data: yd.data,
                    borderColor: YEAR_COLORS[i % YEAR_COLORS.length].border,
                    backgroundColor: YEAR_COLORS[i % YEAR_COLORS.length].bg,
                    fill: false,
                    tension: 0.3,
                    pointRadius: 3
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'top' } },
                scales: {
                    y: { ticks: { callback: v => formatNumber(v) } }
                }
            }
        });
    }

    // 요일별 평균
    const dowCtx = document.getElementById('dayOfWeekChart');
    if (dowCtx && dayOfWeekData) {
        new Chart(dowCtx, {
            type: 'bar',
            data: {
                labels: dayOfWeekData.labels,
                datasets: [{
                    label: '평균 순익',
                    data: dayOfWeekData.values,
                    backgroundColor: [
                        COLORS.primary, COLORS.success, COLORS.info,
                        COLORS.warning, COLORS.danger, COLORS.purple
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { ticks: { callback: v => formatNumber(v) } }
                }
            }
        });
    }

    // 성장률
    const grCtx = document.getElementById('growthRateChart');
    if (grCtx && growthData) {
        new Chart(grCtx, {
            type: 'bar',
            data: {
                labels: growthData.labels,
                datasets: [{
                    label: '전월 대비 증감률 (%)',
                    data: growthData.values,
                    backgroundColor: growthData.values.map(v =>
                        v >= 0 ? COLORS.successLight : COLORS.dangerLight
                    ),
                    borderColor: growthData.values.map(v =>
                        v >= 0 ? COLORS.success : COLORS.danger
                    ),
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { ticks: { callback: v => v + '%' } }
                }
            }
        });
    }
}
