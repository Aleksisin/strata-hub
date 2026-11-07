"""Dummy job for supervisor acceptance tests.

Writes <out>/out/result.json = {"ok": true, "n": N} after sleeping.
Flags: --crash = exit(3) before writing; --bad = write ok:false.
"""
import argparse
import json
import os
import sys
import time

p = argparse.ArgumentParser()
p.add_argument("--out", required=True)
p.add_argument("--sleep", type=float, default=0.0)
p.add_argument("--n", type=int, default=1)
p.add_argument("--crash", action="store_true")
p.add_argument("--bad", action="store_true")
a = p.parse_args()

os.makedirs(os.path.join(a.out, "out"), exist_ok=True)
print("dummy job start, sleep %.1fs" % a.sleep, flush=True)
time.sleep(a.sleep)
if a.crash:
    print("dummy crash", flush=True)
    sys.stdout.flush()
    os._exit(3)
res = {"ok": False, "n": a.n} if a.bad else {"ok": True, "n": a.n}
with open(os.path.join(a.out, "out", "result.json"), "w", encoding="utf-8") as f:
    json.dump(res, f)
print("dummy done ok=%s n=%s" % (res["ok"], res["n"]), flush=True)
