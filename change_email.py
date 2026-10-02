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


def fresh_codes(since):
    """address -> 6-digit code, from messages newer than `since`."""
    out = {}
    try:
        c = json.load(open(U.GMAIL_CREDS))
        M = imaplib.IMAP4_SSL(c["imap_host"])
        M.login(c["email"], c["app_password"])
        M.select("INBOX")
        for who in (new_email, old):
            typ, data = M.search(None, f'(TO "{who}")')
            for i in reversed(data[0].split()[-6:]):
                typ, d = M.fetch(i, "(RFC822)")
                msg = _em.message_from_bytes(d[0][1])
                try:
                    ts = _em.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
                except Exception:
                    ts = 0
                if ts < since:
                    continue
                txt = ""
                for part in (msg.walk() if msg.is_multipart() else [msg]):
                    if part.get_content_type() in ("text/plain", "text/html"):
                        txt += part.get_payload(decode=True).decode(errors="ignore")
                flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))
                m = re.search(r"\b(\d{6})\b", flat)
                if m:
                    out.setdefault(who, m.group(1))
        M.logout()
    except Exception as e:  # noqa: BLE001
        print("imap:", type(e).__name__, e)
    return out


# Supabase "secure email change": one mail per address, a different 6-digit token each.
# Observed behaviour of this project (probed 2026-10-02):
#   verify(email=OLD, token=<code mailed to OLD>) -> 200 "Confirmation link accepted…"
#   verify(email=NEW, token=<code mailed to NEW>) -> 403 otp_expired
# So the OLD address is the lookup key for BOTH sides; the second side needs the token
# that was mailed to the NEW address. Try that first, then fall back to the other combos.
def fetch_codes(since):
    """(code mailed to NEW address, code mailed to OLD address) — exact To-header match."""
    cn = co = None
    try:
        c = json.load(open(U.GMAIL_CREDS))
        M = imaplib.IMAP4_SSL(c["imap_host"])
        M.login(c["email"], c["app_password"])
        M.select("INBOX")
        typ, data = M.search(None, "ALL")
        for i in reversed(data[0].split()[-15:]):
            typ, d = M.fetch(i, "(RFC822)")
            msg = _em.message_from_bytes(d[0][1])
            to = _em.utils.parseaddr(msg.get("To", ""))[1].lower()
            if to not in (new_email, old):
                continue
            if "new uorm" not in (msg.get("Subject") or "").lower():
                continue
            try:
                ts = _em.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
            except Exception:
                ts = 0
            if ts < since:
                continue
            txt = ""
            for part in (msg.walk() if msg.is_multipart() else [msg]):
                if part.get_content_type() in ("text/plain", "text/html"):
                    txt += part.get_payload(decode=True).decode(errors="ignore")
            m = re.search(r"\b(\d{6})\b", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt)))
            if not m:
                continue
            if to == new_email and not cn:
                cn = m.group(1)
            if to == old and not co:
                co = m.group(1)
        M.logout()
    except Exception as e:  # noqa: BLE001
        print("imap:", type(e).__name__, e)
    return cn, co


def verify(email_param, token):
    rr = requests.post(U.SUPA + "/auth/v1/verify", headers=U.anon_headers(),
                       json={"type": "email_change", "email": email_param, "token": token}, timeout=30)
    print(f"  verify({email_param.split('@')[0]}, {token}) -> {rr.status_code} {rr.text[:110]}")
    return rr


def current_email():
    rr = requests.get(U.SUPA + "/auth/v1/user", headers=U.headers(acc), timeout=25)
    return rr.json().get("email") if rr.status_code == 200 else None


cn = co = None
deadline = time.time() + 240
while time.time() < deadline and not (cn and co):
    a, b = fetch_codes(t0 - 120)
    cn, co = cn or a, co or b
    if cn and co:
        break
    if time.time() < deadline - 20:
        print(f"  menunggu kode… baru={cn} lama={co}", flush=True)
        time.sleep(20)
print("kode: alamat-baru-mailbox =", cn, "| alamat-lama-mailbox =", co)

for email_param, tok, tag in ((old, co, "old side"), (old, cn, "new side via OLD param"),
                              (new_email, cn, "new side via NEW param")):
    if current_email() == new_email or not tok:
        continue
    verify(email_param, tok)

final = current_email()
print("current email:", final, "| pending:", new_email)
if final == new_email:
    acc["email"] = new_email
    accs[label] = acc
    U.save_accounts(accs)
    print("✅ EMAIL_CHANGED")
else:
    print("⚠️ masih pending — jalankan lagi nanti")

