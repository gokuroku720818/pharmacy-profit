from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path


def test_home_template_renders_sections_and_escapes_user_text():
    folder = Path(__file__).resolve().parents[1] / 'templates'
    env = Environment(loader=FileSystemLoader(str(folder)), autoescape=select_autoescape(['html']))
    template = env.get_template('work_home.html')
    html = template.render(work_csrf='csrf', today='2026-09-23', query='',
        session={'username':'admin'}, get_flashed_messages=lambda **kwargs: [],
        notes=[{'id':'n','title':'<script>x</script>','detail':'memo','due_date':None,'pinned':1,'done':0}],
        tasks=[], links=[], stocks=[], alerts=[], alert_count=0, unfinished_count=0)
    assert '&lt;script&gt;' in html and '<script>x</script>' not in html
    for title in ('빠른 메모','체크리스트','즐겨찾기','유효기간','순익 대시보드'):
        assert title in html
    assert 'name="csrf_token" value="csrf"' in html
    assert 'href="/?profit=1"' in html
