#!/usr/bin/env python3
"""Change a UORM account's email address (Supabase auth) and complete the
confirmation, reading the code/link from the shared Gmail inbox.

Usage: change_email.py <label> <new_email>

Supabase's built-in mailer is rate-limited (over_email_send_rate_limit after a
few sends per hour) — safe to re-run, it exits cleanly on 429.
"""
import json
import os
import re
import sys
import time
import imaplib
import email as _em
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uorm_lib as U  # noqa: E402

label, new_email = sys.argv[1], sys.argv[2]
accs = U.load_accounts()
acc = accs[label]
U.ensure_token(acc, label)
old = acc["email"]
print(f"{label}: {old} -> {new_email}")

r = requests.put(U.SUPA + "/auth/v1/user", headers=U.headers(acc), json={"email": new_email}, timeout=30)
print("PUT /auth/v1/user:", r.status_code, r.text[:200])
if r.status_code == 429:
    print("RATE_LIMITED — retry later")
    raise SystemExit(0)
if r.status_code != 200:
    raise SystemExit(1)
body = r.json()
print("new_email pending:", body.get("new_email"))

t0 = time.time()
code = None
while time.time() - t0 < 180 and not code:
    try:
        c = json.load(open(U.GMAIL_CREDS))
        M = imaplib.IMAP4_SSL(c["imap_host"]); M.login(c["email"], c["app_password"]); M.select("INBOX")
        for who in (new_email, old):
            typ, data = M.search(None, f'(TO "{who}")')
            for i in reversed(data[0].split()[-4:]):
                typ, d = M.fetch(i, "(RFC822)")
                msg = _em.message_from_bytes(d[0][1])
                ts = _em.utils.parsedate_to_datetime(msg.get("Date")).timestamp() if msg.get("Date") else 0
                if ts < t0 - 60:
                    continue
                txt = ""
                for part in (msg.walk() if msg.is_multipart() else [msg]):
                    if part.get_content_type() in ("text/plain", "text/html"):
                        txt += part.get_payload(decode=True).decode(errors="ignore")
                flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))
                m = re.search(r"\b(\d{6})\b", flat)
                if m and "change" in (msg.get("Subject", "") + flat).lower():
                    code = m.group(1)
                    print("got email-change code from", who)
                    break
            if code:
                break
        M.logout()
    except Exception as e:  # noqa: BLE001
        print("imap:", type(e).__name__)
    if not code:
        time.sleep(10)

if code:
    for who in (new_email, old):
        rr = requests.post(U.SUPA + "/auth/v1/verify", headers=U.anon_headers(),
                           json={"type": "email_change", "email": who, "token": code}, timeout=30)
        print(f"verify({who}):", rr.status_code, rr.text[:120])
        if rr.status_code == 200 and rr.json().get("access_token"):
            acc["access_token"] = rr.json()["access_token"]
            acc["refresh_token"] = rr.json().get("refresh_token", acc.get("refresh_token"))
            break

me = requests.get(U.SUPA + "/auth/v1/user", headers=U.headers(acc), timeout=25)
if me.status_code == 200:
    u = me.json()
    print("current email:", u.get("email"), "| pending:", u.get("new_email"))
    if u.get("email") == new_email:
        acc["email"] = new_email
        accs[label] = acc
        U.save_accounts(accs)
        print("✅ EMAIL_CHANGED")
    else:
        print("⚠️ still pending — run again later")
