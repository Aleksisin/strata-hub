r"""Tournament driver: run ONE harness (pi | dsh) on ONE AgentBench task.

Same worktree/overlay/verifier as AgentBench (reuses agentbench_runner
prepare_copy + verify_*) — ONE verifier for all harnesses. Harness tools
stay NATIVE (differences are the subject of the tournament).

Usage:
  python -I tournament_run.py <pi|dsh> <task_id> <out_dir>

Report fields: harness, task, success, error_class (none | verifier_red |
wall_timeout | harness_launch | harness_error), wall_s, verify_tail,
harness_rc, harness_output.txt (full stdout trace).
Wall cap 3000 s (comparable to AgentBench caps).
"""
import io
import json
import os
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import agentbench_runner as R  # noqa: E402

PI_AGENT_DIR = r"C:\Users\shogu\.pi\agent-tournament"
PI_CMD = r"C:\Users\shogu\AppData\Roaming\npm\pi.cmd"
DSH_CMD = r"C:\Users\shogu\Strata-Hub\harness-tournament\dsh\node_modules\.bin\dsh.cmd"
WALL_CAP = 3000
# Statements are passed via STDIN: npm .cmd shims route argv through
# cmd.exe, whose quoting mangles multi-line task statements ("file not
# found" on a broken fragment). dsh reads "-" from stdin; pi -p reads
# stdin when no positional prompt is given.


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("harness", choices=["pi", "dsh"])
    ap.add_argument("task")
    ap.add_argument("out")
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)

    os.makedirs(a.out, exist_ok=True)
    task = json.load(open(os.path.join(HERE, "tasks", a.task + ".json"),
                          encoding="utf-8"))
    log = lambda msg: print(time.strftime("[%H:%M:%S] ") + msg, flush=True)
    log("tournament %s on %s (base %s)" % (a.harness, a.task, task["base"]))

    try:
        copy = R.prepare_copy(task, a.out)
    except Exception as e:
        json.dump({"harness": a.harness, "task": a.task, "success": False,
                   "error_class": "harness_launch", "error": str(e)},
                  open(os.path.join(a.out, "report.json"), "w",
                       encoding="utf-8"), ensure_ascii=False, indent=1)
        log("prepare_copy FAILED: %r" % e)
        return 2

    statement = task["statement"]
    env = os.environ.copy()
    env.setdefault("STRATA_LOCAL_API_KEY", "sk-strata-local-placeholder")
    if a.harness == "pi":
        env["PI_CODING_AGENT_DIR"] = PI_AGENT_DIR
        cmd = [PI_CMD, "-p", "--mode", "json", "--no-session",
               "--provider", "strata-local", "--model", "nanoplus-v3"]
    else:
        cmd = [DSH_CMD, "--profile", "headless", "--json", "-"]

    t0 = time.time()
    error_class = "none"
    raw = ""
    rc = None
    try:
        p = subprocess.run(cmd, cwd=copy, input=statement,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=WALL_CAP, env=env)
        raw = (p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or "")
        rc = p.returncode
        log("harness exited rc=%s in %.0fs" % (rc, time.time() - t0))
    except subprocess.TimeoutExpired as e:
        error_class = "wall_timeout"
        raw = (e.stdout or b"")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw += "\n--- stderr ---\n" + str(e.stderr or "")
        log("WALL TIMEOUT after %.0fs" % (time.time() - t0))
    except FileNotFoundError as e:
        error_class = "harness_launch"
        raw = "launcher not found: %r" % e
        log("HARNESS LAUNCH FAILED (Windows-specific class): %r" % e)
    with open(os.path.join(a.out, "harness_output.txt"), "w",
              encoding="utf-8") as f:
        f.write(raw)

    report = {"harness": a.harness, "task": a.task, "kind": task["kind"],
              "base": task["base"], "wall_s": round(time.time() - t0, 1),
              "harness_rc": rc}
    if error_class == "none":
        if task["kind"] == "bugfix":
            ok = R.verify_bugfix(task, copy, report)
        elif task["kind"] == "diagnosis":
            ok = R.verify_diagnosis(task, copy, report)
        else:
            ok = R.verify_integration(task, copy, report)
        report["success"] = bool(ok)
        if not ok:
            error_class = "verifier_red"
    else:
        report["success"] = False
    report["error_class"] = error_class
    json.dump(report, open(os.path.join(a.out, "report.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    log("RESULT %s: success=%s error_class=%s wall=%.0fs"
        % (a.task, report["success"], error_class, report["wall_s"]))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
