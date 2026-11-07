"""Create a dummy supervisor job: python -I mkjob.py <run_id> --sleep 8 [--bad] [--timeout 2]"""
import argparse
import json
import os
import sys

BASE = r"C:\Users\shogu\Strata-Hub\supervisor"
PY = r"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"

p = argparse.ArgumentParser()
p.add_argument("run_id")
p.add_argument("--sleep", type=float, default=1.0)
p.add_argument("--bad", action="store_true")
p.add_argument("--timeout", type=float, default=None)
a = p.parse_args()

run_dir = os.path.join(BASE, "runs", a.run_id)
cmd = [PY, "-I", os.path.join(BASE, "tests", "dummy_job.py"),
       "--out", run_dir, "--sleep", str(a.sleep), "--n", "1"]
if a.bad:
    cmd.append("--bad")
job = {
    "run_id": a.run_id,
    "kind": "dummy",
    "cmd": cmd,
    "workdir": BASE,
    "run_dir": run_dir,
    "validate": {"kind": "dummy", "expected_ids": 1},
}
if a.timeout is not None:
    job["timeout_s"] = a.timeout
dst = os.path.join(BASE, "queue", "pending", a.run_id + ".json")
with open(dst, "w", encoding="utf-8") as f:
    json.dump(job, f)
print("queued", a.run_id)
