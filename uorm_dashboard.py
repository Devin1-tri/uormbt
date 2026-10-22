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

INTERVAL_H = float(os.environ.get("INTERVAL_H", "3"))   # 3h: claims land within <=3h of the
# 24h mark and the 4h lucky box is caught more often than with a 6h cycle.
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
    """Merge instead of clobbering — uorm_daily.config_watch() also writes here."""
    try:
        cur = json.load(open(STATE))
    except Exception:
        cur = {}
    cur.update(st)
    json.dump(cur, open(STATE, "w"), indent=1)


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
        # A stale token used to be a hard error here. uorm_lib.refresh() now holds the
        # accounts lock across the whole rotation, so refreshing from the dashboard is
        # safe (the old "lost update" race is fixed at the source).
        p = U.profile(acc)
        if not p:
            try:
                U.ensure_token(acc, label)
            except Exception as e:  # noqa: BLE001
                print(f"[{label}] ensure_token: {e}")
            p = U.profile(acc)
        if not p:
            raise RuntimeError("profil tak terbaca (token/login) — lihat log runner")
        exp = U.token_exp(acc)
        row["token"] = f"{int((exp - time.time()) / 60)}m" if exp else "?"
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


def _cells(text):
    """Display width: emoji/wide codepoints take 2 columns."""
    import unicodedata
    n = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _strip(text):
    import re
    return re.sub(r"\033\[[0-9;]*m", "", str(text))


def pad(text, width, align="left"):
    """Pad a (possibly already coloured) cell to `width` visible columns."""
    import re
    text = str(text)
    cur = _cells(_strip(text))
    if cur > width:
        # trim visible characters, keeping escape sequences intact, then add …
        out, used, i = [], 0, 0
        while i < len(text) and used < width - 1:
            if text[i] == "\033":
                j = text.find("m", i)
                if j == -1:
                    break
                out.append(text[i:j + 1])
                i = j + 1
                continue
            out.append(text[i])
            used += 1
            i += 1
        tail = "" if re.search(r"\033\[0m$", text) else "\033[0m"
        return "".join(out) + "…" + tail
    fill = " " * (width - cur)
    if align == "right":
        return fill + text
    if align == "center":
        left = fill[: len(fill) // 2]
        return left + text + fill[len(left):]
    return text + fill


def render(rows, st):
    """Boxed table: one row per account, aligned columns, daemon footer.

    Columns are fixed-width so numbers line up and the eye can scan down a
    column. Width is chosen per terminal: the full table needs ~100 cols, a
    phone terminal gets a compact table (label, coins, mining state) instead of
    a broken box.
    """
    now = datetime.datetime.now()
    import shutil
    term = shutil.get_terminal_size((100, 30)).columns
    wide = term >= 100

    # (header, width, align)
    if wide:
        cols = [("akun", 6, "left"), ("coins", 11, "right"), ("rank", 9, "left"),
                ("mining", 9, "left"), ("sisa", 9, "left"), ("box", 8, "left"),
                ("check-in", 9, "left"), ("mission", 9, "center"), ("token", 5, "right")]
    else:
        cols = [("akun", 5, "left"), ("coins", 10, "right"),
                ("mining", 9, "left"), ("sisa", 8, "left"), ("token", 6, "right")]
    W = sum(w for _, w, _ in cols) + 3 * len(cols) + 1
    SEP = c("│", "cyan")
    lines = []

    def rule(l, m, r):
        return c(l + m.join("─" * (w + 2) for _, w, _ in cols) + r, "cyan")

    def rowline(cells):
        """cells = dict column->text (list juga boleh, dipetakan per posisi)."""
        parts = []
        for i, (name, w, align) in enumerate(cols):
            if isinstance(cells, dict):
                txt = cells.get(name, "")
            else:
                txt = cells[i] if i < len(cells) else ""
            parts.append(pad(txt, w, align))
        return c("│", "cyan") + " " + (" " + SEP + " ").join(parts) + " " + c("│", "cyan")

    def panel(text, style=None):
        body = pad(text, W - 2)
        if style:
            body = c(body, style)
        return c("│", "cyan") + " " + body + " " + c("│", "cyan")

    # ---- header -----------------------------------------------------------
    lines.append(rule("┌", "┬", "┐"))
    lines.append(panel(f"UORM BOT  ·  {now:%Y-%m-%d %H:%M:%S} WIB", "bold"))
    lines.append(rule("├", "┼", "┤"))
    lines.append(rowline([h for h, _, _ in cols]))
    lines.append(rule("├", "┼", "┤"))

    # ---- one row per account ---------------------------------------------
    for r in rows:
        if r.get("disabled"):
            lines.append(rowline({"akun": r["label"], "coins": c("MATI", "yellow"),
                                  "mining": "-"}))
            continue
        if not r.get("ok"):
            lines.append(rowline({"akun": r["label"], "coins": c("ERROR", "red"),
                                  "mining": "-"}))
            continue

        m = r.get("mining")
        if not m:
            mk, left = c("IDLE", "yellow"), "-"
        elif m.get("overdue", 0) > 0:
            mk, left = c("LEWAT", "red"), c(f"{m['overdue']}h", "red")
        else:
            mk, left = c(f"x{m.get('mult') or 1}", "green"), hms(m.get("left"))

        bx = r.get("box") or 0
        box_txt = c("siap", "green") if bx <= 0 else hms(bx)
        bonus = r.get("bonus") or {}
        ci = c(f"{bonus.get('day')}/7 ok", "green") if bonus.get("claimed") \
            else c(f"{bonus.get('day')}/7 -", "yellow")
        mi = r.get("missions") or {}
        ready = len(mi.get("ready") or [])
        mis = c(f"{mi.get('done', '?')}/{mi.get('total', '?')}", "green")
        if ready:
            mis += c(f" +{ready}", "yellow")

        coins = r.get("coins")
        coins_txt = f"{float(coins):.2f}" if isinstance(coins, (int, float)) else str(coins)
        cells = {"akun": r["label"], "coins": coins_txt,
                 "rank": str(r.get("rank") or "?").upper()[:9], "mining": mk,
                 "sisa": left, "box": box_txt, "check-in": ci, "mission": mis,
                 "token": str(r.get("token", "?"))}
        lines.append(rowline(cells))

    lines.append(c("├" + "┴".join("─" * (w + 2) for _, w, _ in cols) + "┤", "cyan"))

    # ---- detail per account (second box) ---------------------------------
    detail = []
    for r in rows:
        if not r.get("ok"):
            detail.append(c(f"  {r['label']:<4} ", "bold")
                          + c(f"{r.get('error') or r.get('disabled') or 'tidak terbaca'}",
                              "red"))
            continue
        detail.append(c(f"  {r['label']:<4} ", "bold")
                      + c(f"{r.get('email', '?')}@gmail.com  ·  code {r.get('ref_code')}  ·  "
                          f"refs {r.get('refs')}  ·  lvl {r.get('level')}"
                          + (f"  ·  {mi.get('ready')}" if False else ""), "gray"))
    if detail:
        lines.append(c("┌" + "─" * (W - 2) + "┐", "cyan"))
        for d in detail:
            lines.append(c("│", "cyan") + pad(d, W - 2) + c("│", "cyan"))
        lines.append(c("└" + "─" * (W - 2) + "┘", "cyan"))

    # ---- footer, no box --------------------------------------------------
    nxt = st.get("next_cycle_ts")
    res = st.get("last_result") or "-"
    deltas = st.get("last_deltas") or {}
    dtxt = "  ".join(f"{k} {v}" for k, v in deltas.items()) or "-"
    lines.append("")
    lines.append(c(f" cycle ke-{st.get('cycles', 0)}  ·  tiap {INTERVAL_H:g}j  ·  "
                   f"terakhir {st.get('last_cycle_utc') or '-'}  ·  {res}", "gray"))
    if nxt:
        lines.append(c(f" berikutnya {datetime.datetime.fromtimestamp(nxt):%H:%M:%S} "
                       f"(dalam {hms(nxt - time.time())})  ·  perubahan {dtxt}", "yellow"))
    scfg = st.get("config") or {}
    if scfg:
        short = [("device.enforce", "anti-bot"), ("app.maintenance_mode", "maintenance"),
                 ("app.latest_version", "app"), ("mining.base_rate_per_hour", "rate/h")]
        parts = []
        for key, label in short:
            if key in scfg:
                v = scfg[key]
                if isinstance(v, bool):
                    v = "ON" if v else "off"
                parts.append(f"{label}={v}")
        lines.append(c(" server: " + " · ".join(parts), "gray"))
    lines.append(c(" Ctrl+C = stop bot (tetap di screen)  ·  ./uorm.sh start = jalankan lagi", "gray"))
    return "\n".join(lines)


def gather(labels=None):
    accs = U.load_accounts()
    labels = labels or list(accs.keys())
    out = []
    for lb in labels:
        acc = accs.get(lb)
        if not acc:
            continue
        if acc.get("disabled"):
            out.append({"label": lb, "ok": False, "disabled": acc.get("disabled_reason", "-")})
            continue
        out.append(snapshot(lb, acc))
    return out


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
    # NB: accounts.json is written by the routine (uorm_lib.update_account, locked).
    # Writing our stale snapshot here would clobber rotated refresh tokens.
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
                st["last_cycle_utc"] = datetime.datetime.now(
                    datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
