import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from latency_probe import percentiles, validate_url, run_cycle


def test_percentiles_are_nearest_rank_and_handle_single():
    assert percentiles([30]) == {'p50_ms': 30, 'p95_ms': 30, 'max_ms': 30}
    assert percentiles(list(range(1, 21))) == {'p50_ms': 10, 'p95_ms': 19, 'max_ms': 20}


def test_rejects_plain_http_credentials_except_loopback():
    assert validate_url('https://pharmacy.example') == 'https://pharmacy.example'
    assert validate_url('http://localhost:5000/') == 'http://localhost:5000'
    try:
        validate_url('http://pharmacy.example')
    except ValueError:
        pass
    else:
        raise AssertionError('must reject insecure login destination')


def test_probe_authenticates_and_checks_every_page_without_writes():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            seen.append(('POST', self.path))
            assert self.path == '/login'
            body = self.rfile.read(int(self.headers['Content-Length']))
            assert b'username=test-user' in body
            assert b'password=test-pass' in body
            assert b'csrf_token=test-csrf-token' in body
            self.send_response(302)
            self.send_header('Set-Cookie', 'sid=ok; Path=/; HttpOnly')
            self.send_header('Location', '/')
            self.end_headers()
        def do_GET(self):
            seen.append(('GET', self.path))
            if self.path != '/login' and 'sid=ok' not in self.headers.get('Cookie', ''):
                self.send_response(302)
                self.send_header('Location', '/login')
            else:
                self.send_response(200)
            self.end_headers()
            if self.path == '/login':
                self.wfile.write(b'<form><input type="hidden" name="csrf_token" value="test-csrf-token"></form>')
            else:
                self.wfile.write(b'ok')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        results = run_cycle(f'http://127.0.0.1:{server.server_port}', 'test-user', 'test-pass')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert results['login_get']['status'] == 200
    assert results['login_post']['status'] == 302
    assert results['dashboard']['status'] == 200
    assert results['calendar']['status'] == 200
    assert results['calculator']['status'] == 200
    assert all(method == 'GET' for method, path in seen if path != '/login')
