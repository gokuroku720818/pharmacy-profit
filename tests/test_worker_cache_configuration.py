"""The user-level in-memory cache is only coherent inside one Gunicorn worker.

A multi-worker deployment has separate _USER_CACHE dictionaries: saving in worker A
cannot invalidate worker B. Keep *both* startup routes single-worker until a shared
cache/version mechanism is implemented. Never use real pharmacy data in this test.
"""
import runpy
import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_procfile_never_starts_multiple_workers_with_process_local_financial_cache():
    line = (ROOT / 'Procfile').read_text(encoding='utf-8').strip()
    assert line.startswith('web: ')
    command = shlex.split(line.removeprefix('web: '))
    assert command[0] == 'gunicorn'
    options = [command[i + 1] for i, word in enumerate(command[:-1]) if word in ('-w', '--workers')]
    assert options == ['1'], 'Two Gunicorn workers have isolated caches and may display stale profit'
    assert any(word in command for word in ('--threads', '--worker-class'))


def test_gunicorn_default_also_requires_one_worker():
    config = runpy.run_path(str(ROOT / 'gunicorn.conf.py'))
    assert config['workers'] == 1
    assert config['threads'] == 4
    assert callable(config['post_worker_init'])


def test_ci_smoke_exercises_default_worker_count_instead_of_masking_it():
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(encoding='utf-8')
    assert 'gunicorn -c gunicorn.conf.py -b 127.0.0.1:8765 app:app' in workflow
    assert 'gunicorn -c gunicorn.conf.py -w 1 ' not in workflow


def test_all_production_start_commands_load_gunicorn_config_hooks():
    procfile = (ROOT / 'Procfile').read_text(encoding='utf-8')
    render = (ROOT / 'render.yaml').read_text(encoding='utf-8')
    assert 'gunicorn -c gunicorn.conf.py app:app' in procfile
    assert 'startCommand: gunicorn -c gunicorn.conf.py app:app' in render
