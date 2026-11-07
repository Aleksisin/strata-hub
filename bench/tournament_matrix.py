r"""Tournament round-1 matrix driver: run remaining task x harness combos
SEQUENTIALLY (one GPU slot). d3 control already done for both harnesses.

Log: agentbench\runs\t1-matrix.log (UTF-8, single writer).
"""
import io
import os
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.realpath(__file__))
PY = sys.executable
LOG = os.path.join(HERE, "runs", "t1-matrix.log")
COMBOS = [("pi", "bf2-red-not-green"), ("pi", "bf3-expires-at"),
          ("pi", "i1-guard-scan-window"), ("pi", "d1-compression-casualties"),
          ("dsh", "bf2-red-not-green"), ("dsh", "bf3-expires-at"),
          ("dsh", "i1-guard-scan-window"), ("dsh", "d1-compression-casualties")]


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(time.strftime("=== T1 MATRIX start %Y-%m-%d %H:%M:%S ===\n"))
    results = []
    for harness, task in COMBOS:
        run_id = "t1-%s-%s" % (harness, task)
        out = os.path.join(HERE, "runs", run_id)
        if os.path.isfile(os.path.join(out, "report.json")):
            log("SKIP %s (report exists)" % run_id)
            continue
        log("RUN %s" % run_id)
        t0 = time.time()
        p = subprocess.run([PY, "-I", os.path.join(HERE, "tournament_run.py"),
                            harness, task, out],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=3600)
        wall = time.time() - t0
        tail = (p.stdout or "").strip().splitlines()
        res = tail[-1] if tail else "(no output)"
        log("DONE %s rc=%s wall=%.0fs | %s" % (run_id, p.returncode, wall, res[-120:]))
        results.append((run_id, p.returncode))
    log("=== MATRIX DONE: %s ===" % results)


if __name__ == "__main__":
    main()
