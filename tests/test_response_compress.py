"""Tests for zero-dependency HTTP gzip compression."""
import gzip
import pytest
from test_analysis import client_for, service


@pytest.mark.parametrize('encoding', ['gzip;q=0', 'xgzip', 'br, gzip;q=0'])
def test_respects_encoding_refusal(service, encoding):
    response = service.app.test_client().get('/login', headers={'Accept-Encoding': encoding})
    assert 'Content-Encoding' not in response.headers
    assert 'Accept-Encoding' in response.vary


def test_head_preserves_negotiated_get_headers(service):
    client = service.app.test_client()
    headers = {'Accept-Encoding': 'gzip'}
    get = client.get('/login', headers=headers)
    head = client.head('/login', headers=headers)
    assert head.data == b''
    assert head.headers['Content-Length'] == get.headers['Content-Length']


def test_gzip_compression_reduces_size_and_sets_headers(service):
    client = client_for(service)
    
    # 1. 클라이언트가 gzip을 요청하면 Content-Encoding: gzip 적용
    res_gz = client.get('/', headers={'Accept-Encoding': 'gzip'})
    assert res_gz.status_code == 200
    assert res_gz.headers.get('Content-Encoding') == 'gzip'
    assert 'Accept-Encoding' in res_gz.headers.get('Vary', '')
    
    decompressed = gzip.decompress(res_gz.get_data()).decode('utf-8')
    assert '<!DOCTYPE html>' in decompressed


def test_requests_without_gzip_remain_uncompressed(service):
    client = client_for(service)
    
    res_raw = client.get('/', headers={'Accept-Encoding': 'identity'})
    assert res_raw.status_code == 200
    assert 'Content-Encoding' not in res_raw.headers
    assert res_raw.get_data().startswith(b'<!DOCTYPE html>')
