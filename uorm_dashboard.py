#!/usr/bin/env python3
"""UORM live dashboard — the thing you see when you enter the screen session.

Shows every account (coins, rank, streak, referrals, mining rate + time left,
lucky-box timer, check-in, mission progress) plus the daemon state.

    ./uorm.sh start        # foreground: dashboard + routine every INTERVAL_H
    uorm_dashboard.py --once   # print one snapshot and exit

Ctrl+C stops the bot but NOT the screen session (the window runs an interactive
bash, so you land on a prompt and can start it again with ./uorm.sh start).
"""
import datetime
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uorm_lib as U  # noqa: E402
import uorm_daily as D  # noqa: E402

INTERVAL_H = float(os.environ.get("INTERVAL_H", "6"))
REFRESH_S = float(os.environ.get("REFRESH_S", "30"))
STATE = os.path.join(HERE, "state.json")
USE_COLOR = os.environ.get("NO_COLOR", "") == "" and sys.stdout.isatty()

C = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "cyan": "\033[36m",
     "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "gray": "\033[90m"}


def c(txt, color):
    return f"{C[color]}{txt}{C['reset']}" if USE_COLOR else str(txt)


def fit(txt, width):
    """Truncate (then pad) so nothing can push the box border out of line."""
    txt = str(txt)
    return (txt[: width - 1] + "…" if len(txt) > width else txt.ljust(width))


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"cycles": 0, "last_cycle_utc": None, "last_result": None, "last_deltas": {}}


def save_state(st):
    json.dump(st, open(STATE, "w"), indent=1)


def hms(seconds):
    if seconds is None or seconds < 0:
        return "-"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def snapshot(label, acc):
    """Everything the dashboard shows for one account."""
    row = {"label": label, "ok": False}
    try:
        U.ensure_token(acc, label)
        p = U.profile(acc)
        row.update(coins=p.get("total_coins"), rank=p.get("rank_id"), streak=p.get("streak_count"),
                   level=p.get("level"), lifetime=p.get("lifetime_mined"),
                   email=acc.get("email", "").split("@")[0], ref_code=p.get("referral_code"))
        cf, refs = U.rpc(acc, "get_referral_count")
        row["refs"] = refs if cf == 200 else "?"
        sess = U.mining_sessions(acc)
        if sess:
            s = sess[0]
            t0 = datetime.datetime.fromisoformat(s["started_at"].replace("Z", "+00:00")).timestamp()
            dur = (s.get("session_duration_ms") or 86400000) / 1000.0
            row["mining"] = {"rate": s.get("rate_per_hour"), "left": max(0, t0 + dur - time.time()),
                             "mult": s.get("boost_multiplier") or 1,
                             "overdue": round((time.time() - t0 - dur) / 3600.0, 1)}
        else:
            row["mining"] = None
        cb, lb = U.rpc(acc, "get_lucky_box_status")
        row["box"] = (lb or {}).get("remaining_ms", 0) / 1000.0 if cb == 200 else None
        cd, db = U.rpc(acc, "get_daily_bonus_status")
        row["bonus"] = {"day": (db or {}).get("current_day"), "claimed": (db or {}).get("claimed_today"),
                        "amount": (db or {}).get("amount")}
        cm, ms = U.rpc(acc, "get_missions")
        if cm == 200:
            prog = {x["mission_id"]: x for x in (ms.get("progress") or [])}
            done = [k for k, v in prog.items() if v.get("claimed")]
            ready = [k for k, v in prog.items() if v.get("completed") and not v.get("claimed")]
            todo = [k for k, v in prog.items() if not v.get("completed")]
            row["missions"] = {"done": len(done), "total": len(prog), "ready": ready, "todo": todo}
        row["ok"] = True
    except Exception as e:  # noqa: BLE001
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def render(rows, st):
    W = 76
    now = datetime.datetime.now()
    nxt = st.get("next_cycle_ts")
    lines = []
    head = f"  UORM BOT · dashboard        {now:%Y-%m-%d %H:%M:%S} WIB"
    lines.append(c("┌" + "─" * W + "┐", "cyan"))
    lines.append(c("│", "cyan") + head.ljust(W) + c("│", "cyan"))
    lines.append(c("├" + "─" * W + "┤", "cyan"))

    for r in rows:
        if not r.get("ok"):
            lines.append(c("│ ", "cyan") + c(f"{r['label']}: ERROR {r.get('error', 'unknown')}".ljust(W - 2), "red") + c("│", "cyan"))
            continue
        title = f"  {r['label']}  ·  {r['coins']} coins  ·  {str(r['rank']).upper()}  ·  streak {r['streak']}"
        lines.append(c("│ ", "cyan") + c(fit(title, W - 2), "bold") + c("│", "cyan"))
        sub1 = f"     {r.get('email','?')}@gmail.com   refs {r['refs']}   code {r.get('ref_code')}   lvl {r.get('level')}"
        lines.append(c("│ ", "cyan") + c(fit(sub1, W - 2), "gray") + c("│", "cyan"))
        m = r.get("mining")
        if m:
            if m.get("overdue", 0) > 0:
                mk = f"     ⚠ mining sesi lewat {m['overdue']}h — menunggu claim berhasil (rate {m['rate']}/h)"
            else:
                mk = f"     mining {m['rate']}/h x{m['mult']} · sisa {hms(m['left'])}"
        else:
            mk = "     mining: TIDAK ADA sesi aktif — akan di-start di cycle berikutnya"
        lines.append(c("│ ", "cyan") + c(fit(mk, W - 2), "red" if (m and m.get("overdue", 0) > 0) else ("green" if m else "yellow")) + c("│", "cyan"))
        bx = r.get("box")
        bonus = r.get("bonus") or {}
        info = (f"     lucky box {'siap dibuka' if (bx or 0) <= 0 else 'dalam ' + hms(bx)}"
                f"   check-in hari {bonus.get('day')}/7 {'✅' if bonus.get('claimed') else '⏳'}")
        lines.append(c("│ ", "cyan") + fit(info, W - 2) + c("│", "cyan"))
        mi = r.get("missions") or {}
        pend = ", ".join(mi.get("ready") or []) or "—"
        lines.append(c("│ ", "cyan") + fit(f"     mission {mi.get('done', '?')}/{mi.get('total', '?')} claimed · siap di-claim: {pend}", W - 2) + c("│", "cyan"))
        lines.append(c("├" + "─" * W + "┤", "cyan"))

    cyc = st.get("cycles", 0)
    last = st.get("last_cycle_utc") or "-"
    res = st.get("last_result") or "-"
    deltas = st.get("last_deltas") or {}
    dtxt = "  ".join(f"{k} {('+' + str(v)) if isinstance(v, (int, float)) and v else v}" for k, v in deltas.items()) or "-"
    lines.append(c("│ ", "cyan") + f"  daemon: tiap {INTERVAL_H:g} jam · cycle ke-{cyc} · refresh {REFRESH_S:g}s".ljust(W - 2) + c("│", "cyan"))
    lines.append(c("│ ", "cyan") + fit(f"  cycle terakhir: {last} · {res}", W - 2) + c("│", "cyan"))
    lines.append(c("│ ", "cyan") + fit(f"  perubahan: {dtxt}", W - 2) + c("│", "cyan"))
    nxt_txt = f"  cycle berikutnya: {datetime.datetime.fromtimestamp(nxt):%H:%M:%S} (dalam {hms(nxt - time.time())})" if nxt else "  cycle berikutnya: jalankan sekarang…"
    lines.append(c("│ ", "cyan") + c(fit(nxt_txt, W - 2), "yellow") + c("│", "cyan"))
    lines.append(c("├" + "─" * W + "┤", "cyan"))
    lines.append(c("│ ", "cyan") + c(fit("  Ctrl+C = stop bot (tetap di screen)  ·  ./uorm.sh start = jalankan lagi", W - 2), "gray") + c("│", "cyan"))
    lines.append(c("└" + "─" * W + "┘", "cyan"))
    return "\n".join(lines)


def gather(labels=None):
    accs = U.load_accounts()
    labels = labels or list(accs.keys())
    return [snapshot(lb, accs[lb]) for lb in labels if lb in accs]


def run_cycle(rows_before, labels=None):
    accs = U.load_accounts()
    labels = labels or list(accs.keys())
    results, deltas = {}, {}
    for lb in labels:
        acc = accs.get(lb)
        if not acc:
            continue
        try:
            res = D.run_account(lb, acc)
            results[lb] = "ok"
            before = next((r.get("coins") for r in rows_before if r["label"] == lb), None)
            if before is not None and isinstance(res.get("coins"), (int, float)):
                gain = round(res["coins"] - before, 4)
                if gain:
                    deltas[lb] = f"+{gain}"
        except Exception as e:  # noqa: BLE001
            results[lb] = f"ERROR {type(e).__name__}: {e}"
    U.save_accounts(accs)
    return results, deltas


def main():
    once = "--once" in sys.argv
    labels = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    st = load_state()
    if once:
        print(render(gather(labels), st))
        return 0
    st["next_cycle_ts"] = 0    # run one cycle right after start, then every INTERVAL_H
    try:
        while True:
            rows = gather(labels)
            st.setdefault("next_cycle_ts", time.time())
            os.system("clear" if os.environ.get("TERM") else "")
            sys.stdout.write("\033[H\033[2J" if USE_COLOR else "")
            print(render(rows, st))
            sys.stdout.flush()
            if time.time() >= st.get("next_cycle_ts", 0):
                print(c("\n  ⏳ menjalankan cycle rutin...\n", "yellow"), flush=True)
                results, deltas = run_cycle(rows, labels)
                st["cycles"] = st.get("cycles", 0) + 1
                st["last_cycle_utc"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                st["last_result"] = "; ".join(f"{k}:{v}" for k, v in results.items())
                st["last_deltas"] = deltas
                st["next_cycle_ts"] = time.time() + INTERVAL_H * 3600
                save_state(st)
            time.sleep(REFRESH_S)
    except KeyboardInterrupt:
        print(c("\n  ⏹  bot dihentikan. Lo masih di dalam screen — ketik ./uorm.sh start buat jalanin lagi.\n", "yellow"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
