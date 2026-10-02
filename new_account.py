#!/usr/bin/env python3
"""Create a UORM account and (optionally) apply a referral code.

Usage: new_account.py <label> [referral_code]
Example: new_account.py a1 <REFERRAL_CODE>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import uorm_lib as U  # noqa: E402

label = sys.argv[1]
ref = sys.argv[2] if len(sys.argv) > 2 else None

acc = U.signup(label, label=label)
print("created:", acc["label"], "|", acc["email"], "| uid:", acc["user_id"])
if ref:
    code, resp = U.rpc(acc, "apply_referral_code", {"p_code": ref})
    print("apply_referral_code(%s):" % ref, code, json.dumps(resp)[:220])
accs = U.load_accounts()
accs[label] = acc
U.save_accounts(accs)
print("saved. labels:", list(accs.keys()))
