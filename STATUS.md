# UORM bot — STATUS (parked 2026-10-03, 12:30 UTC)

## Akun
| label | email | coins | status |
|---|---|---|---|
| a1 | rian3124@gmail.com | **249.50** | AKTIF — token sehat, mining jalan, boost x2 aktif |
| a2 | rian3124+a2@gmail.com | (beku) | MATI — refresh chain putus + hook alias server |
| t1 | rian3124+t1@gmail.com | (beku) | MATI — sama |

- a1 ref code: `0E4AA37A` (refs 0 — kredit dari akun alias ikut dibersihin server mereka)
- Room a1: `room_6614b9bc327c4db48f9e5e648be897a1` (`rizvan-crew`, entry cost 0)
- Sesi mining a1: `5949be5e…` mulai 2026-10-02 16:42:39 UTC → **selesai 2026-10-03 16:42 UTC (23:42 WIB)** → claim otomatis.

## Yang jalan sendiri
- `screen uorm` (detached): dashboard + cycle routine tiap 3 jam (boost 2x/hari, room, misi, box, check-in, mining restart/claim)
- cron `uorm-claim-check` (one-shot, 2026-10-04 01:08 WIB) → lapor saldo sebelum→sesudah + payout mining pertama
- cron `uorm-daily` → PAUSED (fallback; runner utama = screen)
- cron `kaleido-daily` → aktif 08:05 WIB (proyek lain)

## Earning paths — terverifikasi
- misi sosmed 17.5/misi · `once_room` **+30** · `daily_boost` +0.75 · check-in +3 (day 2) · box 4h cooldown · boost x2 rate (1.37 → 3.07/h) · referral tiers 3:+150 / 10:+600 / 25:+1800 / 50:+4000

## Menunggu
1. **Payout mining pertama** (claim 16:42 UTC) — bukti terakhir sebelum gas multi.
2. Email asli terpisah (bukan `+tag`, bukan trik dot) per akun tambahan → daftar pakai ref a1.

## Catatan teknis penting
- Jangan jalankan dua proses yang refresh token bersamaan; token di-persist langsung + `refresh()` baca ulang token terbaru dari disk.
- Akun alias **tidak bisa** login ulang (hook server) — jangan buang kuota email buat OTP/recover ke alamat `+tag`.
- Script: `uorm_lib.py`, `uorm_daily.py` (routine), `uorm_dashboard.py`, `uorm.sh`, `new_account.py`, `change_email.py`. Config: `config.json` (+setting `room_id`), akun: `accounts.json`.
