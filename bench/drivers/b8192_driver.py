r"""Budget 8192 experiment (operator order 07.11): run the two marathon
cases d1 + bf2 under --reasoning-budget 8192 instead of 16384.

Mechanism being tested: llama.cpp reasoning-budget sampler FORCES the
end-of-thinking sequence when the budget is exhausted and the request
CONTINUES without reasoning (no abort). d1 r02full failed err=model:
17 read-only rounds, then a 14313-token finish=stop monologue and the
answer.json was never written. Clipping reasoning at 8192 should force
content/tool_calls out of marathon rounds.

References (same exe, same runner, seed 42):
  abx5-bf2    FAIL budget (wall timeout 3053s, 14 rounds)  [no budget flag]
  r02full-d1  FAIL model  (0 edits, answer missing, r18 ctok 14313 stop)
  r02full-bf2 PASS 2580s / 20 rounds / auto_red=2            [budget 16384]

Verdict rule: bf2 must STAY green (regression = REJECT 8192);
d1 success = first-ever pass = strong GO; d1 fail again = keep 16384.

Usage: python -I b8192_driver.py [task ...]
Env: B8192_PREFIX (run-id prefix, default b8192 - MUST be new when
re-running, supervisor rejects duplicate run_id), B8192_LOG (chain log
name), B8192_DEADLINE_H (report wait hours, default 6).
Restores prod (NP3-START) with freeze-abx5b identity check.
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
LAUNCH = r"C:\Users\shogu\Strata-Hub\launch"
AGENTBENCH = r"C:\Users\shogu\Strata-Hub\agentbench"
RUNS = os.path.join(AGENTBENCH, "runs")
PY = r"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"
LLAMA = r"C:\Users\shogu\.unsloth\llama.cpp\build\bin\Release\llama-server.exe"
MDIR = r"C:\Users\shogu\Strata-Hub\models\Qwen3.8-27B-EfficientThink-NanoPlus-V3"
MODEL = MDIR + r"\Qwen3.8-27B-EfficientThink-VAL-APEX-I-NanoPlus-V3.gguf"
MMPROJ = MDIR + r"\mmproj-Qwen3.8-27B-Q4_K_M.gguf"
HEALTH = "http://127.0.0.1:8107/health"
PREFIX = os.environ.get("B8192_PREFIX", "b8192")
TASKS_DEFAULT = ["d1-compression-casualties", "bf2-red-not-green"]


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass


def health():
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as r:
            return b"ok" in r.read()
    except Exception:
        return False


def wait_health(max_tries, tag):
    for i in range(max_tries):
        if health():
            log("%s up (try %d)" % (tag, i + 1))
            return True
        time.sleep(10)
    log("FAILED: %s health not up" % tag)
    return False


def kill_llama(tag):
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
           "| Where-Object { $_.CommandLine -match 'NanoPlus-V3' } "
           "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                   capture_output=True)
    log("%s: llama kill signal sent" % tag)


def prod_identity_ok():
    """freeze-abx5b identity: unsloth exe, 131072 ctx, budget 16384."""
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
           "| Where-Object { $_.CommandLine -match '8107' } "
           "| ForEach-Object { $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True).stdout or ""
    out = out.lower()
    return (".unsloth" in out
            and "-c 131072" in out
            and "--reasoning-budget 16384" in out
            and "nanoplus" in out)


def wait_runner_idle():
    """No other agentbench runner may hold the GPU/queue while we swap
    the server (supervisor queue must be empty)."""
    for i in range(60):
        q = os.path.join(r"C:\Users\shogu\Strata-Hub\supervisor", "queue",
                         "pending")
        pend = [f for f in os.listdir(q)] if os.path.isdir(q) else []
        cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
               "| Where-Object { $_.CommandLine -match 'agentbench_runner' } "
               "| ForEach-Object { $_.ProcessId }")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True)
        pids = [x for x in (p.stdout or "").split() if x.strip().isdigit()]
        if not pend and not pids:
            log("queue empty, no runner running (try %d)" % (i + 1))
            return True
        time.sleep(30)
    log("runner/queue NOT idle after wait - aborting")
    return False


def restore(tag):
    log("restoring prod (%s)" % tag)
    subprocess.run(["schtasks", "/Run", "/TN", "StrataHub\\NP3-START"],
                   capture_output=True)
    for i in range(40):
        time.sleep(10)
        if health() and prod_identity_ok():
            log("prod 8107 restored with production identity (try %d)" % (i + 1))
            log("=== B8192 DONE (%s) ===" % tag)
            return True
    log("=== B8192 FAILED: prod did not come back with prod identity (%s) ==="
        % tag)
    return False


def summarize(prefix, tasks):
    """Per-task verdict + marathon/cap-fire indicators from transcripts."""
    for t in tasks:
        rp = os.path.join(RUNS, "%s-%s" % (prefix, t), "report.json")
        tp = os.path.join(RUNS, "%s-%s" % (prefix, t), "transcript.jsonl")
        try:
            r = json.load(open(rp, encoding="utf-8"))
        except Exception:
            log("  %-24s NO/UNREADABLE REPORT" % t)
            continue
        log("  %-24s success=%-5s err=%-10s wall=%-7s rounds=%-3s "
            "edits=%-2s auto_red=%s"
            % (t, r.get("success"), r.get("error_class"),
               str(r.get("wall_s")), r.get("rounds"), r.get("edits_count"),
               r.get("auto_red_count", "n/a")))
        try:
            comps = []
            fins = {}
            for line in open(tp, encoding="utf-8", errors="replace"):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                u = rec.get("usage") or {}
                if u.get("completion_tokens") is not None:
                    comps.append(u["completion_tokens"])
                fr = rec.get("finish")
                if fr:
                    fins[fr] = fins.get(fr, 0) + 1
            if comps:
                over = [c for c in comps if c > 8192]
                log("    ctok: n=%d max=%s sum=%s >8192: %s finish=%s"
                    % (len(comps), max(comps), sum(comps), over, fins))
        except Exception:
            pass


def main():
    global LOG
    LOG = os.path.join(LAUNCH,
                       os.environ.get("B8192_LOG", "chain-b8192.log"))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("=== B8192 EXPERIMENT start %s ===\n"
                % time.strftime("%Y-%m-%d %H:%M:%S"))
    tasks = sys.argv[1:] or list(TASKS_DEFAULT)
    proc = None
    pending = []
    try:
        if not wait_runner_idle():
            return 2
        log("prod alive before stop: %s" % health())
        kill_llama("stop-prod")
        time.sleep(5)
        args = [LLAMA, "-m", MODEL, "--mmproj", MMPROJ,
                "--host", "127.0.0.1", "--port", "8107",
                "-ngl", "99", "-c", "131072", "-np", "1",
                "-fa", "on", "-ctk", "q4_0", "-ctv", "q4_0",
                "--reasoning-budget", "8192",
                "--jinja", "--chat-template-kwargs",
                '{"reasoning_effort":"low"}',
                "--temp", "0.6", "--top-p", "0.95", "--top-k", "20",
                "--min-p", "0.05", "--threads", "8", "--metrics"]
        lf = open(os.path.join(LAUNCH, "nanoplus-v3-b8192.log"), "w",
                  encoding="utf-8")
        proc = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)
        log("b8192 server Popen pid=%d budget=8192" % proc.pid)
        if not wait_health(30, "b8192 server"):
            proc.kill()
            restore("b8192-boot-failed")
            return 2
        raw = open(os.path.join(LAUNCH, "nanoplus-v3-b8192.log"),
                   encoding="utf-8", errors="replace").read()
        log("boot fit-warning present: %s" % ("failed to fit" in raw))
        subprocess.run([PY, "-I", os.path.join(AGENTBENCH, "mkagentbench.py"),
                        "--prefix", PREFIX + "-"] + tasks,
                       capture_output=True, cwd=AGENTBENCH)
        log("queued %r with prefix %s-" % (tasks, PREFIX))
        reports = [os.path.join(RUNS, "%s-%s" % (PREFIX, t), "report.json")
                   for t in tasks]
        pending = list(reports)
        deadline = time.time() + \
            float(os.environ.get("B8192_DEADLINE_H", "6")) * 3600
        while pending and time.time() < deadline:
            time.sleep(60)
            for rp in list(pending):
                if os.path.isfile(rp):
                    try:
                        json.load(open(rp, encoding="utf-8"))
                        pending.remove(rp)
                        log("report done: %s" % os.path.basename(rp))
                    except Exception:
                        pass
        log("reports done: %d/%d (missing: %r)"
            % (len(tasks) - len(pending), len(tasks), pending))
        summarize(PREFIX, tasks)
    finally:
        kill_llama("restore")
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        time.sleep(5)
        restore("%s-complete" % PREFIX if not pending
                else "%s-partial-missing%r" % (PREFIX, pending))
    return 0


LOG = os.path.join(LAUNCH,
                   os.environ.get("B8192_LOG", "chain-b8192.log"))

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("FATAL %r\n" % e)
            f.write(traceback.format_exc())
        sys.exit(3)
