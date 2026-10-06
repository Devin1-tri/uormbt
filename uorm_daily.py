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
NO_RETRY = "--no-retry" in sys.argv          # skip the second pass for failed accounts
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE_RATE = 1.3          # mining.base_rate_per_hour from app_config
CONFIG_WATCH = ["device.enforce", "device.block_other_login", "app.maintenance_mode",
                "app.latest_version", "mining.base_rate_per_hour", "luckybox.cooldown_hours"]


def log(*a):
    if not QUIET:
        print(*a, flush=True)


def run_account(label, acc):
    row = {"label": label}
    try:
        U.ensure_token(acc, label)
        exp = U.token_exp(acc)
        row["token"] = f"ok ({int((exp - time.time()) / 60)}m sisa)" if exp else "ok"
    except Exception as e:  # noqa: BLE001
        row["token"] = f"ERR {type(e).__name__}"
        row["error"] = f"auth gagal: {type(e).__name__}"
    try:
        c, r = U.ensure_device(acc)
        row["device"] = "bound" if c == 200 else f"{c} {json.dumps(r)[:60]}"
    except Exception as e:  # noqa: BLE001
        row["device"] = f"ERR {type(e).__name__}"
    code, resp = U.rpc(acc, "ping_daily_open")
    row["ping"] = resp if code == 200 else f"ERR {code}"

    # --- boost: rewarded "watch boost" (2/day) doubles the mining rate for an hour AND
    #     completes daily_boost / weekly_boosts. Must run BEFORE the mission claims.
    c, bs = U.rpc(acc, "get_boost_status")
    row["boost"] = f"{bs.get('used')}/{bs.get('limit')} terpakai" if c == 200 else f"ERR {c}"
    if c == 200 and (bs.get("used") or 0) < (bs.get("limit") or 0):
        sess = U.mining_sessions(acc)
        # A cold cycle (fresh start / the cycle right after a payout) has no live session, so
        # the boost silently no-op'd and both 2× boosts went unused every day (digest read
        # "0/2 terpakai"). Make sure a session exists first — the mining block below then sees
        # it running and won't start a second one.
        if not sess:
            U.rpc(acc, "start_mining_session", {"p_rate_per_hour": BASE_RATE})
            sess = U.mining_sessions(acc)
        if sess and sess[0].get("rate_per_hour"):
            rate = float(sess[0]["rate_per_hour"])
            # Don't burn a second boost while one is still running: the last segment holds the
            # doubled rate for 3600 s. Without this, two runs minutes apart (a manual `once`
            # right after a cycle, or a double-triggered cycle) consume BOTH daily boosts
            # inside a single 1-hour window — measured 2026-10-06.
            segs = sess[0].get("segments") or []
            last = segs[-1] if segs else {}
            active = (last.get("ratePerHour") or 0) > rate * 1.01 and \
                (time.time() - (last.get("startedAt") or 0) / 1000.0) < 3600
            if active:
                row["boost"] = f"sudah aktif x2 → {last.get('ratePerHour')}/h (skip)"
            else:
                bc, br = U.rpc(acc, "apply_mining_boost",
                               {"p_boosted_rate": round(rate * 2, 6), "p_duration_ms": 3600000,
                                "p_multiplier": 2, "p_session_id": sess[0]["id"]})
                if bc == 200:
                    seg = ((br or {}).get("segments") or [{}])[-1]
                    row["boost"] = f"aktif x2 → {seg.get('ratePerHour', round(rate * 2, 3))}/h (1 jam)"
                else:
                    row["boost"] = f"gagal {json.dumps(br)[:60]}"

    # --- room: room activity unlocks extra rate; once_room mission pays 30
    p0 = U.profile(acc)
    if not p0.get("room_id"):
        room = U.get_setting("room_id")
        if not room:
            rc, rr = U.rpc(acc, "create_room", {"p_name": "rizvan-crew"})
            room = (rr or {}).get("room_id") if rc == 200 else None
            if room:
                U.set_setting("room_id", room)
        if room:
            sc, sr = U.rpc(acc, "switch_room", {"new_room_id": room, "p_join_pin": None})
            row["room"] = f"join {room[-6:]}" if sc == 200 else f"gagal {json.dumps(sr)[:50]}"

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
        row["lucky_box"] = f"cooldown {(st or {}).get('remaining_ms', '?')}ms" if code == 200 else f"ERR {code}"

    # --- missions: fully catalog-driven, so ANY mission the developers add later
    #     (new id, new type) is picked up automatically — nothing is hardcoded.
    claimed = []
    code, mis = U.rpc(acc, "get_missions")
    catalog = {m["id"]: m for m in (mis.get("catalog") or [])} if code == 200 else {}
    progress = {p["mission_id"]: p for p in (mis.get("progress") or [])} if code == 200 else {}

    def claim(mid):
        c, r = U.rpc(acc, "claim_mission", {"p_mission_id": mid})
        if c == 200:
            claimed.append(f"{mid}+{r.get('amount')}")
            return True
        return False

    def is_claimed(mid):
        return bool((progress.get(mid) or {}).get("claimed"))

    # 1) anything the server already marks completed but unclaimed
    for mid, pr in progress.items():
        if pr.get("completed") and not pr.get("claimed"):
            claim(mid)
    # 2) every unclaimed mission in the catalog: honour-based mark first (a no-op
    #    for missions that verify themselves), then claim; failures are harmless
    for mid in catalog:
        if not is_claimed(mid):
            U.rpc(acc, "mark_social_mission", {"p_mission_id": mid})
            claim(mid)
    # 3) bundles (whichever type is ready)
    for btype in ("daily", "weekly"):
        c, r = U.rpc(acc, "claim_mission_bundle", {"p_type": btype})
        if c == 200:
            claimed.append(f"{btype}_bundle+{(r or {}).get('amount')}")
    row["missions"] = claimed or "already"
    row["social"] = claimed or "already"
    row["daily"] = claimed or "-"

    # --- mining: restart finished sessions, keep one running
    import datetime
    sessions = U.mining_sessions(acc)
    now = time.time()
    action, warn = [], None
    running = False
    for s in sessions:
        t0 = datetime.datetime.fromisoformat(s["started_at"].replace("Z", "+00:00")).timestamp()
        dur = (s.get("session_duration_ms") or 86400000) / 1000.0
        if now - t0 >= dur:
            c, r = U.rpc(acc, "claim_mining_session", {"p_session_id": s["id"]})
            action.append(f"claimed {s['id'][:8]} -> {json.dumps(r)[:80]}")
            if c != 200:
                over = (now - t0 - dur) / 3600.0
                warn = f"session {s['id'][:8]} lewat {over:.1f} jam tapi claim gagal: {json.dumps(r)[:80]}"
                # a completed-but-unclaimed session can block new ones: try anyway
                c2, r2 = U.rpc(acc, "start_mining_session", {"p_rate_per_hour": BASE_RATE})
                action.append(f"retry start -> {json.dumps(r2)[:80]}")
        else:
            running = True
            c, r = U.rpc(acc, "refresh_mining_session_rate")
            action.append(f"running {s['id'][:8]} rate={(r or {}).get('rate_per_hour')}")
    # Start whenever nothing is RUNNING. The old guard was `if not sessions:` — on the very
    # cycle that CLAIMS a payout the fetched list still holds that (just-claimed) row, so the
    # guard never fired and mining sat idle until the NEXT cycle: up to 3 h of zero coins per
    # 24 h payout. Operator caught it on the dashboard 2026-10-06 ("TIDAK ADA sesi aktif").
    if not running:
        r = None
        for attempt in (1, 2, 3):
            c, r = U.rpc(acc, "start_mining_session", {"p_rate_per_hour": BASE_RATE})
            if c == 200:
                action.append(f"started {json.dumps(r)[:110]}")
                break
            action.append(f"start try{attempt} gagal ({c}) -> {json.dumps(r)[:80]}")
            if attempt < 3:
                time.sleep(3 * attempt)
        else:
            warn = f"gagal start mining: {json.dumps(r)[:80]}"
    row["mining"] = action
    if warn:
        row["warning"] = warn

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
        if r.get("disabled"):
            out.append(f"• {r['label']}: ⏸ MATI — {r['disabled']}")
            continue
        if r.get("error"):
            out.append(f"❌ {r['label']}: {r['error']}")
            continue
        out.append(f"• {r['label']} ({r.get('ref_code')}): {r.get('coins')} coins · {r.get('rank')} · "
                   f"streak {r.get('streak')} · refs {r.get('refs')}")
        if r.get("warning"):
            out.append(f"   ⚠️ {r['warning']}")
        if r.get("token"):
            out.append(f"   🔑 token {r['token']}" + (f"   ⚠️ {r['error']}" if r.get("error") else ""))
        if r.get("device") and r["device"] != "bound":
            out.append(f"   ⚠️ device binding: {r['device']}")
        out.append(f"   bonus {r.get('daily_bonus')} · box {r.get('lucky_box')} · missions {r.get('missions')}")
        if r.get("boost"):
            out.append(f"   ⚡ boost {r['boost']}")
        if r.get("room"):
            out.append(f"   🏠 room {r['room']}")
        out.append(f"   mining: {r.get('mining')}")
    return "\n".join(out)


def config_watch(acc=None):
    """Alert when the developers flip a server-side switch (anti-bot, rates, versions).
    Needs an authenticated account: app_config is not readable with the anon key."""
    try:
        cfg = U.app_config(acc)
    except Exception:  # noqa: BLE001
        return []
    if not cfg:
        return []
    path = os.path.join(HERE, "state.json")
    try:
        st = json.load(open(path))
    except Exception:
        st = {}
    prev = st.get("config") or {}
    changed = [f"{k}: {prev.get(k)} → {cfg.get(k)}" for k in CONFIG_WATCH
               if k in cfg and k in prev and prev.get(k) != cfg.get(k)]
    st["config"] = {k: cfg.get(k) for k in CONFIG_WATCH if k in cfg}
    try:
        json.dump(st, open(path, "w"), indent=1)
    except Exception:  # noqa: BLE001
        pass
    return changed


def main(labels=None):
    accs = U.load_accounts()
    labels = labels or list(accs.keys())
    rows = []
    for lb in labels:
        acc = accs.get(lb)
        if not acc:
            rows.append({"label": lb, "error": "unknown account label"})
            continue
        if acc.get("disabled"):
            rows.append({"label": lb, "disabled": acc.get("disabled_reason", "-")})
            continue
        try:
            rows.append(run_account(lb, acc))
        except Exception as e:  # noqa: BLE001
            rows.append({"label": lb, "error": f"{type(e).__name__}: {e}"})
    for _lb in labels:
        if _lb in accs:
            U.update_account(_lb, accs[_lb])   # per-account write, no clobber
    failed = [r["label"] for r in rows if r.get("error") and r["label"] in accs]
    if failed and not NO_RETRY:
        print(f"↻ {len(failed)} akun gagal ({', '.join(failed)}) — coba lagi 4 menit lagi…", flush=True)
        time.sleep(240)
        for i, r in enumerate(rows):
            if r.get("error") and r["label"] in failed:
                try:
                    rows[i] = run_account(r["label"], accs[r["label"]])
                except Exception as e:  # noqa: BLE001
                    rows[i] = {"label": r["label"], "error": f"{type(e).__name__}"}
        for _lb in labels:
            if _lb in accs:
                U.update_account(_lb, accs[_lb])
    text = digest(rows)
    first = next((accs[l] for l in labels if l in accs), None)
    changes = config_watch(first)
    if changes:
        text += "\n⚙️ Perubahan config server:\n  " + "\n  ".join(changes)
    print(text)
    return rows


if __name__ == "__main__":
    main(args or None)
