"""Validate integration-task machinery with reference solutions (no GPU).

i1: reference guard (ref/test_guard_scan_window_ref.py) must pass step1
    (clean) and step2 (must FAIL under the scan_window mutation).
i2: reference = e825fc32's test version applied to base e825fc32^ must pass
    step1 (clean), step2 (state.md item removal), and step3 (module mutation
    must FAIL).
"""
import io
import json
import os
import shutil
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.realpath(__file__))
MIRROR = os.path.join(HERE, "mirror")
SCRATCH = os.path.join(HERE, "scratch")
VENV_PY = r"C:\IBF\DiagnosticPipeline\.venv\Scripts\python.exe"


def sh(args, cwd=None, timeout=900):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def prep(task, name):
    copy = os.path.join(SCRATCH, name)
    sh(["git", "-C", MIRROR, "worktree", "remove", "--force", copy])
    subprocess.run(["git", "-C", MIRROR, "worktree", "prune"],
                   capture_output=True)
    r = sh(["git", "-C", MIRROR, "worktree", "add", "--detach", copy,
            task["base"]])
    assert r.returncode == 0, r.stderr
    return copy


def run_guard(copy, guard):
    r = sh([VENV_PY, "-m", "pytest", guard, "-q", "--no-header",
            "-p", "no:cacheprovider"], cwd=copy, timeout=900)
    return r.returncode, (r.stdout or "")[-800:]


def step(task, copy, guard, step_spec):
    from agentbench_runner import _apply_mutation, _revert_file
    if "mutate" in step_spec:
        if not _apply_mutation(copy, step_spec["mutate"]):
            return None, "mutation anchor missing"
    rc, out = run_guard(copy, guard)
    if "mutate" in step_spec:
        _revert_file(copy, step_spec["mutate"]["file"])
    expect = step_spec["run"]
    if expect == "must_pass":
        return rc == 0, ("green" if rc == 0 else "RED:\n" + out)
    return rc != 0, ("red (caught)" if rc != 0 else "green (mutation SURVIVED)")


def validate_i1():
    task = json.load(open(os.path.join(HERE, "tasks", "i1-guard-scan-window.json"),
                          encoding="utf-8"))
    copy = prep(task, "mach-i1")
    ref = os.path.join(HERE, "ref", "test_guard_scan_window_ref.py")
    shutil.copy(ref, os.path.join(copy, task["guard_test_file"]))
    for i, s in enumerate(task["verify_steps"]):
        ok, detail = step(task, copy, task["guard_test_file"], s)
        print("i1 step%d %s: %s" % (i, "OK" if ok else "FAIL", detail[:300]))
        if not ok:
            return False
    sh(["git", "-C", MIRROR, "worktree", "remove", "--force", copy])
    return True


def validate_i2():
    task = json.load(open(os.path.join(HERE, "tasks", "i2-tail-registry-synthetic.json"),
                          encoding="utf-8"))
    copy = prep(task, "mach-i2")
    # reference solution = the real refactor of the test (e825fc32)
    r = sh(["git", "-C", copy, "checkout", task["overlay_commit"].replace("^", ""),
            "--", task["guard_test_file"]])
    assert r.returncode == 0, r.stderr
    for i, s in enumerate(task["verify_steps"]):
        ok, detail = step(task, copy, task["guard_test_file"], s)
        print("i2 step%d %s: %s" % (i, "OK" if ok else "FAIL", detail[:300]))
        if not ok:
            return False
    sh(["git", "-C", MIRROR, "worktree", "remove", "--force", copy])
    return True


def main():
    r1 = validate_i1()
    r2 = validate_i2()
    print("\n==== INTEGRATION MACHINERY: i1=%s i2=%s ====" %
          ("PASS" if r1 else "FAIL", "PASS" if r2 else "FAIL"))


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
