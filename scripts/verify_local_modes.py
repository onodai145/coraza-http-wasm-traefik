#!/usr/bin/env python3
"""Verify the local example in On/DetectionOnly; always restore its config."""
import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base', default='http://localhost:18080')
parser.add_argument('--config', type=Path, default=Path('config-dynamic.yaml'))
parser.add_argument('--limit', type=int, default=13631488, help='Example Traefik maxRequestBodyBytes')
args = parser.parse_args()
if urllib.parse.urlsplit(args.base).hostname not in ('localhost', '127.0.0.1', '::1'):
    parser.error('only a local validation endpoint is allowed')
config = args.config
original = config.read_bytes()
mode = stat.S_IMODE(config.stat().st_mode)
expected = original
scripts = Path(__file__).resolve().parent
if original.count(b'SecRuleEngine On') != 1 or original.count(b'SecAuditEngine Off') != 1:
    parser.error('expected the provided local example with one On/AuditEngine Off directive')


def install(content):
    global expected
    if config.read_bytes() != expected:
        raise RuntimeError('config changed concurrently; refusing to overwrite it')
    with tempfile.NamedTemporaryFile(dir=config.parent, prefix='.coraza-validation-', delete=False) as file:
        temporary = Path(file.name)
        file.write(content)
    try:
        temporary.chmod(mode)
        os.replace(temporary, config)
        expected = content
    finally:
        temporary.unlink(missing_ok=True)


def response_status(path, data=None):
    req = urllib.request.Request(args.base + path, data=data, headers={
        'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def await_status(path, wanted, data=None):
    deadline = time.monotonic() + 60
    last = None
    while time.monotonic() < deadline:
        try:
            last = response_status(path, data)
            if last == wanted:
                return
        except OSError as error:
            last = str(error)
        time.sleep(0.5)
    raise RuntimeError(f'config did not become active: expected {wanted}, got {last}')


def checks(engine):
    subprocess.run([sys.executable, str(scripts / 'smoke.py'), args.base, engine], check=True)
    subprocess.run([sys.executable, str(scripts / 'body_limits.py'), '--base', args.base,
                    '--limit', str(args.limit), '--mode', engine], check=True)


sqli = '/anything?' + urllib.parse.urlencode({'q': "1' OR '1'='1' --"})
large_body = urllib.parse.urlencode({'q': 'a' * 4096}).encode()
try:
    await_status(sqli, 403)
    checks('On')

    detection = original.replace(b'SecRuleEngine On', b'SecRuleEngine DetectionOnly')
    install(detection)
    await_status(sqli, 200)
    checks('DetectionOnly')

    # First observe WAF rejection with a small inspection limit. This proves
    # the limit is active before switching the same config to DetectionOnly.
    small_limit = original.replace(b'SecAuditEngine Off',
        b'SecAuditEngine Off\n            - SecRequestBodyLimit 1024\n            - SecRequestBodyInMemoryLimit 1024\n            - SecRequestBodyLimitAction Reject')
    install(small_limit)
    await_status('/anything', 413, large_body)
    print('On WAF inspection limit 1024: 413', flush=True)
    install(small_limit.replace(b'SecRuleEngine On', b'SecRuleEngine DetectionOnly'))
    await_status('/anything', 200, large_body)
    checks('DetectionOnly')
    print('DetectionOnly above WAF inspection limit preserves the entire body', flush=True)
finally:
    # Do not discard an edit made by a human while this script was running.
    if config.read_bytes() != expected:
        raise RuntimeError('config changed concurrently; original was not restored automatically')
    if expected != original:
        install(original)
        await_status(sqli, 403)
