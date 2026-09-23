import sqlite3
from datetime import date
import pytest

from work_store import init_tables, add, change, edit, read_home, validate_url, stock_flags


@pytest.fixture
def db():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
    con.executemany('INSERT INTO users(id) VALUES (?)', [(1,), (2,)])
    init_tables(con)
    init_tables(con)
    yield con
    con.close()


def test_notes_pinned_completed_and_scoped(db):
    a = add(db, 'note', 1, {'title': '품절', 'detail': '거래처 전화', 'due_date': '2026-09-24'})
    add(db, 'note', 2, {'title': '다른 약국'})
    change(db, 'note', 1, a, 'pin', date(2026, 9, 23))
    change(db, 'note', 1, a, 'toggle', date(2026, 9, 23))
    home = read_home(db, 1, date(2026, 9, 23))
    assert len(home['notes']) == 1
    assert home['notes'][0]['pinned'] == 1 and home['notes'][0]['done'] == 1
    change(db, 'note', 2, a, 'delete', date(2026, 9, 23))
    assert len(read_home(db, 1, date(2026, 9, 23))['notes']) == 1


def test_daily_task_resets_and_overdue_rolls_forward(db):
    a = add(db, 'task', 1, {'title': '마감 체크', 'repeat_daily': '1'})
    b = add(db, 'task', 1, {'title': '전화', 'due_date': '2026-09-22'})
    change(db, 'task', 1, a, 'toggle', date(2026, 9, 23))
    today = read_home(db, 1, date(2026, 9, 23))['tasks']
    assert next(r for r in today if r['id'] == a)['is_done'] is True
    assert next(r for r in today if r['id'] == b)['is_overdue'] is True
    tomorrow = read_home(db, 1, date(2026, 9, 24))['tasks']
    assert next(r for r in tomorrow if r['id'] == a)['is_done'] is False
    assert next(r for r in tomorrow if r['id'] == b)['is_overdue'] is True


def test_stock_alerts_expiry_and_low_qty_are_separate(db):
    item = add(db, 'stock', 1, {'title': '약A', 'quantity': '2', 'minimum_quantity': '5', 'expires_on': '2026-10-01'})
    alert = read_home(db, 1, date(2026, 9, 23))['alerts'][0]
    assert alert['id'] == item and alert['low_stock'] and alert['expiry_status'] == 'soon'
    edit(db, 'stock', 1, item, {'title': '약A', 'quantity': '10', 'minimum_quantity': '5', 'expires_on': '2027-10-01'})
    assert read_home(db, 1, date(2026, 9, 23))['alert_count'] == 0
    assert stock_flags({'quantity': 1, 'minimum_quantity': 0, 'expires_on': '2026-09-22'}, date(2026,9,23))['expiry_status'] == 'expired'


def test_links_validate_https_and_search(db):
    with pytest.raises(ValueError):
        validate_url('javascript:alert(1)')
    with pytest.raises(ValueError):
        validate_url('https://user:secret@example.com')
    add(db, 'link', 1, {'title': '의약품 정보', 'url': 'https://example.com/path'})
    add(db, 'note', 1, {'title': '의약품 품절'})
    result = read_home(db, 1, date(2026,9,23), '의약품')
    assert len(result['links']) == 1 and len(result['notes']) == 1


def test_bad_inputs_do_not_modify_database(db):
    with pytest.raises(ValueError):
        add(db, 'stock', 1, {'title': 'x', 'quantity': '-1', 'minimum_quantity': '2'})
    with pytest.raises(ValueError):
        add(db, 'note', 1, {'title': 'x', 'due_date': '2026-99-99'})
    with pytest.raises(ValueError):
        add(db, 'link', 1, {'title': 'x', 'url': 'http://example.com'})
    assert read_home(db, 1, date(2026,9,23))['stocks'] == []


def test_search_does_not_hide_total_unfinished_count(db):
    add(db, 'task', 1, {'title': '열기'})
    add(db, 'task', 1, {'title': '닫기'})
    result = read_home(db, 1, date(2026,9,23), '열기')
    assert len(result['tasks']) == 1
    assert result['unfinished_count'] == 2
