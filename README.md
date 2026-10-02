# uormbt — UORM (com.uorm.app) automation bot

A small, dependency-light Python bot for the **UORM** mining app.
It talks straight to the app's backend API — **no phone, no emulator, no APK patching needed at runtime**.

```text
check-in bonus · mining session (auto restart) · lucky box · social + daily missions
```

---

## ⚠️ Read first

* Use this **only with accounts you own**. Multi-accounting may violate the app's terms — you accept the risk.
* `accounts.json` and `config.json` hold credentials/tokens: **never commit or share them**.
* The bot is idempotent — running it twice in a row does nothing harmful (the server rejects duplicate claims).

---

## Requirements

| What | Why |
|---|---|
| Python **3.9+** | runs the bot |
| `requests` | the only external library |
| A **Gmail account with an App Password** | UORM signs up with email + a 6-digit code, and the bot reads that code from your inbox over IMAP |

Everything else (`imaplib`, `json`, `time`, …) ships with Python.

---

## Quick start (5 minutes, copy-paste)

### 1. Get the code

```bash
git clone https://github.com/Devin1-tri/uormbt.git
cd uormbt
```

### 2. Install

```bash
python3 -m venv .venv
# Linux / macOS
.venv/bin/pip install -r requirements.txt
# Windows (CMD / PowerShell)
.venv\Scripts\pip.exe install -r requirements.txt
```

### 3. Create a Gmail App Password

UORM emails a **6-digit verification code**, and the bot reads it for you:

1. Google Account → **Security** → enable **2-Step Verification**
2. Open **App passwords**: https://myaccount.google.com/apppasswords
3. Create one (name it `uorm-bot`) and copy the 16-character password

### 4. Create `config.json`

```bash
cp config.example.json config.json
```

Fill it in:

```json
{
  "supabase_url": "https://<project>.supabase.co",
  "anon_key": "<public anon key>",
  "gmail_creds": "./gmail_creds.json"
}
```

…and create `gmail_creds.json` next to it:

```json
{
  "email": "youraddress@gmail.com",
  "app_password": "abcd efgh ijkl mnop",
  "imap_host": "imap.gmail.com"
}
```

> **Where do `supabase_url` / `anon_key` come from?**
> UORM is a Flutter app with a Supabase backend, and it ships both values inside the APK at
> `assets/flutter_assets/.env`. They are the *public anonymous* credentials (not private keys).
> Unzip the APK, open that file, and copy
> `PUBLIC_SUPABASE_URL` → `supabase_url` and `PUBLIC_SUPABASE_ANON_KEY` → `anon_key`.

### 5. Create your first account

```bash
.venv/bin/python new_account.py acc1             # signs up youraddress+acc1@gmail.com
.venv/bin/python new_account.py acc2 <REF_CODE>  # optional: second account, referred by acc1
```

What happens: the bot signs up with the `+alias` address, waits for the code in your Gmail,
verifies it, applies the referral code (if given) and stores the tokens in `accounts.json`.
Your own referral code appears in the daily output (it is the first 8 characters of the user ID, uppercased).

Adding more accounts = repeat step 5 with a new label. One label = one email alias = one account.

### 6. Run it

One pass:

```bash
.venv/bin/python uorm_daily.py
```

Continuous (recommended — one pass every 6 hours):

```bash
screen -S uorm -dm bash -c '.venv/bin/python uorm_loop.py'
screen -r uorm            # attach to watch it — Ctrl-A then D to detach
```

No `screen` on the machine? Use nohup instead:

```bash
nohup .venv/bin/python uorm_loop.py > /dev/null 2>&1 &
```

Want it on a scheduler (cron) instead? Any of these works:

```cron
0 */6 * * * cd /path/to/uormbt && .venv/bin/python uorm_daily.py --quiet
```

---

## What each file does

| File | Purpose |
|---|---|
| `uorm_lib.py` | Auth (sign up, read the code from IMAP, verify, refresh tokens) + thin Supabase RPC client |
| `uorm_daily.py` | The routine: check-in, lucky box, mining keep-alive/restart, mission claims, digest output |
| `new_account.py` | `new_account.py <label> [referral_code]` — create an account |
| `change_email.py` | `change_email.py <label> <new@email>` — move an account to another email |
| `uorm_loop.py` | Daemon: runs the routine every `INTERVAL_H` hours (default 6), logs to `uorm_loop.log` |
| `config.example.json` | Template for `config.json` |

---

## What the routine actually does

For every account stored in `accounts.json`:

1. **Daily check-in** — `ping_daily_open` + `claim_daily_bonus` (the ladder rises 2.25 → 8.75 coins over 7 days).
2. **Lucky box** — opened whenever the 4-hour cooldown has passed (prizes 0.225 / 0.45 / 0.9).
3. **Mining session** — starts a 24-hour session when none is running, refreshes the rate while one runs, and claims + restarts it once the 24 hours are up.
4. **Missions** — claims the four one-time social missions (17.5 coins each) and every daily mission whose requirement is met.
5. Prints a digest line per account: coins, rank, streak, referral count and what was claimed.

The bot never watches ads, so `daily_boost` stays unclaimed — by design.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `over_email_send_rate_limit` | Supabase's built-in mailer only allows a few mails per hour. Wait ~1 hour and re-run. |
| `otp not received in time` | Wrong/expired App Password, IMAP disabled, or the code expired (1 hour). Re-run `new_account.py`. |
| `401 / permission denied` on RPCs | Access token expired; the bot refreshes automatically and falls back to password sign-in. If it persists, run once more. |
| `Unknown account label` | That label is not in `accounts.json` yet — create it with `new_account.py`. |
| Signup returns no email | That address is already registered. Use a fresh `+alias`. |

---

## Security notes

* `.gitignore` excludes `config.json`, `gmail_creds.json`, `accounts.json` and `uorm_loop.log` **on purpose**.
* Anyone holding `accounts.json` can act as your accounts — keep it private.
* A Gmail App Password grants mailbox access; revoke it any time from your Google account.

---

## Notes

Reverse-engineered for personal automation from UORM APK v1.0.6 (Flutter + Supabase). The bot reads game values
(rate, cooldowns, mission list) from the server's public config, so it keeps working when the developers tune numbers.

---

### Ringkasan Bahasa Indonesia

Bot untuk app **UORM** — jalan langsung lewat API (tanpa HP/emulator).
Butuh: Python 3.9+, `requests`, dan **Gmail App Password** (buat baca kode OTP).
Isi `config.json` (URL + anon key Supabase dari dalam APK, di `assets/flutter_assets/.env`), siapkan `gmail_creds.json`, lalu:

```bash
python new_account.py akun1          # daftar akun
python new_account.py akun2 KODE     # akun kedua pakai kode referral akun1
python uorm_daily.py                 # jalankan rutin sekali
python uorm_loop.py                  # daemon tiap 6 jam (saran: di dalam screen)
```

Rutinnya: check-in harian, lucky box, mining 24 jam (auto restart), plus klaim mission social & harian.
