#!/usr/bin/env python3
"""UORM (com.uorm.app) client library — Supabase backend, no app needed.

Everything was recovered from the APK (assets/flutter_assets/.env + libapp.so
strings): the backend is Supabase and every game action is a Postgres RPC.

Config lives in config.json next to this file (supabase url + anon key, both
public/anonymous by design — the APK ships them).
"""
import base64
import imaplib
import email as _email
import json
import os
import re
import time
from email.header import decode_header

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
SUPA = CFG["supabase_url"]
ANON = CFG["anon_key"]
GMAIL_CREDS = CFG["gmail_creds"]
ACCOUNTS = os.path.join(HERE, "accounts.json")


# ---------------------------------------------------------------- accounts
LOCK = os.path.join(HERE, ".accounts.lock")


def _flock():
    """Serialise accounts.json writers: the dashboard and the runner both touch it,
    and a lost update breaks the (rotating) refresh-token chain."""
    import fcntl
    f = open(LOCK, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def load_accounts():
    return json.load(open(ACCOUNTS)) if os.path.exists(ACCOUNTS) else {}


def save_accounts(d):
    f = _flock()
    try:
        tmp = ACCOUNTS + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1)
        os.replace(tmp, ACCOUNTS)
        os.chmod(ACCOUNTS, 0o600)
    finally:
        f.close()


def update_account(label, acc):
    """Persist ONE account's tokens without clobbering the others (locked read-modify-write)."""
    f = _flock()
    try:
        cur = json.load(open(ACCOUNTS)) if os.path.exists(ACCOUNTS) else {}
        cur[label] = acc
        tmp = ACCOUNTS + ".tmp"
        json.dump(cur, open(tmp, "w"), indent=1)
        os.replace(tmp, ACCOUNTS)
        os.chmod(ACCOUNTS, 0o600)
    finally:
        f.close()


def get_setting(key, default=None):
    try:
        return json.load(open(os.path.join(HERE, "config.json"))).get(key, default)
    except Exception:  # noqa: BLE001
        return default


def set_setting(key, value):
    f = _flock()
    try:
        path = os.path.join(HERE, "config.json")
        cfg = json.load(open(path)) if os.path.exists(path) else {}
        cfg[key] = value
        json.dump(cfg, open(path, "w"), indent=1)
    finally:
        f.close()


def anon_headers():
    return {"apikey": ANON, "Authorization": "Bearer " + ANON, "Content-Type": "application/json"}


def headers(acc):
    return {"apikey": ANON, "Authorization": "Bearer " + acc["access_token"], "Content-Type": "application/json"}


# ---------------------------------------------------------------- auth
def imap_otp(alias_email, since_ts, timeout=180, subject_hint="code"):
    """Poll the shared Gmail box for the UORM verification code sent to alias."""
    c = json.load(open(GMAIL_CREDS))
    deadline = time.time() + timeout
    seen_any = False
    while time.time() < deadline:
        try:
            M = imaplib.IMAP4_SSL(c["imap_host"])
            M.login(c["email"], c["app_password"])
            M.select("INBOX")
            typ, data = M.search(None, f'(TO "{alias_email}")')
            ids = data[0].split()
            for i in reversed(ids[-5:]):
                typ, d = M.fetch(i, "(RFC822)")
                msg = _email.message_from_bytes(d[0][1])
                ts = _email.utils.parsedate_to_datetime(msg.get("Date")).timestamp() if msg.get("Date") else 0
                if ts and ts < since_ts - 30:
                    continue
                body = ""
                for part in (msg.walk() if msg.is_multipart() else [msg]):
                    if part.get_content_type() in ("text/plain", "text/html"):
                        body += part.get_payload(decode=True).decode(errors="ignore")
                txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
                codes = re.findall(r"\b(\d{6})\b", txt)
                if codes:
                    M.logout()
                    return codes[0]
            M.logout()
            seen_any = True
        except Exception:
            pass
        time.sleep(8)
    return None


def signup(alias_local, password=None, label=None):
    """Create an account: signup -> OTP from Gmail -> verify. Stores tokens."""
    gmail = json.load(open(GMAIL_CREDS))["email"]
    base, domain = gmail.split("@")
    alias = f"{base}+{alias_local}@{domain}"
    pw = password or ("Uo" + base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=") + "!7")
    t0 = time.time()
    r = requests.post(SUPA + "/auth/v1/signup", headers=anon_headers(),
                      json={"email": alias, "password": pw, "data": {}}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"signup failed {r.status_code}: {r.text[:200]}")
    uid = r.json().get("id")
    otp = imap_otp(alias, t0)
    if not otp:
        raise RuntimeError("otp not received in time")
    r2 = requests.post(SUPA + "/auth/v1/verify", headers=anon_headers(),
                       json={"type": "signup", "email": alias, "token": otp}, timeout=30)
    if r2.status_code != 200 or not r2.json().get("access_token"):
        raise RuntimeError(f"verify failed {r2.status_code}: {r2.text[:200]}")
    tok = r2.json()
    acc = {"label": label or alias_local, "email": alias, "password": pw, "user_id": uid,
           "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
           "created": time.time()}
    accs = load_accounts()
    accs[acc["label"]] = acc
    save_accounts(accs)
    return acc


def _retry(fn, attempts=4, backoff=(1, 3, 8, 15)):
    """Run fn(), retrying transient network failures (Supabase auth has flaky minutes)."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last = e
            if i < attempts - 1:
                time.sleep(backoff[min(i, len(backoff) - 1)])
    raise last


def token_exp(acc):
    """Expiry epoch of the current access token (JWT exp), or None."""
    try:
        p = acc["access_token"].split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("exp")
    except Exception:  # noqa: BLE001
        return None


def refresh(acc, label=None):
    """Rotate the access token. Persisted immediately (a lost update = dead token chain).
    Also picks up a token another process rotated since we loaded the file."""
    if label:
        try:
            disk = (load_accounts().get(label) or {})
        except Exception:  # noqa: BLE001
            disk = {}
        if disk.get("refresh_token") and (disk.get("refreshed") or 0) > (acc.get("refreshed") or 0):
            acc.update({k: disk[k] for k in ("access_token", "refresh_token", "refreshed") if k in disk})
    r = _retry(lambda: requests.post(SUPA + "/auth/v1/token?grant_type=refresh_token",
                                     headers=anon_headers(), json={"refresh_token": acc["refresh_token"]},
                                     timeout=20))
    if r.status_code == 200:
        d = r.json()
        acc["access_token"] = d["access_token"]
        acc["refresh_token"] = d.get("refresh_token", acc["refresh_token"])
        acc["refreshed"] = time.time()
        if label:
            update_account(label, acc)
        return acc
    raise RuntimeError(f"refresh {r.status_code}: {r.text[:120]}")


def otp_login(acc, label=None):
    """Last-resort recovery when the refresh token chain is broken: OTP by e-mail."""
    t0 = time.time()
    r = _retry(lambda: requests.post(SUPA + "/auth/v1/otp", headers=anon_headers(),
                                     json={"email": acc["email"], "create_user": True}, timeout=25))
    if r.status_code not in (200, 201):
        raise RuntimeError(f"otp send {r.status_code}: {r.text[:120]}")
    code = imap_otp(acc["email"], t0 - 30, timeout=240)
    if not code:
        raise RuntimeError("otp tidak masuk (kuota mailer?)")
    last = None
    for typ in ("email", "magiclink", "signup"):
        rr = requests.post(SUPA + "/auth/v1/verify", headers=anon_headers(),
                           json={"type": typ, "email": acc["email"], "token": code}, timeout=30)
        if rr.status_code == 200 and rr.json().get("access_token"):
            d = rr.json()
            acc["access_token"] = d["access_token"]
            acc["refresh_token"] = d.get("refresh_token") or acc.get("refresh_token")
            acc["refreshed"] = time.time()
            if label:
                update_account(label, acc)
            return acc
        last = f"{typ}:{rr.status_code} {rr.text[:80]}"
    raise RuntimeError(f"otp verify gagal ({last})")


def signin(acc):
    """Password sign-in (fallback when the refresh token is gone)."""
    r = requests.post(SUPA + "/auth/v1/token?grant_type=password", headers=anon_headers(),
                      json={"email": acc["email"], "password": acc["password"]}, timeout=30)
    if r.status_code == 200:
        d = r.json()
        acc["access_token"] = d["access_token"]
        acc["refresh_token"] = d.get("refresh_token")
    return acc


def ensure_token(acc, label=None):
    """Make sure acc['access_token'] works: proactive refresh → 401 refresh → OTP recovery."""
    probe = lambda: _retry(lambda: requests.get(SUPA + "/rest/v1/profiles?select=id&limit=1",
                                                headers=headers(acc), timeout=20))  # noqa: E731
    exp = token_exp(acc)
    if exp and exp - time.time() < 900:          # renew well before expiry (Supabase auth blips)
        try:
            refresh(acc, label)
        except Exception:  # noqa: BLE001
            pass
    if probe().status_code == 200:
        return acc
    for step in (refresh, otp_login):            # refresh may be dead → fall back to an e-mail OTP
        try:
            step(acc, label)
        except Exception:  # noqa: BLE001
            continue
        if probe().status_code == 200:
            return acc
    return acc


# ---------------------------------------------------------------- game RPCs
def rpc(acc, name, payload=None):
    try:
        r = _retry(lambda: requests.post(f"{SUPA}/rest/v1/rpc/{name}", headers=headers(acc),
                                         json=payload or {}, timeout=25))
    except Exception as e:  # noqa: BLE001
        return 0, {"_net_error": type(e).__name__}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:200]


def profile(acc):
    r = requests.get(f"{SUPA}/rest/v1/profiles?select=*&id=eq.{acc['user_id']}", headers=headers(acc), timeout=25)
    return (r.json() or [{}])[0] if r.status_code == 200 else {}


def app_config(acc=None):
    """Server-side switches (device.enforce, maintenance, versions, rates…)."""
    h = headers(acc) if acc else anon_headers()
    r = requests.get(f"{SUPA}/rest/v1/app_config?select=key,value,updated_at", headers=h, timeout=25)
    if r.status_code != 200:
        return {}
    return {row["key"]: row["value"] for row in r.json()}


def ensure_device(acc):
    """Bind a stable per-account device hash. UORM's `device.enforce` switch can be
    flipped on by the developers at any time; claiming a hash once keeps the account
    looking like a normal install and is a no-op afterwards."""
    import uuid
    if not acc.get("device_hash"):
        acc["device_hash"] = uuid.uuid4().hex
    return rpc(acc, "device_account_claim", {"p_device_hash": acc["device_hash"]})


def mining_sessions(acc):
    r = requests.get(f"{SUPA}/rest/v1/mining_sessions?select=*&user_id=eq.{acc['user_id']}"
                     "&claimed=eq.false&order=started_at.desc&limit=3", headers=headers(acc), timeout=25)
    return r.json() if r.status_code == 200 else []
