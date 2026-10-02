#!/usr/bin/env python3
"""Standalone UORM daemon — for VPS/screen deployments outside the Hermes cron.

Runs the daily routine every INTERVAL_H hours, appends to a log, and (optionally)
prints a line per cycle. Recommended over `screen` only when you want the bot on
a box that has no cron/agent; the Hermes cron job already does this with delivery
+ failure alerts.

Usage:
    python uorm_loop.py                # loop, 6h interval
    INTERVAL_H=4 python uorm_loop.py   # custom interval
    python uorm_loop.py --once         # single pass (same as uorm_daily.py)
"""
import os
import sys
import time
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uorm_daily  # noqa: E402

INTERVAL_H = float(os.environ.get("INTERVAL_H", "6"))
LOG = os.environ.get("UORM_LOG", os.path.join(HERE, "uorm_loop.log"))


def log(line):
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    with open(LOG, "a") as f:
        f.write(f"[{stamp}] {line}\n")
    print(f"[{stamp}] {line}", flush=True)


if __name__ == "__main__":
    if "--once" in sys.argv:
        uorm_daily.QUIET = False
        uorm_daily.main(None)
        raise SystemExit(0)
    log(f"daemon start, interval {INTERVAL_H}h, log {LOG}")
    while True:
        try:
            rows = uorm_daily.main(None)
            log("cycle ok: " + "; ".join(f"{r.get('label')}={r.get('coins')}" for r in rows))
        except Exception as e:  # noqa: BLE001
            log(f"cycle ERROR {type(e).__name__}: {e}")
        time.sleep(int(INTERVAL_H * 3600))
