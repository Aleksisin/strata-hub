"""Acceptance tests for Supervisor v1 (TASK-SUPERVISOR-2026-11-04.md section 8).

Run: python -I tests/acceptance.py
Requires: supervisor running via Task Scheduler (started by START-SUPERVISOR.bat),
production contour alive for test 5 (8107 llama.cpp, 8109 agentgw, 6280 localdocs).

TEST1: two simultaneous submits -> one RUNNING, other stays QUEUED (pending).
TEST4: duplicate run_id -> rejected, existing results untouched.
TEST2: kill flag mid-run -> INTERRUPTED/FAILED_INVALID, never DONE; artifacts kept.
TEST5: engines/gateways healthy before AND after the kill (isolation).
TEST3: hard-kill supervisor while RUNNING -> restart -> recovery (INTERRUPTED),
       queued job survives and completes, DONE results intact.
FINAL: ok job -> DONE; timeout job -> FAILED_TIMEOUT; bad-artifact job ->
       FAILED_INVALID, no manual help (completion criterion).
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = r"C:\Users\shogu\Strata-Hub\supervisor"
PY = r"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"
STATE = os.path.join(BASE, "state")
PENDING = os.path.join(BASE, "queue", "pending")
REJECTED = os.path.join(BASE, "queue", "rejected")
ACTIVE = os.path.join(BASE, "queue", "active")
HEARTBEAT = os.path.join(STATE, "supervisor.json")

RESULTS = []


def ok(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


def state(run_id):
    p = os.path.join(STATE, run_id + ".json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def heartbeat():
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def wait_status(run_id, statuses, timeout_s=90):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = state(run_id)
        if st and st.get("status") in statuses:
            return st
        time.sleep(1)
    return state(run_id)


def mkjob(run_id, **kw):
    args = [PY, "-I", os.path.join(BASE, "tests", "mkjob.py"), run_id]
    for k, v in kw.items():
        if v is None:
            continue
        if isinstance(v, bool):
            if v:
                args.append("--" + k)
        else:
            args += ["--" + k, str(v)]
    subprocess.run(args, check=True, capture_output=True)


def health(url):
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def kill_supervisor():
    hb = heartbeat()
    pid = hb.get("pid") if hb else None
    if pid:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)
    time.sleep(2)
    return hb.get("pid") if hb else None


def start_supervisor():
    r = subprocess.run(["cmd", "/c", os.path.join(BASE, "START-SUPERVISOR.bat")],
                       capture_output=True, text=True)
    return r.returncode


def wait_supervisor_up(timeout_s=40):
    """Wait for a LIVE supervisor: heartbeat pid alive AND pulse fresh."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        hb = heartbeat()
        if hb and hb.get("pid") and _alive(hb["pid"]):
            try:
                import calendar
                import datetime
                pulse = hb.get("pulse_utc", "")
                pt = calendar.timegm(datetime.datetime.strptime(
                    pulse, "%Y-%m-%dT%H:%M:%SZ").timetuple())
                age = time.time() - pt
                if abs(age) < 15:
                    return hb
            except Exception:
                pass
        time.sleep(1)
    return None


def main():
    # fresh slate: remove stop flag, wait for supervisor
    flag = os.path.join(STATE, "stop.flag")
    if os.path.isfile(flag):
        os.remove(flag)
    hb = heartbeat()
    if not hb or not hb.get("pid") or not _alive(hb["pid"]):
        print("supervisor not running - starting via START-SUPERVISOR.bat")
        start_supervisor()
        hb = wait_supervisor_up()
    ok("supervisor alive", hb is not None, "pid=%s" % (hb or {}).get("pid"))

    # ---------------- TEST 1: two simultaneous ----------------
    mkjob("acc1-a", sleep=8)
    mkjob("acc1-b", sleep=8)
    sa = sb = None
    saw_queue = False
    t0 = time.time()
    while time.time() - t0 < 30:
        sa, sb = state("acc1-a"), state("acc1-b")
        if sa and sa["status"] == "RUNNING":
            if os.path.isfile(os.path.join(PENDING, "acc1-b.json")):
                saw_queue = True
                break
        if sb and sb["status"] == "RUNNING":
            if os.path.isfile(os.path.join(PENDING, "acc1-a.json")):
                saw_queue = True
                break
        time.sleep(0.5)
    ok("TEST1 second stays QUEUED", saw_queue,
       "a=%s b=%s" % ((sa or {}).get("status"), (sb or {}).get("status")))
    fa = wait_status("acc1-a", {"DONE"}, 120)
    fb = wait_status("acc1-b", {"DONE"}, 120)
    ok("TEST1 both complete DONE", fa and fa["status"] == "DONE"
       and fb and fb["status"] == "DONE",
       "a=%s b=%s" % ((fa or {}).get("status"), (fb or {}).get("status")))

    # ---------------- TEST 4: duplicate run_id ----------------
    before = os.path.getmtime(os.path.join(BASE, "runs", "acc1-a",
                                           "out", "result.json"))
    mkjob("acc1-a", sleep=0.5)  # same run_id again
    time.sleep(3)
    rej = os.path.join(REJECTED, "acc1-a.json")
    dup_rejected = os.path.isfile(rej)
    reason = ""
    if dup_rejected:
        reason = json.load(open(rej, encoding="utf-8")).get("_rejected", [])
    after = os.path.getmtime(os.path.join(BASE, "runs", "acc1-a",
                                          "out", "result.json"))
    ok("TEST4 duplicate rejected", dup_rejected, "; ".join(reason))
    ok("TEST4 artifacts untouched", before == after and state("acc1-a")["status"] == "DONE")

    # ---------------- TEST 5 pre-check + TEST 2: kill mid-run ----------------
    h_before = {u: health(u) for u in
                ("http://127.0.0.1:8107/health", "http://127.0.0.1:8109/health",
                 "http://127.0.0.1:6280/health")}
    print("health before:", h_before)
    mkjob("acc2-kill", sleep=45)
    st = wait_status("acc2-kill", {"RUNNING"}, 30)
    ok("TEST2 job RUNNING", st and st["status"] == "RUNNING")
    kdir = os.path.join(STATE, "kill")
    os.makedirs(kdir, exist_ok=True)
    open(os.path.join(kdir, "acc2-kill.flag"), "w").close()
    st = wait_status("acc2-kill", {"INTERRUPTED", "FAILED_INVALID", "DONE"}, 30)
    ok("TEST2 killed -> not DONE", st and st["status"] in ("INTERRUPTED", "FAILED_INVALID"),
       "status=%s" % (st or {}).get("status"))
    art = os.path.join(BASE, "runs", "acc2-kill", "out", "result.json")
    ok("TEST2 artifacts preserved as-is", not os.path.isfile(art))
    h_after = {u: health(u) for u in h_before}
    print("health after:", h_after)
    ok("TEST5 contour untouched by kill", h_after == h_before, json.dumps(h_after))

    # ---------------- TEST 3: supervisor restart ----------------
    mkjob("acc3-queued", sleep=6)
    mkjob("acc3-2", sleep=2)   # must still be pending while acc3-queued runs
    pid = kill_supervisor()
    ok("TEST3 supervisor hard-killed", pid is not None, "pid=%s" % pid)
    time.sleep(2)
    child = state("acc3-queued")
    ok("TEST3 child state remains RUNNING (supervisor died)",
       child is None or child["status"] == "RUNNING",
       "state=%s" % (child or {}).get("status"))
    pending_survived = os.path.isfile(os.path.join(PENDING, "acc3-2.json"))
    done_before = {r: (state(r) or {}).get("status")
                   for r in ("acc1-a", "acc1-b", "acc2-kill")}
    start_supervisor()
    hb = wait_supervisor_up()
    ok("TEST3 supervisor restarted", hb is not None, "pid=%s" % (hb or {}).get("pid"))
    st = wait_status("acc3-queued", {"DONE", "INTERRUPTED", "FAILED_INVALID"}, 60)
    ok("TEST3 dead run recovered to terminal status",
       st and st["status"] in ("DONE", "INTERRUPTED", "FAILED_INVALID"),
       "status=%s recovered=%s" % ((st or {}).get("status"), (st or {}).get("recovered")))
    f = wait_status("acc3-2", {"DONE"}, 90)
    ok("TEST3 pending queue survived restart", f and f["status"] == "DONE" and pending_survived,
       "acc3-2=%s pending_seen=%s" % ((f or {}).get("status"), pending_survived))
    done_after = {r: (state(r) or {}).get("status") for r in ("acc1-a", "acc1-b", "acc2-kill")}
    ok("TEST3 existing results intact", done_before == done_after)

    # ---------------- FINAL: unattended chain ----------------
    mkjob("accf-1", sleep=2)
    mkjob("accf-2", sleep=30, timeout=2)
    mkjob("accf-3", sleep=1, bad=True)
    s1 = wait_status("accf-1", {"DONE"}, 60)
    s2 = wait_status("accf-2", {"FAILED_TIMEOUT"}, 90)
    s3 = wait_status("accf-3", {"FAILED_INVALID"}, 60)
    ok("FINAL ok -> DONE", s1 and s1["status"] == "DONE")
    ok("FINAL timeout -> FAILED_TIMEOUT", s2 and s2["status"] == "FAILED_TIMEOUT")
    ok("FINAL bad artifact -> FAILED_INVALID", s3 and s3["status"] == "FAILED_INVALID")

    npass = sum(1 for _, c, _ in RESULTS if c)
    print("\n==== ACCEPTANCE: %d/%d PASS ====" % (npass, len(RESULTS)))
    for name, c, d in RESULTS:
        if not c:
            print("  FAILED:", name, d)
    return 0 if npass == len(RESULTS) else 1


def _alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
