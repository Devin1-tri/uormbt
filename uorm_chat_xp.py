#!/usr/bin/env python3
"""UORM chat-XP farmer — levels the account by sending qualifying room messages.

Reversed from libapp.so (APK 1.0.6) + live probes (2026-10-16):
  RPC      : send_room_message(p_content) -> {awarded, xp(total), level, repeat, room_id}
  eligible : >=5 chars, >=4 letters, >=3 distinct letters (numbers/punctuation/emoji = 0 xp)
  pacing   : server cooldown chat.cooldown_seconds=2; use 3s to stay clear of "slow down"
  cap      : room-activity score counts up to 20 qualifying msgs/person/day; XP still
             awarded past 20 but rate drops (5xp -> 3xp) -> we stop at 20 for the day.
  streak   : chat on consecutive days -> xp multiplier x1.05 (3d), x1.10 (7d), x1.15 (14d).

State file per account: chat_xp_state.json (day, count). Idempotent: rerun resumes,
never double-counts within a day. Single message = dry probe (prints the server reply).
"""
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uorm_lib as U  # noqa: E402

STATE = os.path.join(HERE, "chat_xp_state.json")
DAILY_CAP = 20          # room-activity cap (per the in-app copy)
EARN_CAP = 13           # observed: profiles.xp stops rising after ~13 awarded msgs/day
GAP_S = 3.2             # > chat.cooldown_seconds(2), safe vs the observed rate limiter

# qualifying messages: all >=5 chars, >=4 letters, >=3 distinct letters
CHAT_LINES = [
    "chatting in the room while mining runs today",
    "the mining rate feels better with chat levels up",
    "steady cycle today everyone keep the room active",
    "sending a quick message from my room here",
    "room activity helps the whole cohort mine faster",
    "checking the chat level bonus on my account now",
    "keeping my streak alive with a short message here",
    "another qualifying message for the chat pool today",
    "the room looks busy this morning good to see",
    "mining is running smooth hope everyone is well",
    "quick hello to the room before my next claim",
    "chat pool boosts mining for all of us here",
    "one more message to stay active in the room",
    "appreciate the room bonus on the mining rate",
    "staying consistent with daily chat and mining",
    "short message here to keep the streak going",
    "the chat level really helps the hourly rate",
    "good vibes in the room today keep it up",
    "posting my daily chat message for the pool",
    "last qualifying message for the room today",
]


def qualifies(text):
    letters = [c for c in text.lower() if c.isalpha()]
    return len(text) >= 5 and len(letters) >= 4 and len(set(letters)) >= 3


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(st):
    json.dump(st, open(STATE, "w"), indent=1)


def get_room(acc):
    """A room is required (RLS on room_messages); create+switch if we are in none."""
    p = U.profile(acc)
    if p and p.get("room_id"):
        return p["room_id"]
    _, cr = U.rpc(acc, "create_room", {"p_name": "uorm-chat-farm"})
    rid = (cr or {}).get("room_id") if isinstance(cr, dict) else None
    if not rid:
        raise RuntimeError(f"create_room gagal: {cr}")
    U.rpc(acc, "switch_room", {"new_room_id": rid, "p_join_pin": None})
    return rid


def send(acc, text):
    return U.rpc(acc, "send_room_message", {"p_content": text})


def _unique_texts(acc, state, n):
    """Messages the server has NOT already rejected with 'you already sent that'.

    The duplicate check is per-recipient server-side (room_messages history), so the
    same 20 lines only ever pay once. Rotate a suffix per day to stay fresh:
    'day phrase <n>' keeps the >=3-distinct-letter rule satisfied.
    """
    stamp = time.strftime("%Y%m%d")
    out = []
    i = 0
    while len(out) < n and i < n * 8:
        i += 1
        base = CHAT_LINES[(state.get("sent_total", 0) + i) % len(CHAT_LINES)]
        text = f"{base} {stamp}{i:03d}"
        if qualifies(text):
            out.append(text)
    return out


def profile_field(acc, field):
    p = U.profile(acc)
    return (p or {}).get(field) if isinstance(p, dict) else None


def run_daily(label):
    """Called by the 3h dashboard cycle: send up to the daily room-activity cap of
    fresh qualifying messages, resuming from chat_xp_state.json (idempotent).

    Server truth (probed 2026-10-16): profiles.xp stopped rising at 135 after ~13
    QUALIFYING msgs even though chat_xp_log.count kept climbing to 30 and later
    sends returned awarded=False. So the real daily XP ceiling is ~13 awarded msgs
    (~120-135 xp), NOT the 20 room-activity cap. EARN_CAP reflects that; past it we
    stop sending (extra sends cost nothing but are pointless and look spammy)."""
    return run(label)


def run(label, max_msgs=None, dry=False):
    acc = U.ensure_token(U.load_accounts()[label], label)
    st = load_state()
    day = time.strftime("%Y-%m-%d")
    ent = st.setdefault(label, {"day": day, "count": 0, "sent_total": 0})
    if ent["day"] != day:
        ent.update(day=day, count=0)

    room = get_room(acc)
    xp_before = profile_field(acc, "xp")

    want = min(DAILY_CAP, EARN_CAP) - ent["count"] if max_msgs is None else max_msgs
    if want <= 0:
        print(f"[{label}] chat xp: cap xp harian ({EARN_CAP}) sudah penuh (day {ent['day']})")
        return ent

    texts = _unique_texts(acc, ent, want)
    sent = 0
    for i, text in enumerate(texts):
        if dry:
            print(f"[{label}] DRY room={room} xp_before={xp_before} text={text!r}")
            return ent
        code, resp = send(acc, text)
        if code == 200 and isinstance(resp, dict) and resp.get("awarded"):
            ent["count"] += 1
            ent["sent_total"] = ent.get("sent_total", 0) + 1
            sent += 1
            print(f"[{label}] xp_total={resp.get('xp')} level={resp.get('level')} "
                  f"repeat={resp.get('repeat')} ({ent['count']}/{EARN_CAP})")
            save_state(st)
        elif code == 400 and "already sent" in str(resp):
            ent["sent_total"] = ent.get("sent_total", 0) + 1  # consumed, try next text
            save_state(st)
        else:
            print(f"[{label}] send gagal code={code} resp={str(resp)[:140]}")
        if i < len(texts) - 1:
            time.sleep(GAP_S)

    xp_after = profile_field(acc, "xp")
    lvl = profile_field(acc, "level")
    print(f"[{label}] done: kirim {sent} pesan baru · xp {xp_before} -> {xp_after} · level={lvl}")
    return ent


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "a1"
    dry = "--dry" in sys.argv
    max_msgs = None
    for a in sys.argv[1:]:
        if a.startswith("--count="):
            max_msgs = int(a.split("=")[1])
    run(label, max_msgs=max_msgs, dry=dry)
