"""Validate IBF-AgentBench bugfix candidates: red-at-base / green-at-fix.

Protocol per candidate (SWE-bench style):
  base worktree (fix^) + TEST overlay from the fix commit -> pytest -> RED
  (target test methods must be among failures)
  fix worktree (full commit) -> pytest -> GREEN (whole file)

Candidates are real fix commits from IBF history (C:/IBF, read-only; the
mirror lives in agentbench/mirror).
"""
import io
import os
import re
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = r"C:\Users\shogu\Strata-Hub\agentbench"
MIRROR = os.path.join(BASE, "mirror")
SCRATCH = os.path.join(BASE, "scratch")
VENV_PY = r"C:\IBF\DiagnosticPipeline\.venv\Scripts\python.exe"

MQ = "tests/test_merge_queue.py"
BF = "tests/test_became_facts_probe.py"

CANDS = [
    ("bf1-budget-tree", "07f8ef7f", MQ, [
        "test_release_budget_stops_a_grandchild_that_holds_the_pipe"]),
    ("bf2-red-not-green", "9362ef06", MQ, [
        "test_a_gate_that_reports_red_is_never_certified_however_green_its_log",
        "test_a_red_report_is_refused_even_when_the_gate_exits_cleanly",
        "test_a_surviving_descendant_costs_the_run_its_certificate",
        "test_a_held_pipe_is_refused_rather_than_noted",
        "test_the_gate_tree_is_stopped_even_when_the_gate_returns_in_time"]),
    ("bf3-expires-at", "f9af8e1c", MQ, [
        "test_unreadably_large_expiry_is_unknown_not_a_crash",
        "test_a_checkout_that_survives_its_own_cleanup_is_named_not_done"]),
    ("bf4-gate-address", "ae08ef20", MQ, [
        "test_the_gate_judges_the_candidate_tree_and_not_its_own_address"]),
    ("bf5-range-key", "6d733d1e", BF, [
        "test_range_form_in_the_carrier_is_a_confirmation"]),
]


def sh(args, cwd=None, timeout=600):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def worktree(name, sha):
    p = os.path.join(SCRATCH, name)
    if os.path.isdir(p):
        sh(["git", "-C", MIRROR, "worktree", "remove", "--force", p])
    r = sh(["git", "-C", MIRROR, "worktree", "add", "--detach", p, sha])
    assert r.returncode == 0, r.stderr
    return p


def run_pytest(wt, test_file):
    t0 = time.time()
    r = sh([VENV_PY, "-m", "pytest", test_file, "-q", "--no-header", "-p", "no:cacheprovider"],
           cwd=wt, timeout=900)
    return r.returncode, r.stdout, time.time() - t0


def failed_ids(out):
    return set(re.findall(r"FAILED (?:[^:]+::)+(\w+)", out))


def validate(task_id, fix, test_file, targets):
    full = sh(["git", "-C", MIRROR, "rev-parse", fix]).stdout.strip()
    base = sh(["git", "-C", MIRROR, "rev-parse", fix + "^"]).stdout.strip()
    res = {"task": task_id, "fix": full[:8], "base": base[:8]}

    # base + test overlay -> RED with targets failing
    wt = worktree(task_id + "-base", base)
    ov = sh(["git", "-C", wt, "checkout", full, "--", test_file])
    assert ov.returncode == 0, ov.stderr
    rc, out, dt = run_pytest(wt, test_file)
    fails = failed_ids(out)
    res["base_rc"] = rc
    res["base_time_s"] = round(dt)
    res["base_targets_red"] = [t for t in targets if t in fails]
    res["base_all_fails_n"] = len(fails)
    res["base_ok"] = rc != 0 and len(res["base_targets_red"]) == len(targets)
    shutil_remove(wt)

    # fix worktree -> GREEN
    wt = worktree(task_id + "-fix", full)
    rc, out, dt = run_pytest(wt, test_file)
    res["fix_rc"] = rc
    res["fix_time_s"] = round(dt)
    res["fix_ok"] = rc == 0
    shutil_remove(wt)
    return res


def shutil_remove(wt):
    import shutil
    sh(["git", "-C", MIRROR, "worktree", "remove", "--force", wt])
    shutil.rmtree(wt, ignore_errors=True)


def main():
    os.makedirs(SCRATCH, exist_ok=True)
    results = []
    for task_id, fix, tf, targets in CANDS:
        print("== validating", task_id, fix, flush=True)
        try:
            r = validate(task_id, fix, tf, targets)
        except Exception as e:
            r = {"task": task_id, "error": repr(e)}
        results.append(r)
        print("  ", {k: v for k, v in r.items() if k != "task"}, flush=True)
    print("\n==== SUMMARY ====")
    for r in results:
        ok = r.get("base_ok") and r.get("fix_ok")
        print("  %-18s %s base_red=%s/%s fix_green=%s (%ss/%ss)" % (
            r["task"], "PASS" if ok else "FAIL",
            len(r.get("base_targets_red", [])), len(r.get("base_targets_red", []) or []) or "-",
            r.get("fix_ok"), r.get("base_time_s"), r.get("fix_time_s")))
    import json
    with open(os.path.join(BASE, "validation_report.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
