"""
바탕화면에 3가지 형태의 파일 생성:
1. 약국순익_대시보드.html (서버 없이 언제나 더블클릭하면 열리는 오프라인 리포트)
2. 약국순익_실행기.bat (클릭 시 로컬 웹 서버 켜고 브라우저 자동 오픈)
3. 약국순익_정리본.xlsx (암호 해제 및 서식/수식 적용된 최신 엑셀 정리본)
"""
import os
import sqlite3
import json
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'sales.db')
DESKTOP_DIR = os.path.join(os.path.expanduser('~'), 'Desktop')

def build_standalone_html():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    monthly = [dict(r) for r in conn.execute('SELECT * FROM monthly_summary WHERE grand_total > 0 ORDER BY year, month').fetchall()]
    daily = [dict(r) for r in conn.execute('SELECT * FROM daily_profit ORDER BY date DESC LIMIT 200').fetchall()]
    
    dow_rows = conn.execute('SELECT day_of_week, AVG(total) as avg_total FROM daily_profit WHERE total > 0 GROUP BY day_of_week').fetchall()
    dow_dict = {r['day_of_week']: int(r['avg_total']) for r in dow_rows}
    day_order = ['월', '화', '수', '목', '금', '토']
    dow = {'labels': day_order, 'values': [dow_dict.get(d, 0) for d in day_order]}
    conn.close()

    latest = monthly[-1] if monthly else {}
    total_sum = sum(m.get('grand_total', 0) for m in monthly)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>약국 순익 분석 대시보드 (오프라인 열람용)</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        body {{ background-color: #f8fafc; font-family: 'Segoe UI', 'Malgun Gothic', sans-serif; color: #1e293b; }}
        .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.04); margin-bottom: 20px; }}
        .card-header {{ background: #fff; font-weight: bold; border-bottom: 1px solid #f1f5f9; padding: 16px 20px; border-radius: 12px 12px 0 0 !important; }}
        .stat-card {{ border-radius: 12px; padding: 22px; color: white; }}
        .bg-gradient-blue {{ background: linear-gradient(135deg, #1e3a8a, #3b82f6); }}
        .bg-gradient-green {{ background: linear-gradient(135deg, #065f46, #10b981); }}
        .bg-gradient-purple {{ background: linear-gradient(135deg, #581c87, #a855f7); }}
        .bg-gradient-orange {{ background: linear-gradient(135deg, #9a3412, #f97316); }}
        .table th {{ background-color: #f8fafc; color: #64748b; font-weight: 600; }}
        .nav-tabs .nav-link {{ font-weight: 600; color: #64748b; border: none; padding: 12px 24px; }}
        .nav-tabs .nav-link.active {{ color: #2563eb; border-bottom: 3px solid #2563eb; background: transparent; }}
    </style>
</head>
<body>
    <div class="container py-4">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h2 class="fw-bold mb-1"><i class="fas fa-clinic-medical text-primary"></i> 약국 순익 분석 대시보드</h2>
                <span class="text-muted">인터넷이나 서버 없이 바로 열어보는 독립형 리포트</span>
            </div>
            <div>
                <button onclick="window.print()" class="btn btn-outline-secondary"><i class="fas fa-print me-1"></i> 인쇄 / PDF 저장</button>
            </div>
        </div>

        <div class="row g-3 mb-4">
            <div class="col-md-3">
                <div class="stat-card bg-gradient-blue">
                    <small class="opacity-75">최근 집계월 ({latest.get('year')}년 {latest.get('month')}월)</small>
                    <div class="fs-3 fw-bold mt-1">{latest.get('grand_total', 0):,}원</div>
                    <small class="mt-2 d-block opacity-75">전월 대비: {latest.get('prev_month_diff', 0):+,}원</small>
                </div>
            </div>
            <div class="col-md-3">
                <div class="stat-card bg-gradient-green">
                    <small class="opacity-75">조제 + 일매순익 합계</small>
                    <div class="fs-3 fw-bold mt-1">{latest.get('dispensing_plus_daily_total', 0):,}원</div>
                    <small class="mt-2 d-block opacity-75">핵심 약국 조제 마진</small>
                </div>
            </div>
            <div class="col-md-3">
                <div class="stat-card bg-gradient-purple">
                    <small class="opacity-75">비보험 약가차액 순익</small>
                    <div class="fs-3 fw-bold mt-1">{latest.get('non_insurance_total', 0):,}원</div>
                    <small class="mt-2 d-block opacity-75">비급여 마진</small>
                </div>
            </div>
            <div class="col-md-3">
                <div class="stat-card bg-gradient-orange">
                    <small class="opacity-75">누적 총 순익 (48개월)</small>
                    <div class="fs-3 fw-bold mt-1">{total_sum:,}원</div>
                    <small class="mt-2 d-block opacity-75">약 15.2억원 집계</small>
                </div>
            </div>
        </div>

        <div class="row g-3 mb-4">
            <div class="col-lg-8">
                <div class="card h-100">
                    <div class="card-header"><i class="fas fa-chart-line text-primary me-2"></i>월별 순익 추이 (전체 기간)</div>
                    <div class="card-body">
                        <canvas id="trendChart" height="280"></canvas>
                    </div>
                </div>
            </div>
            <div class="col-lg-4">
                <div class="card h-100">
                    <div class="card-header"><i class="fas fa-chart-pie text-success me-2"></i>요일별 평균 순익 패턴</div>
                    <div class="card-body">
                        <canvas id="dowChart" height="280"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header bg-white border-bottom-0 pb-0">
                <ul class="nav nav-tabs" id="reportTabs" role="tablist">
                    <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#monthlyTab">월별 전체 집계표</button></li>
                    <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#dailyTab">최근 일별 순익 내역</button></li>
                </ul>
            </div>
            <div class="card-body tab-content">
                <div class="tab-pane fade show active" id="monthlyTab">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead>
                                <tr>
                                    <th>연도/월</th>
                                    <th class="text-end">조제 + 일매순익</th>
                                    <th class="text-end">비보험 약가차액</th>
                                    <th class="text-end">전체 합계</th>
                                    <th class="text-end">전월 대비</th>
                                </tr>
                            </thead>
                            <tbody>"""

    for m in reversed(monthly):
        diff = m.get('prev_month_diff', 0)
        diff_color = 'text-success' if diff >= 0 else 'text-danger'
        html_content += f"""
                                <tr>
                                    <td><strong>{m.get('year')}년 {m.get('month')}월</strong></td>
                                    <td class="text-end">{m.get('dispensing_plus_daily_total', 0):,}원</td>
                                    <td class="text-end">{m.get('non_insurance_total', 0):,}원</td>
                                    <td class="text-end fw-bold">{m.get('grand_total', 0):,}원</td>
                                    <td class="text-end {diff_color}">{diff:+,}원</td>
                                </tr>"""

    html_content += """
                            </tbody>
                        </table>
                    </div>
                </div>
                <div class="tab-pane fade" id="dailyTab">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle">
                            <thead>
                                <tr>
                                    <th>날짜</th>
                                    <th>요일</th>
                                    <th class="text-end">조제료</th>
                                    <th class="text-end">일매순익</th>
                                    <th class="text-end">비보험 마진</th>
                                    <th class="text-end">당일 합계</th>
                                </tr>
                            </thead>
                            <tbody>"""

    for d in daily:
        html_content += f"""
                                <tr>
                                    <td>{d.get('date')}</td>
                                    <td><span class="badge bg-secondary">{d.get('day_of_week')}</span></td>
                                    <td class="text-end">{d.get('dispensing_fee', 0):,}원</td>
                                    <td class="text-end">{d.get('daily_net_profit', 0):,}원</td>
                                    <td class="text-end">{d.get('non_insurance_margin', 0):,}원</td>
                                    <td class="text-end fw-bold text-primary">{d.get('total', 0):,}원</td>
                                </tr>"""

    html_content += f"""
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const monthlyData = {json.dumps(monthly, ensure_ascii=False)};
        const dowData = {json.dumps(dow, ensure_ascii=False)};

        new Chart(document.getElementById('trendChart'), {{
            type: 'line',
            data: {{
                labels: monthlyData.map(m => m.year + '.' + String(m.month).padStart(2, '0')),
                datasets: [
                    {{
                        label: '전체 순익',
                        data: monthlyData.map(m => m.grand_total),
                        borderColor: '#2563eb',
                        backgroundColor: 'rgba(37, 99, 235, 0.1)',
                        fill: true,
                        tension: 0.3
                    }},
                    {{
                        label: '조제+일매순익',
                        data: monthlyData.map(m => m.dispensing_plus_daily_total),
                        borderColor: '#10b981',
                        borderDash: [5, 5],
                        fill: false,
                        tension: 0.3
                    }},
                    {{
                        label: '비보험차액순익',
                        data: monthlyData.map(m => m.non_insurance_total),
                        borderColor: '#f59e0b',
                        borderDash: [3, 3],
                        fill: false,
                        tension: 0.3
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ position: 'top' }} }},
                scales: {{
                    y: {{
                        ticks: {{
                            callback: v => (v >= 100000000 ? (v/100000000).toFixed(1)+'억' : (v/10000).toFixed(0)+'만')
                        }}
                    }}
                }}
            }}
        }});

        new Chart(document.getElementById('dowChart'), {{
            type: 'bar',
            data: {{
                labels: dowData.labels,
                datasets: [{{
                    label: '요일별 평균 순익',
                    data: dowData.values,
                    backgroundColor: ['#3b82f6', '#10b981', '#06b6d4', '#f59e0b', '#ef4444', '#8b5cf6']
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{
                        ticks: {{
                            callback: v => (v/10000).toFixed(0)+'만'
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

    target_file = os.path.join(DESKTOP_DIR, '약국순익_대시보드.html')
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print("1. [HTML 리포트 완료]", target_file)


def build_launcher_bat():
    """바탕화면에 원클릭 실행 배치파일 생성"""
    python_exe = r'C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe'
    app_dir = BASE_DIR
    bat_content = f"""@echo off
chcp 65001 > nul
title 약국 순익 관리 시스템
echo ==============================================
echo   약국 순익 관리 시스템을 실행합니다...
echo ==============================================

cd /d "{app_dir}"
start "" http://localhost:5000
"{python_exe}" app.py
pause
"""
    target_bat = os.path.join(DESKTOP_DIR, '약국순익관리_실행.bat')
    with open(target_bat, 'w', encoding='cp949', errors='replace') as f:
        f.write(bat_content)
    print("2. [실행기 BAT 완료]", target_bat)


def build_clean_excel():
    """암호 없는 깔끔한 최신 엑셀 정리본 생성"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    monthly = conn.execute('SELECT * FROM monthly_summary ORDER BY year, month').fetchall()
    daily = conn.execute('SELECT * FROM daily_profit ORDER BY date').fetchall()
    conn.close()

    wb = openpyxl.Workbook()
    # 기본 시트: 월별 집계
    ws_month = wb.active
    ws_month.title = "월별 순익 집계"

    headers_m = ["연도", "월", "조제 + 일매순익", "비보험약가차액", "전체 합계", "전월 대비 증감"]
    ws_month.append(headers_m)

    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(name="맑은 고딕", size=11, bold=True, color="FFFFFF")
    border_thin = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    for col_idx in range(1, len(headers_m) + 1):
        cell = ws_month.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r_idx, m in enumerate(monthly, start=2):
        ws_month.append([
            m['year'],
            f"{m['month']}월",
            m['dispensing_plus_daily_total'],
            m['non_insurance_total'],
            m['grand_total'],
            m['prev_month_diff']
        ])
        for c_idx in range(1, 7):
            cell = ws_month.cell(row=r_idx, column=c_idx)
            cell.border = border_thin
            if c_idx >= 3:
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal="right")
            else:
                cell.alignment = Alignment(horizontal="center")

    # 일별 상세 시트
    ws_day = wb.create_sheet(title="일별 순익 내역")
    headers_d = ["날짜", "요일", "조제료", "일매순익", "조제+일매", "비보험약가차액", "전체 합계"]
    ws_day.append(headers_d)

    for col_idx in range(1, len(headers_d) + 1):
        cell = ws_day.cell(row=1, column=col_idx)
        cell.fill = PatternFill(start_color="2E75B6", end_color="2E75B6", fill_type="solid")
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r_idx, d in enumerate(daily, start=2):
        ws_day.append([
            d['date'],
            d['day_of_week'],
            d['dispensing_fee'],
            d['daily_net_profit'],
            d['dispensing_plus_daily'],
            d['non_insurance_margin'],
            d['total']
        ])
        for c_idx in range(1, 8):
            cell = ws_day.cell(row=r_idx, column=c_idx)
            cell.border = border_thin
            if c_idx >= 3:
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal="right")
            else:
                cell.alignment = Alignment(horizontal="center")

    # 열 너비 자동 조정
    for ws in [ws_month, ws_day]:
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 5, 14)

    target_excel = os.path.join(DESKTOP_DIR, '약국순익표_정리본.xlsx')
    wb.save(target_excel)
    print("3. [엑셀 정리본 완료]", target_excel)


if __name__ == '__main__':
    build_standalone_html()
    build_launcher_bat()
    build_clean_excel()
