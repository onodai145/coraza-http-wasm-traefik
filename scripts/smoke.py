#!/usr/bin/env python3
"""Check real CRS against a running local Compose example (no configuration changes)."""
import base64
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

base = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:18080'
engine = sys.argv[2] if len(sys.argv) > 2 else 'On'

def status(path, data=None):
    request = urllib.request.Request(base + path, data=data, headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code

for attempt in range(30):
    try:
        if status('/anything') == 200:
            break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(1)
else:
    raise SystemExit('local Traefik did not become ready')

leak = base64.b64encode(b'You have an error in your SQL syntax; check the manual that corresponds to your MySQL server version').decode()
for name, path, data, attack in [
    ('normal', '/anything?q=hello', None, False),
    ('sqli', '/anything?' + urllib.parse.urlencode({'q': "1' OR '1'='1' --"}), None, True),
    ('xss', '/anything?' + urllib.parse.urlencode({'q': '<script>alert(1)</script>'}), None, True),
    ('request_body', '/anything', urllib.parse.urlencode({'q': '<script>alert(1)</script>'}).encode(), True),
    ('response_body', '/base64/' + urllib.parse.quote(leak, safe=''), None, True),
]:
    actual = status(path, data)
    expected = 403 if attack and engine == 'On' else 200
    print(f'{engine} {name}: {actual} (expected {expected})')
    if actual != expected:
        raise SystemExit(1)

# A 200 alone does not prove that body inspection preserved the input.
# Verify what the real backend received, including bodies larger than the SDK buffer.
import json
for size in (256, 4096, 16384, 1024 * 1024):
    payload = 'a' * size
    data = urllib.parse.urlencode({'q': payload}).encode()
    request = urllib.request.Request(base + '/anything', data=data, headers={
        'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    actual = result.get('form', {}).get('q')
    if actual != [payload]:
        raise SystemExit(f'{engine} body preservation failed for {size} bytes')
    print(f'{engine} body_preservation_{size}: OK')
