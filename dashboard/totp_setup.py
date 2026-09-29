#!/usr/bin/env python3
"""Dashboard MFA setup — authenticator-app (TOTP) secret for the login page.

    venv/bin/python dashboard/totp_setup.py            # create secret (if none), show QR
    venv/bin/python dashboard/totp_setup.py --rotate   # new secret: old phone entries stop working
    venv/bin/python dashboard/totp_setup.py --check 123456   # does the phone agree?

The secret lives in .env as DASHBOARD_TOTP_SECRET. The QR is written to a temp PNG and
opened on THIS Mac's screen only — it is never served by the dashboard, so the setup
code cannot be fetched over the public URL. Restart the dashboard after create/rotate.
"""
import argparse, base64, os, re, secrets, subprocess, sys, tempfile, time
from urllib.parse import quote

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(BASE, '.env')
KEY = 'DASHBOARD_TOTP_SECRET'
ISSUER, ACCOUNT = 'TriVega', 'dashboard'


def read_secret():
    for line in open(ENV):
        if line.startswith(KEY + '='):
            return line.split('=', 1)[1].strip()
    return ''


def write_secret(secret):
    lines = [l for l in open(ENV) if not l.startswith(KEY + '=')]
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    lines.append(f'{KEY}={secret}\n')
    tmp = ENV + '.tmp'
    with open(tmp, 'w') as f:
        f.writelines(lines)
    os.chmod(tmp, os.stat(ENV).st_mode & 0o777)
    os.replace(tmp, ENV)


def show_qr(secret):
    import segno
    uri = (f'otpauth://totp/{quote(ISSUER)}:{quote(ACCOUNT)}'
           f'?secret={secret}&issuer={quote(ISSUER)}&digits=6&period=30')
    path = os.path.join(tempfile.gettempdir(), 'trivega_totp_qr.png')
    segno.make(uri, error='m').save(path, scale=10, border=4)
    subprocess.run(['open', path])
    print(f'QR opened on this Mac: {path}')
    print('Scan it in Google Authenticator (+ -> Scan a QR code). Delete the PNG afterwards:')
    print(f'    rm {path}')
    print(f'Manual entry instead: account "{ISSUER}", key {" ".join(re.findall(".{1,4}", secret))}, time-based.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rotate', action='store_true')
    ap.add_argument('--check', metavar='CODE')
    a = ap.parse_args()

    if a.check:
        secret = read_secret()
        if not secret:
            sys.exit('no secret yet — run without --check first')
        from totp import totp_code
        now = int(time.time()) // 30
        ok = any(totp_code(secret, c) == a.check.strip() for c in (now - 1, now, now + 1))
        print('MATCH — phone and dashboard agree' if ok else 'NO MATCH — check the phone clock / re-scan')
        sys.exit(0 if ok else 1)

    secret = read_secret()
    if secret and not a.rotate:
        print('Secret already set (use --rotate for a new one). Re-showing its QR.')
    else:
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
        write_secret(secret)
        print('New secret written to .env. Restart the dashboard:')
        print('    launchctl kickstart -k gui/$(id -u)/com.sushil.trading.dashboard')
    show_qr(secret)


if __name__ == '__main__':
    main()
