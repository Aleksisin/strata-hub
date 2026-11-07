r"""R0 series chain v2: STRICT sequencing via synchronous subprocess runs
of r0_driver.py (no log polling, no races). Steps:
  0. wait until any previous r0_driver python has exited AND prod healthy
  1. r02x: redo bf4 under budget flags (r02-bf4 was killed by the v1 race)
  2. r03m2: full trio under effort medium
  3. decision: r02 = d3+bf1 (r02x-bf4) and r03m2 3/3 -> run r04 trio
  4. final per-variant table into the series log; prod left on frozen cfg.
"""
import io
import json
import os
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
PY = r"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"
DRIVER = r"C:\Users\shogu\strata_tournament\r0_driver.py"
RUNS = r"C:\Users\shogu\Strata-Hub\agentbench\runs"
CHAIN_LOG = r"C:\Users\shogu\Strata-Hub\launch\chain-r0-series.log"
TRIO = ["d3-adr-erattum", "bf1-budget-tree", "bf4-gate-address"]


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    with open(CHAIN_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass


def prod_ready():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8107/health",
                                    timeout=3) as r:
            if b"ok" not in r.read():
                return False
    except Exception:
        return False
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
           "| Where-Object { $_.CommandLine -match '8107' } "
           "| ForEach-Object { $_.CommandLine }")
    out = (subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True).stdout or "").lower()
    return "nanoplus" in out and "--reasoning-budget" not in out


def wait_prev_done():
    for i in range(90):
        p = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter "
                            "\"Name='python.exe'\" | Where-Object { "
                            "$_.CommandLine -match 'r0_driver|r0_chain' } "
                            "| ForEach-Object { $_.ProcessId }"],
                           capture_output=True, text=True)
        pids = [x for x in (p.stdout or "").split() if x.strip().isdigit()]
        me = os.getpid()
        pids = [x for x in pids if int(x) != me]
        if not pids:
            log("no r0 driver/chain processes left")
            return prod_ready()
        time.sleep(60)
    log("wait_prev_done TIMEOUT - proceeding")
    return prod_ready()


def run(variant, prefix, tasks):
    log("=== RUN %s (%s) tasks=%r ===" % (variant, prefix, tasks))
    t0 = time.time()
    p = subprocess.run([PY, "-I", DRIVER, variant, prefix] + tasks,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=5 * 3600)
    log("=== %s exited rc=%s wall=%.0fs ===" % (prefix, p.returncode,
                                                time.time() - t0))
    for t in tasks:
        rp = os.path.join(RUNS, "%s-%s" % (prefix, t), "report.json")
        try:
            r = json.load(open(rp, encoding="utf-8"))
            log("  %-24s success=%-5s err=%-10s wall=%-7s rounds=%-3s "
                "auto_red=%s"
                % (t, r.get("success"), r.get("error_class"),
                   str(r.get("wall_s")), r.get("rounds"),
                   r.get("auto_red_count", "n/a")))
        except Exception:
            log("  %-24s NO/UNREADABLE REPORT" % t)


def passed(prefix, tasks):
    for t in tasks:
        try:
            r = json.load(open(os.path.join(RUNS, "%s-%s" % (prefix, t),
                                            "report.json"),
                               encoding="utf-8"))
            if not r.get("success"):
                return False
        except Exception:
            return False
    return True


def main():
    with open(CHAIN_LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("=== R0 SERIES v2 start %Y-%m-%d %H:%M:%S ===\n"))
    if not wait_prev_done():
        log("prod not ready at start - aborting chain")
        return 2
    # step 1: redo bf4 under budget flags
    run("r02", "r02x", ["bf4-gate-address"])
    r02_tasks = {"d3-adr-erattum": passed("r02", ["d3-adr-erattum"]),
                 "bf1-budget-tree": passed("r02", ["bf1-budget-tree"]),
                 "bf4-gate-address": passed("r02x", ["bf4-gate-address"])}
    # step 2: effort medium trio
    run("r03m", "r03m2", TRIO)
    # step 3: combo decision
    if all(r02_tasks.values()) and passed("r03m2", TRIO):
        log("r02 and r03m both passed smoke -> running r04 combo")
        run("r04", "r04", TRIO)
    else:
        log("combo skipped: r02=%r r03m=%r" % (r02_tasks,
                                               passed("r03m2", TRIO)))
    log("=== R0 SERIES v2 COMPLETE (prod on frozen config) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
