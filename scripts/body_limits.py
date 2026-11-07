#!/usr/bin/env python3
"""Exercise the service's explicit Traefik body limit, before the WASM WAF."""
import argparse
import socket
import urllib.parse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base', default='http://localhost:18080')
parser.add_argument('--limit', type=int, required=True, help='Configured maxRequestBodyBytes')
parser.add_argument('--mode', default='On', choices=['On', 'DetectionOnly'])
parser.add_argument('--check-order', action='store_true', help='Use E2E /admin deny rule to prove limiter runs first')
args = parser.parse_args()
if args.limit <= 0:
    parser.error('--limit must be finite and positive')
url = urllib.parse.urlsplit(args.base)
if url.scheme != 'http':
    parser.error('this local test uses plain HTTP')


def head(reader):
    line = reader.readline(65537)
    if not line:
        raise AssertionError('connection closed without an HTTP response')
    status = int(line.split()[1])
    length = 0
    while True:
        line = reader.readline(65537)
        length += len(line)
        if not line or length > 65536:
            raise AssertionError('invalid HTTP response headers')
        if line == b'\r\n':
            return status


def post(size, chunked):
    with socket.create_connection((url.hostname, url.port or 80), timeout=60) as conn:
        conn.settimeout(60)
        headers = [
            'POST /admin HTTP/1.1', f'Host: {url.netloc}',
            'User-Agent: Mozilla/5.0', 'Content-Type: application/octet-stream',
            'coraza-e2e: test', 'Connection: close', 'Expect: 100-continue',
        ]
        headers.append('Transfer-Encoding: chunked' if chunked else f'Content-Length: {size}')
        conn.sendall(('\r\n'.join(headers) + '\r\n\r\n').encode())
        with conn.makefile('rb') as reader:
            status = head(reader)
            if status != 100:
                return status
            remaining = size
            block = b'x' * 65536
            try:
                while remaining:
                    data = block[:min(len(block), remaining)]
                    if chunked:
                        conn.sendall(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
                    else:
                        conn.sendall(data)
                    remaining -= len(data)
                if chunked:
                    conn.sendall(b'0\r\n\r\n')
            except OSError:
                # A limiter may close its read side after sending HTTP 413.
                # The response must still be received and asserted below.
                pass
            status = head(reader)
            while status == 100:
                status = head(reader)
            return status


for chunked in (False, True):
    label = 'chunked' if chunked else 'Content-Length'
    actual = post(args.limit + 1, chunked)
    print(f'{args.mode} {label} over limit: {actual} (expected 413)')
    if actual != 413:
        raise SystemExit(1)

if args.check_order:
    # Exactly at the limit passes buffering and reaches the phase-1 /admin deny.
    # Above the limit must be 413, not the WAF's 403. This also proves ordering.
    for chunked in (False, True):
        label = 'chunked' if chunked else 'Content-Length'
        actual = post(args.limit, chunked)
        print(f'{args.mode} {label} at limit reaches WAF: {actual} (expected 403)')
        if actual != 403:
            raise SystemExit(1)
