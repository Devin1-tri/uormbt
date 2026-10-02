#!/usr/bin/env python3
"""UORM daily routine — check-in, mining session, lucky box, social + daily claims.

Usage:
    uorm_daily.py                 # all accounts
    uorm_daily.py a1 a2           # selected labels
    uorm_daily.py --quiet         # only the digest line (cron delivery)

Idempotent: safe to run every few hours. Nothing raises out of an account.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uorm_lib as U  # noqa: E402

QUIET = "--quiet" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE_RATE = 1.3          # mining.base_rate_per_hour from app_config
SOCIAL = ["social_x", "social_telegram", "social_youtube", "twitter_repost"]


def log(*a):
    if not QUIET:
        print(*a, flush=True)


def run_account(label, acc):
    row = {"label": label}
    U.ensure_token(acc, label)
    code, resp = U.rpc(acc, "ping_daily_open")
    row["ping"] = resp if code == 200 else f"ERR {code}"

    # --- daily bonus
    code, st = U.rpc(acc, "get_daily_bonus_status")
    if code == 200 and not st.get("claimed_today"):
        c, r = U.rpc(acc, "claim_daily_bonus")
        row["daily_bonus"] = r.get("amount") if isinstance(r, dict) else r
    else:
        row["daily_bonus"] = "already"

    # --- lucky box
    code, st = U.rpc(acc, "get_lucky_box_status")
    if code == 200 and st.get("can_open"):
        c, r = U.rpc(acc, "open_lucky_box")
        row["lucky_box"] = (r or {}).get("amount", r) if isinstance(r, dict) else r
    else:
        row["lucky_box"] = f"cooldown {(st or {}).get('remaining_ms', '?')}" if code == 200 else f"ERR {code}"

    # --- social missions (one-time, 17.5 each)
    claimed = []
    code, mis = U.rpc(acc, "get_missions")
    state = {}
    if code == 200:
        for m in mis.get("catalog", []):
            state[m["id"]] = m
    for mid in SOCIAL:
        c, r = U.rpc(acc, "claim_mission", {"p_mission_id": mid})
        if c == 200:
            claimed.append(f"{mid}+{r.get('amount')}")
        elif isinstance(r, dict) and "not complete" in str(r.get("message", "")):
            U.rpc(acc, "mark_social_mission", {"p_mission_id": mid})
            c2, r2 = U.rpc(acc, "claim_mission", {"p_mission_id": mid})
            if c2 == 200:
                claimed.append(f"{mid}+{r2.get('amount')}")
    row["social"] = claimed or "already"

    # --- daily missions
    daily = []
    for mid in ["daily_open", "daily_start", "daily_boost", "daily_share", "daily_invite"]:
        c, r = U.rpc(acc, "claim_mission", {"p_mission_id": mid})
        if c == 200:
            daily.append(f"{mid}+{r.get('amount')}")
    for btype in ["daily"]:
        c, r = U.rpc(acc, "claim_mission_bundle", {"p_type": btype})
        if c == 200:
            daily.append(f"{btype}_bundle+{r.get('amount')}")
    row["daily"] = daily or "-"

    # --- mining: restart finished sessions, keep one running
    sessions = U.mining_sessions(acc)
    now = time.time()
    action = []
    for s in sessions:
        started = s["started_at"].replace("Z", "+00:00")
        import datetime
        t0 = datetime.datetime.fromisoformat(started).timestamp()
        dur = (s.get("session_duration_ms") or 86400000) / 1000.0
        if now - t0 >= dur:
            c, r = U.rpc(acc, "claim_mining_session", {"p_session_id": s["id"]})
            action.append(f"claimed {s['id'][:8]} -> {json.dumps(r)[:80]}")
        else:
            c, r = U.rpc(acc, "refresh_mining_session_rate")
            action.append(f"running {s['id'][:8]} rate={(r or {}).get('rate_per_hour')}")
    if not sessions:
        c, r = U.rpc(acc, "start_mining_session", {"p_rate_per_hour": BASE_RATE})
        action.append(f"started {json.dumps(r)[:110]}")
    row["mining"] = action

    p = U.profile(acc)
    row["coins"] = p.get("total_coins")
    row["rank"] = p.get("rank_id")
    row["streak"] = p.get("streak_count")
    row["ref_code"] = p.get("referral_code")
    c, refc = U.rpc(acc, "get_referral_count")
    row["refs"] = refc if c == 200 else "?"
    return row


def digest(rows):
    out = ["⛏️ UORM daily · " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())]
    for r in rows:
        if r.get("error"):
            out.append(f"❌ {r['label']}: {r['error']}")
            continue
        out.append(f"• {r['label']} ({r.get('ref_code')}): {r.get('coins')} coins · {r.get('rank')} · "
                   f"streak {r.get('streak')} · refs {r.get('refs')}")
        out.append(f"   bonus {r.get('daily_bonus')} · box {r.get('lucky_box')} · social {r.get('social')} · daily {r.get('daily')}")
        out.append(f"   mining: {r.get('mining')}")
    return "\n".join(out)


def main(labels=None):
    accs = U.load_accounts()
    labels = labels or list(accs.keys())
    rows = []
    for lb in labels:
        acc = accs.get(lb)
        if not acc:
            rows.append({"label": lb, "error": "unknown account label"})
            continue
        try:
            rows.append(run_account(lb, acc))
        except Exception as e:  # noqa: BLE001
            rows.append({"label": lb, "error": f"{type(e).__name__}: {e}"})
    U.save_accounts(accs)
    text = digest(rows)
    print(text)
    return rows


if __name__ == "__main__":
    main(args or None)
