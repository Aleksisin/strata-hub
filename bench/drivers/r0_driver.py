r"""R0 flags driver v2 (race-free): unique log per run, explicit prefix and
task list, prod-identity check on restore (health alone can be satisfied
by the NEXT variant's server - the race that killed the first chain).

Usage: python -I r0_driver.py <variant> <prefix> [task ...]
  variant: r01 | r02 | r03m | r04
  prefix:  report prefix, e.g. r02x (reports: runs/<prefix>-<task>/)
  tasks:   default = d3-adr-erattum bf1-budget-tree bf4-gate-address
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
PY = r"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"
LLAMA = r"C:\Users\shogu\.unsloth\llama.cpp\build\bin\Release\llama-server.exe"
MDIR = r"C:\Users\shogu\Strata-Hub\models\Qwen3.8-27B-EfficientThink-NanoPlus-V3"
MODEL = MDIR + r"\Qwen3.8-27B-EfficientThink-VAL-APEX-I-NanoPlus-V3.gguf"
MMPROJ = MDIR + r"\mmproj-Qwen3.8-27B-Q4_K_M.gguf"
HEALTH = "http://127.0.0.1:8107/health"
KWTPL = '{"reasoning_effort":"%s"}'


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
    """Health is not enough: confirm the 8107 owner has NO variant flags."""
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" "
           "| Where-Object { $_.CommandLine -match '8107' } "
           "| ForEach-Object { $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True).stdout or ""
    out = out.lower()
    return ("--reasoning-budget" not in out
            and "--no-reasoning-preserve" not in out
            and "nanoplus" in out)


def variant_args(variant):
    if variant == "r01":
        return ["--no-reasoning-preserve"], KWTPL % "low"
    if variant == "r02":
        return ["--reasoning-budget", "16384"], KWTPL % "low"
    if variant == "r03m":
        return [], KWTPL % "medium"
    if variant == "r04":
        return ["--reasoning-budget", "16384"], KWTPL % "medium"
    raise SystemExit("unknown variant %s" % variant)


def restore(tag):
    log("restoring prod (%s)" % tag)
    subprocess.run(["schtasks", "/Run", "/TN", "StrataHub\\NP3-START"],
                   capture_output=True)
    for i in range(40):
        time.sleep(10)
        if health() and prod_identity_ok():
            log("prod 8107 restored with production identity (try %d)" % (i + 1))
            log("=== R0 DONE (%s) ===" % tag)
            return True
    log("=== R0 FAILED: prod did not come back with prod identity (%s) ==="
        % tag)
    return False


def main():
    variant = sys.argv[1]
    prefix = sys.argv[2]
    tasks = sys.argv[3:] or ["d3-adr-erattum", "bf1-budget-tree",
                             "bf4-gate-address"]
    extra, kwargs = variant_args(variant)
    try:
        return run_main(variant, prefix, tasks, extra, kwargs)
    except Exception as e:
        import traceback
        log("FATAL %r" % e)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        restore("fatal-%s" % prefix)
        return 3


def run_main(variant, prefix, tasks, extra, kwargs):
    global LOG
    LOG = os.path.join(LAUNCH, "chain-r0-%s.log" % prefix)
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("=== R0 %s (%s) start %s ===\n"
                % (variant, prefix, time.strftime("%Y-%m-%d %H:%M:%S")))
    proc = None
    pending = []
    try:
        log("prod alive before stop: %s" % health())
        kill_llama("stop-prod")
        time.sleep(5)
        args = [LLAMA, "-m", MODEL, "--mmproj", MMPROJ,
                "--host", "127.0.0.1", "--port", "8107",
                "-ngl", "99", "-c", "131072", "-np", "1",
                "-fa", "on", "-ctk", "q4_0", "-ctv", "q4_0",
                "--jinja", "--chat-template-kwargs", kwargs,
                "--temp", "0.6", "--top-p", "0.95", "--top-k", "20",
                "--min-p", "0.05", "--threads", "8"] + extra
        lf = open(os.path.join(LAUNCH, "nanoplus-v3-r0.log"), "w",
                  encoding="utf-8")
        proc = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)
        log("%s server Popen pid=%d extra=%r" % (variant, proc.pid, extra))
        if not wait_health(30, "%s server" % variant):
            proc.kill()
            restore("%s-boot-failed" % prefix)
            return 2
        raw = open(os.path.join(LAUNCH, "nanoplus-v3-r0.log"), encoding="utf-8",
                   errors="replace").read()
        log("boot fit-warning present: %s" % ("failed to fit" in raw))
        subprocess.run([PY, "-I", os.path.join(AGENTBENCH, "mkagentbench.py"),
                        "--prefix", prefix + "-"] + tasks,
                       capture_output=True, cwd=AGENTBENCH)
        log("queued %r with prefix %s-" % (tasks, prefix))
        reports = [os.path.join(AGENTBENCH, "runs", "%s-%s" % (prefix, t),
                                "report.json") for t in tasks]
        pending = list(reports)
        deadline = time.time() + 6 * 3600
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
    finally:
        kill_llama("restore")
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        time.sleep(5)
        restore("%s-complete" % prefix if not pending
                else "%s-partial-missing%r" % (prefix, pending))
    return 0


LOG = os.path.join(LAUNCH, "chain-r0.log")

if __name__ == "__main__":
    sys.exit(main())
