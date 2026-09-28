"""Read-only HTTP latency probe; credentials are read from environment only.

Use a dedicated test account. GET page timings include network, NOT paint time.
The POST /login is the only non-GET operation. No customer data is printed.
"""

import argparse
import http.cookiejar
import json
import math
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, Request

PAGES = (
    ('dashboard', '/'), ('calendar', '/calendar'), ('input', '/input'),
    ('report', '/report'), ('trend', '/trend'), ('calculator', '/calculator'),
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def validate_url(url):
    parts = urlsplit(url)
    allowed = parts.scheme == 'https' or (
        parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1')
    )
    if not allowed or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('URL must be a bare HTTPS origin (HTTP only for localhost)')
    return url.rstrip('/')


def percentiles(values):
    if not values:
        raise ValueError('No samples')
    ordered = sorted(values)
    return {'p50_ms': ordered[math.ceil(len(ordered) * .5) - 1],
            'p95_ms': ordered[math.ceil(len(ordered) * .95) - 1],
            'max_ms': ordered[-1]}


def _request(opener, url, body=None, timeout=15):
    headers = {'User-Agent': 'PharmacyLatencyProbe/1.0'}
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = Request(url, data=body, headers=headers)
    start = time.perf_counter()
    try:
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            # A blocked redirect is returned as HTTPError by urllib.
            response = exc
        with response:
            status = response.status
            response.read()
    except Exception:
        # Never print the underlying URL or credentials to stdout.
        raise RuntimeError('HTTP probe failed; inspect server logs privately') from None
    return {'status': status, 'ms': round((time.perf_counter() - start) * 1000, 1)}


def run_cycle(base_url, username, password):
    origin = validate_url(base_url)
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
    readings = {}
    readings['login_get'] = _request(opener, origin + '/login')
    if readings['login_get']['status'] != 200:
        raise RuntimeError('Login form did not return HTTP 200')
    body = urlencode({'username': username, 'password': password}).encode()
    readings['login_post'] = _request(opener, origin + '/login', body)
    if readings['login_post']['status'] != 302:
        raise RuntimeError('Login did not redirect; verify dedicated test account privately')
    for name, path in PAGES:
        readings[name] = _request(opener, origin + path)
        if readings[name]['status'] != 200:
            raise RuntimeError('Authenticated page failed: ' + name)
    return readings


def main():
    parser = argparse.ArgumentParser(description='Compare HTTP stage latency without exposing credentials')
    parser.add_argument('--url', required=True, help='Bare HTTPS origin only')
    parser.add_argument('--runs', type=int, default=5, help='1-30 login/page cycles')
    arguments = parser.parse_args()
    if not 1 <= arguments.runs <= 30:
        parser.error('--runs must be between 1 and 30')
    try:
        origin = validate_url(arguments.url)
    except ValueError as exc:
        parser.error(str(exc))
    username, password = os.getenv('PERF_TEST_USER'), os.getenv('PERF_TEST_PASSWORD')
    if not username or not password:
        parser.error('Set PERF_TEST_USER and PERF_TEST_PASSWORD for a dedicated test account')
    samples = {}
    for _ in range(arguments.runs):
        result = run_cycle(origin, username, password)
        for stage, entry in result.items():
            samples.setdefault(stage, []).append(entry['ms'])
    print(json.dumps({'runs': arguments.runs,
                      'note': 'Round-trip HTTP timings only; first hit is not necessarily a cold start.',
                      'stages': {key: percentiles(values) for key, values in samples.items()}},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
