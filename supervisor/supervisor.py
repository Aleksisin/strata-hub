r"""Strata Supervisor v1 — single-owner GPU job queue with artifact validation.

Implements TASK-SUPERVISOR-2026-11-04.md (operator-approved spec):
  - file queue: queue/pending/*.json -> (atomic rename) -> queue/active/, FIFO by mtime
  - unique run_id + run_dir created BEFORE the process starts (no move step)
  - DONE only after artifact validation (never on exit code)
  - per-run Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE): TerminateJobObject kills
    exactly that run's process tree - never taskkill by image name
  - timeout_s -> TerminateJobObject + FAILED_TIMEOUT (artifacts still validated)
  - operator kill: drop state/kill/<run_id>.flag -> killed, NEVER DONE
  - recovery on restart: state/<run_id>.json reconciled with disk (§6 of spec)
  - manifest with sha256 of actual files, written before start, results appended after
  - no auto-retry; duplicate run_id rejected; single instance lock; heartbeat file

Launch: Task Scheduler task StrataHub\SUPERVISOR (session 0, past-date trigger).
Stop:   create state/stop.flag; hard stop = schtasks /End + taskkill by stored PID.
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import msvcrt
import os
import shutil
import subprocess
import sys
import time
import traceback

BASE = r"C:\Users\shogu\Strata-Hub\supervisor"
QUEUE = os.path.join(BASE, "queue")
PENDING = os.path.join(QUEUE, "pending")
ACTIVE = os.path.join(QUEUE, "active")
REJECTED = os.path.join(QUEUE, "rejected")
STATE = os.path.join(BASE, "state")
LOGS = os.path.join(BASE, "logs")
KILLDIR = os.path.join(STATE, "kill")
STOP_FLAG = os.path.join(STATE, "stop.flag")
LOCK_FILE = os.path.join(STATE, "supervisor.lock")
HEARTBEAT = os.path.join(STATE, "supervisor.json")
SUPERVISOR_LOG = os.path.join(LOGS, "supervisor.log")

VALIDATOR_KINDS = {"peakstone59", "dummy", "agentbench_task"}

# ---------------- Job Objects (ctypes, no pywin32) ----------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# explicit signatures: HANDLE-returning functions truncate via default c_int restype
H = wt.HANDLE
kernel32.CreateJobObjectW.restype = H
kernel32.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPWSTR]
kernel32.SetInformationJobObject.restype = wt.BOOL
kernel32.SetInformationJobObject.argtypes = [H, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
kernel32.AssignProcessToJobObject.restype = wt.BOOL
kernel32.AssignProcessToJobObject.argtypes = [H, H]
kernel32.TerminateJobObject.restype = wt.BOOL
kernel32.TerminateJobObject.argtypes = [H, wt.DWORD]
kernel32.CreateProcessW.restype = wt.BOOL
kernel32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p,
                                    ctypes.c_void_p, wt.BOOL, wt.DWORD,
                                    ctypes.c_void_p, wt.LPCWSTR,
                                    ctypes.c_void_p, ctypes.c_void_p]
kernel32.ResumeThread.restype = wt.DWORD
kernel32.ResumeThread.argtypes = [H]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.TerminateProcess.argtypes = [H, wt.UINT]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForSingleObject.argtypes = [H, wt.DWORD]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [H]
kernel32.OpenProcess.restype = H
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.GetCurrentProcess.restype = H
kernel32.GetCurrentProcess.argtypes = []
kernel32.SetHandleInformation.restype = wt.BOOL
kernel32.SetHandleInformation.argtypes = [H, wt.DWORD, wt.DWORD]
kernel32.CreateFileW.restype = H
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, H]

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
DUPLICATE_SAME_ACCESS = 2
THREAD_SUSPEND_RESUME = 0x0002
PROCESS_SYNCHRONIZE = 0x00100000
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
                ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


def job_create_kill_on_close():
    h = kernel32.CreateJobObjectW(None, None)
    if not h:
        raise OSError("CreateJobObjectW failed: %s" % ctypes.get_last_error())
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(h, JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError("SetInformationJobObject failed: %s" % ctypes.get_last_error())
    return h


def job_assign(hjob, hproc):
    if not kernel32.AssignProcessToJobObject(hjob, hproc):
        raise OSError("AssignProcessToJobObject failed: %s" % ctypes.get_last_error())


def job_terminate(hjob, code=1):
    kernel32.TerminateJobObject(hjob, code)


def spawn_in_job(cmdline, cwd, env, hjob, log_fd_in, log_fd_out, log_fd_err):
    """CREATE_SUSPENDED -> assign to job -> resume. Returns (hProcess, pid).
    env: dict -> UTF-16 environment block; None -> inherit."""
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.dwFlags = STARTF_USESTDHANDLES
    si.hStdInput = log_fd_in
    si.hStdOutput = log_fd_out
    si.hStdError = log_fd_err
    flags = CREATE_SUSPENDED | CREATE_NO_WINDOW
    env_block = None
    if env:
        flags |= 0x00000400  # CREATE_UNICODE_ENVIRONMENT
        env_block = ctypes.c_wchar_p(
            "".join("%s=%s\0" % (k, v) for k, v in env.items()) + "\0")
    pi = PROCESS_INFORMATION()
    cmd_buf = ctypes.create_unicode_buffer(cmdline)
    cwd_buf = ctypes.c_wchar_p(cwd) if cwd else None
    ok = kernel32.CreateProcessW(None, cmd_buf, None, None, True, flags,
                                 env_block, cwd_buf, ctypes.byref(si),
                                 ctypes.byref(pi))
    if not ok:
        raise OSError("CreateProcessW failed: %s" % ctypes.get_last_error())
    try:
        job_assign(hjob, pi.hProcess)
    except OSError:
        kernel32.TerminateProcess(pi.hProcess, 1)
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)
        raise
    kernel32.ResumeThread(pi.hThread)
    kernel32.CloseHandle(pi.hThread)
    return pi.hProcess, pi.dwProcessId


def make_inheritable(handle):
    kernel32.SetHandleInformation(handle, 1, 1)  # HANDLE_FLAG_INHERIT
    return handle


def pid_alive(pid):
    if not pid:
        return False
    h = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not h:
        return False
    try:
        return kernel32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(h)


def wait_handle(h, ms):
    return kernel32.WaitForSingleObject(h, ms)


# ---------------- small utils ----------------

def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Log:
    """Line-buffered supervisor log."""

    def __init__(self, path):
        self.f = open(path, "a", buffering=1, encoding="utf-8", errors="replace")

    def w(self, msg):
        self.f.write("[%s] %s\n" % (now_utc(), msg))
        self.f.flush()


# ---------------- validators ----------------

def validate_peakstone59(job, run_dir):
    """results.json: exact N rows, ids 1:1 with ids_file, no dupes, numeric
    final_score each; bundle.json present and signed (bundle_hash non-empty)."""
    v = job.get("validate", {})
    expected = int(v.get("expected_ids", 59))
    ids_file = v.get("ids_file")
    need_bundle = bool(v.get("bundle_signature"))
    res_path = os.path.join(run_dir, "results.json")
    det = {"artifact_present": False}
    if not os.path.isfile(res_path):
        return False, "results.json missing", det
    det["artifact_present"] = True
    try:
        data = read_json(res_path)
    except Exception as e:
        return False, "results.json unparsable: %s" % e, det
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return False, "results.json has no results list", det
    det["n_results"] = len(rows)
    ids = []
    for r in rows:
        if not isinstance(r, dict) or "challenge" not in r:
            return False, "row without challenge id", det
        if not isinstance(r.get("final_score"), (int, float)):
            return False, "row %s: final_score not numeric" % r.get("challenge"), det
        ids.append(r["challenge"])
    if len(ids) != expected:
        return False, "expected %d results, got %d" % (expected, len(ids)), det
    if len(set(ids)) != len(ids):
        return False, "duplicate challenge ids", det
    if ids_file:
        ref = [l.strip() for l in open(ids_file, encoding="utf-8") if l.strip()]
        if set(ref) != set(ids):
            missing = sorted(set(ref) - set(ids))[:5]
            extra = sorted(set(ids) - set(ref))[:5]
            return False, "ids not 1:1 (missing %s, extra %s)" % (missing, extra), det
    if need_bundle:
        bpath = os.path.join(run_dir, "bundle.json")
        if not os.path.isfile(bpath):
            return False, "bundle.json missing", det
        try:
            b = read_json(bpath)
        except Exception as e:
            return False, "bundle.json unparsable: %s" % e, det
        if not b.get("bundle_hash"):
            return False, "bundle not signed (bundle_hash empty)", det
        det["bundle_hash16"] = str(b.get("bundle_hash"))[:16]
    return True, "ok", det


def validate_dummy(job, run_dir):
    """Test validator: out/result.json with {"ok": true, "n": expected_ids}."""
    v = job.get("validate", {})
    expected = int(v.get("expected_ids", 1))
    p = os.path.join(run_dir, "out", "result.json")
    det = {"artifact_present": False}
    if not os.path.isfile(p):
        return False, "out/result.json missing", det
    det["artifact_present"] = True
    try:
        d = read_json(p)
    except Exception as e:
        return False, "out/result.json unparsable: %s" % e, det
    if d.get("ok") is not True:
        return False, "ok != true", det
    if int(d.get("n", -1)) != expected:
        return False, "n %r != expected %d" % (d.get("n"), expected), det
    return True, "ok", det


def validate_agentbench_task(job, run_dir):
    """AgentBench result: report.json complete (success is the BENCHMARK
    outcome, not the job outcome - a failed agent task with a complete,
    well-formed report is a valid job)."""
    det = {"artifact_present": False}
    p = os.path.join(run_dir, "report.json")
    if not os.path.isfile(p):
        return False, "report.json missing", det
    det["artifact_present"] = True
    try:
        r = read_json(p)
    except Exception as e:
        return False, "report.json unparsable: %s" % e, det
    if not isinstance(r.get("task"), str) or not r.get("task"):
        return False, "report.task missing", det
    if not isinstance(r.get("success"), bool):
        return False, "report.success not bool", det
    if r.get("error_class") not in ("none", "model", "tool", "infra", "budget",
                                    "no_progress"):
        return False, "report.error_class invalid: %r" % r.get("error_class"), det
    if not isinstance(r.get("wall_s"), (int, float)):
        return False, "report.wall_s missing", det
    if not os.path.isfile(os.path.join(run_dir, "transcript.jsonl")):
        return False, "transcript.jsonl missing", det
    det.update({"bench_success": r["success"], "bench_class": r["error_class"]})
    return True, "report complete", det


VALIDATORS = {"peakstone59": validate_peakstone59, "dummy": validate_dummy,
              "agentbench_task": validate_agentbench_task}


def validate_artifacts(job):
    run_dir = job["run_dir"]
    kind = job.get("validate", {}).get("kind")
    fn = VALIDATORS.get(kind)
    if fn is None:
        return False, "unknown validator kind %r" % kind, {}
    try:
        return fn(job, run_dir)
    except Exception as e:
        return False, "validator crashed: %s" % e, {}


# ---------------- manifest ----------------

def build_manifest(job):
    m = job.get("manifest") or {}

    def hash_of(path):
        if path and os.path.isfile(path):
            try:
                return sha256_file(path)
            except Exception:
                return None
        return None

    out = {
        "run_id": job.get("run_id"),
        "created_utc": now_utc(),
        "kind": job.get("kind"),
        "model": {"name": (m.get("model") or {}).get("name"),
                  "gguf_sha256": hash_of((m.get("model") or {}).get("gguf_path"))},
        "engine": {"name": (m.get("engine") or {}).get("name"),
                   "port": (m.get("engine") or {}).get("port"),
                   "binary_sha256": hash_of((m.get("engine") or {}).get("binary_path"))},
        "inference": m.get("inference") or {},
        "tools": {"profile": (m.get("tools") or {}).get("profile"),
                  "gateway_sha256": hash_of((m.get("tools") or {}).get("gateway_path")),
                  "allowlist_sha256": hash_of((m.get("tools") or {}).get("allowlist_path"))},
        "benchmark": {"suite_sha256": hash_of((m.get("benchmark") or {}).get("suite_path")),
                      "ids_file": (m.get("benchmark") or {}).get("ids_file")},
    }
    return out


def append_results_to_manifest(run_dir, n, score, ctok0, wall_s, status, validated):
    p = os.path.join(run_dir, "manifest.json")
    try:
        m = read_json(p) if os.path.isfile(p) else {}
    except Exception:
        m = {}
    m["results"] = {"score": score, "n_challenges": n,
                    "transport_errors_ctok0": ctok0, "tool_errors": None,
                    "wall_s": round(wall_s, 1), "status": status,
                    "validated": bool(validated)}
    m["finished_utc"] = now_utc()
    write_json(p, m)


def summarize_results(run_dir):
    """(n, sum(final_score), ctok0_count) or (None, None, None)."""
    p = os.path.join(run_dir, "results.json")
    if not os.path.isfile(p):
        return None, None, None
    try:
        data = read_json(p)
    except Exception:
        return None, None, None
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None, None, None
    score = 0.0
    ctok0 = 0
    for r in rows:
        s = r.get("final_score")
        if isinstance(s, (int, float)):
            score += s
        if not r.get("completion_tokens") and not s:
            ctok0 += 1
    return len(rows), round(score, 2), ctok0


# ---------------- supervisor ----------------

class Supervisor:
    def __init__(self):
        self.log = Log(SUPERVISOR_LOG)
        self.job = None          # current run's job object
        self.proc_h = None       # current run's process handle
        self.current = None      # job dict
        self.started_monotonic = 0.0

    # --- instance lock ---
    def acquire_lock(self):
        if os.path.isfile(LOCK_FILE):
            try:
                old = read_json(LOCK_FILE)
                if pid_alive(old.get("pid")):
                    self.log.w("REFUSED: another supervisor (pid %s) is alive" % old.get("pid"))
                    return False
                self.log.w("stale lock from pid %s - taking over" % old.get("pid"))
            except Exception:
                self.log.w("unreadable lock - taking over")
        write_json(LOCK_FILE, {"pid": os.getpid(), "started_utc": now_utc()})
        return True

    # --- heartbeat ---
    def heartbeat(self):
        write_json(HEARTBEAT, {
            "pid": os.getpid(),
            "pulse_utc": now_utc(),
            "current_run": self.current["run_id"] if self.current else None,
            "status": "running" if self.current else "idle",
        })

    # --- admission ---
    def admit(self, path):
        name = os.path.basename(path)
        try:
            job = read_json(path)
        except Exception as e:
            shutil.move(path, os.path.join(REJECTED, name))
            self.log.w("REJECTED %s: unparsable JSON (%s)" % (name, e))
            return
        run_id = job.get("run_id")
        problems = []
        if not run_id or not isinstance(run_id, str):
            problems.append("run_id missing")
        elif run_id != name[:-5]:
            problems.append("run_id != filename stem")
        elif os.path.isfile(os.path.join(STATE, run_id + ".json")) or \
                os.path.isfile(os.path.join(ACTIVE, run_id + ".json")) or \
                os.path.isfile(os.path.join(REJECTED, run_id + ".json")):
            problems.append("duplicate run_id (already seen)")
        if job.get("kind") not in ("peakstone", "dummy", "agentbench"):
            problems.append("kind must be peakstone|dummy|agentbench")
        for field in ("cmd", "workdir", "run_dir"):
            if not job.get(field):
                problems.append("%s missing" % field)
        if job.get("cmd") and (not isinstance(job["cmd"], list) or
                               not all(isinstance(a, str) for a in job["cmd"])):
            problems.append("cmd must be list of strings")
        vk = (job.get("validate") or {}).get("kind")
        if vk not in VALIDATOR_KINDS:
            problems.append("validate.kind must be one of %s" % sorted(VALIDATOR_KINDS))
        ts = job.get("timeout_s")
        if ts is not None and (not isinstance(ts, (int, float)) or ts <= 0):
            problems.append("timeout_s must be positive number")
        if problems:
            job["_rejected"] = problems
            write_json(os.path.join(REJECTED, name), job)
            os.remove(path) if os.path.isfile(path) else None
            self.log.w("REJECTED %s: %s" % (name, "; ".join(problems)))
            return
        os.replace(path, os.path.join(ACTIVE, name))
        self.log.w("admitted %s (queued)" % run_id)
        self.start(job)

    def pick_next(self):
        if not os.path.isdir(PENDING):
            return None
        cands = [os.path.join(PENDING, f) for f in os.listdir(PENDING)
                 if f.endswith(".json")]
        if not cands:
            return None
        return min(cands, key=os.path.getmtime)

    # --- start run ---
    def start(self, job):
        run_id = job["run_id"]
        os.makedirs(job["run_dir"], exist_ok=True)
        os.makedirs(LOGS, exist_ok=True)
        write_json(os.path.join(STATE, run_id + ".json"),
                   {"run_id": run_id, "status": "RUNNING", "pid": None,
                    "started_utc": now_utc(), "finished_utc": None,
                    "validation": None})
        man = build_manifest(job)
        write_json(os.path.join(job["run_dir"], "manifest.json"), man)
        log_path = os.path.join(LOGS, run_id + ".log")
        fh = open(log_path, "ab")
        # NUL stdin: share-all + inheritable, otherwise every grandchild
        # spawn inside the job dies with WinError 6 (invalid std handle)
        h_in = kernel32.CreateFileW("NUL", 0x80000000 | 0x40000000,
                                    0x00000001 | 0x00000002, None, 3, 0, None)
        if h_in:
            make_inheritable(h_in)
        h_out = make_inheritable(msvcrt.get_osfhandle(fh.fileno()))
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        cmdline = subprocess.list2cmdline(job["cmd"])
        hjob = job_create_kill_on_close()
        try:
            hproc, pid = spawn_in_job(cmdline, job["workdir"], env, hjob,
                                      h_in, h_out, h_out)
        except Exception as e:
            kernel32.CloseHandle(hjob)
            fh.close()
            self.log.w("START FAILED %s: %s" % (run_id, e))
            st = read_json(os.path.join(STATE, run_id + ".json"))
            st.update({"status": "FAILED_INVALID", "finished_utc": now_utc(),
                       "validation": {"ok": False, "detail": "spawn failed: %s" % e}})
            write_json(os.path.join(STATE, run_id + ".json"), st)
            os.remove(os.path.join(ACTIVE, run_id + ".json"))
            return
        self.job = hjob
        self.proc_h = hproc
        self.current = job
        self.started_monotonic = time.monotonic()
        st = read_json(os.path.join(STATE, run_id + ".json"))
        st["pid"] = pid
        st["log"] = log_path
        write_json(os.path.join(STATE, run_id + ".json"), st)
        self.log.w("RUNNING %s pid=%s (timeout_s=%s)" % (run_id, pid, job.get("timeout_s")))

    # --- finish run ---
    def finalize(self, killed_reason=None):
        job = self.current
        run_id = job["run_id"]
        timeout = job.get("timeout_s")
        elapsed = time.monotonic() - self.started_monotonic
        timed_out = (killed_reason is None and timeout is not None
                     and elapsed > float(timeout))
        if timed_out:
            if self.job:
                job_terminate(self.job, 1)
            elif self.proc_h:
                kernel32.TerminateProcess(self.proc_h, 1)
            if self.proc_h:
                wait_handle(self.proc_h, 15000)
            killed_reason = "timeout"
        st_path = os.path.join(STATE, run_id + ".json")
        st = read_json(st_path)
        ok, detail, vdet = validate_artifacts(job)
        n, score, ctok0 = summarize_results(job["run_dir"])
        artifact_present = bool(vdet.get("artifact_present")) or \
            (n is not None and n > 0)

        if killed_reason == "timeout":
            status = "FAILED_TIMEOUT"
        elif killed_reason == "killed":
            # operator kill must never yield DONE (spec test 2)
            status = "FAILED_INVALID" if artifact_present else "INTERRUPTED"
        elif ok:
            status = "DONE"
        elif artifact_present:
            status = "FAILED_INVALID"
        else:
            status = "INTERRUPTED"
        st.update({"status": status, "finished_utc": now_utc(),
                   "wall_s": round(elapsed, 1),
                   "validation": {"ok": bool(ok), "detail": detail, **vdet}})
        if st.get("pid"):
            st["exit_alive_check"] = pid_alive(st["pid"])
        write_json(st_path, st)
        append_results_to_manifest(job["run_dir"], n, score, ctok0, elapsed,
                                   status, ok)
        self.log.w("%s %s: validation=%s (%s) n=%s score=%s wall=%.0fs" %
                   (status, run_id, ok, detail, n, score, elapsed))
        # cleanup handles
        if self.job:
            kernel32.CloseHandle(self.job)
        if self.proc_h:
            kernel32.CloseHandle(self.proc_h)
        self.job = self.proc_h = None
        self.current = None
        active_file = os.path.join(ACTIVE, run_id + ".json")
        if os.path.isfile(active_file):
            os.remove(active_file)

    # --- recovery (§6) ---
    def recover(self):
        # active files without a RUNNING state -> reconcile
        if os.path.isdir(ACTIVE):
            for f in sorted(os.listdir(ACTIVE)):
                if not f.endswith(".json"):
                    continue
                path = os.path.join(ACTIVE, f)
                try:
                    job = read_json(path)
                except Exception:
                    shutil.move(path, os.path.join(REJECTED, f))
                    self.log.w("recovery: unparsable active file %s -> rejected" % f)
                    continue
                run_id = job.get("run_id", f[:-5])
                st_path = os.path.join(STATE, run_id + ".json")
                if os.path.isfile(st_path):
                    st = read_json(st_path)
                else:
                    st = {"run_id": run_id, "status": "RUNNING", "pid": None,
                          "started_utc": None, "finished_utc": None,
                          "validation": None}
                    write_json(st_path, st)
                if st.get("status") == "RUNNING":
                    alive = pid_alive(st.get("pid"))
                    h = None
                    if alive and st.get("pid"):
                        h = kernel32.OpenProcess(SYNCHRONIZE, False, int(st["pid"]))
                    if h:
                        self.log.w("recovery: %s RUNNING pid=%s alive -> re-attach"
                                   % (run_id, st.get("pid")))
                        self.current = job
                        self.proc_h = h
                        self.job = None  # foreign job: no Terminate available
                        self.started_monotonic = time.monotonic()  # fresh wall clock
                        return True
                    self.log.w("recovery: %s process dead -> validating artifacts" % run_id)
                    self.current = job
                    self.job = None
                    self.proc_h = None
                    # finalize-without-handles path
                    st2 = read_json(st_path)
                    ok, detail, vdet = validate_artifacts(job)
                    n, score, ctok0 = summarize_results(job["run_dir"])
                    artifact_present = bool(vdet.get("artifact_present")) or \
                        (n is not None and n > 0)
                    if ok:
                        status = "DONE"
                    elif artifact_present:
                        status = "FAILED_INVALID"
                    else:
                        status = "INTERRUPTED"
                    st2.update({"status": status, "finished_utc": now_utc(),
                                "validation": {"ok": bool(ok), "detail": detail, **vdet},
                                "recovered": True})
                    write_json(st_path, st2)
                    self.log.w("recovery: %s -> %s (%s)" % (run_id, status, detail))
                    self.current = None  # loop must resume admitting new jobs
                    os.remove(path)
                else:
                    # terminal state already recorded; active file is a leftover
                    self.log.w("recovery: %s already %s - dropping active file"
                               % (run_id, st.get("status")))
                    os.remove(path)
        return False

    # --- main loop ---
    def run(self):
        if not self.acquire_lock():
            return 2
        myjob = job_create_kill_on_close()
        job_assign(myjob, kernel32.GetCurrentProcess())
        self.log.w("supervisor v1 started pid=%s" % os.getpid())
        reattached = self.recover()
        if reattached:
            self.log.w("re-attached run continues below")
        try:
            while True:
                self.heartbeat()
                if os.path.isfile(STOP_FLAG):
                    self.log.w("stop.flag seen - exiting")
                    break
                if self.current is None:
                    nxt = self.pick_next()
                    if nxt:
                        self.admit(nxt)
                # current run bookkeeping
                if self.current is not None:
                    # operator kill flag?
                    kf = os.path.join(KILLDIR, self.current["run_id"] + ".flag")
                    if os.path.isfile(kf):
                        os.remove(kf)
                        self.log.w("kill flag for %s - TerminateJobObject" %
                                   self.current["run_id"])
                        if self.job:
                            job_terminate(self.job, 1)
                        elif self.proc_h:
                            kernel32.TerminateProcess(self.proc_h, 1)
                        if self.proc_h:
                            wait_handle(self.proc_h, 15000)
                        self.finalize(killed_reason="killed")
                        continue
                    if self.proc_h is not None:
                        rc = wait_handle(self.proc_h, 1000)
                        if rc == WAIT_OBJECT_0 or rc != WAIT_TIMEOUT:
                            self.finalize()
                            continue
                        timeout = self.current.get("timeout_s")
                        if timeout is not None and \
                                time.monotonic() - self.started_monotonic > float(timeout):
                            self.log.w("timeout %s after %.0fs" %
                                       (self.current["run_id"], timeout))
                            self.finalize(killed_reason="timeout")
                            continue
                time.sleep(0.5)
        except Exception:
            self.log.w("FATAL:\n" + traceback.format_exc())
            raise
        finally:
            self.heartbeat()
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass
            self.log.w("supervisor exited")
        return 0


if __name__ == "__main__":
    os.makedirs(PENDING, exist_ok=True)
    os.makedirs(ACTIVE, exist_ok=True)
    os.makedirs(REJECTED, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    os.makedirs(KILLDIR, exist_ok=True)
    sys.exit(Supervisor().run())
