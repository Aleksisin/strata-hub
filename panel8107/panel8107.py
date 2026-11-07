r"""panel8107 — monitoring dashboard backend for the production llama-server
(b11985-mix, NanoPlus V3, port 8107). Serves panel8107.html and one API
endpoint /api/state; polls every 2 s:
  8107 /health /props /slots /metrics   (native llama.cpp endpoints)
  nvidia-smi                            (VRAM / temp / clocks / power)
  Win32_Process CreationDate            (engine PID + uptime, cached)

Live tok/s computed by DIFFING /metrics counters between polls. Request
log derived from slot id_task transitions (llama.cpp keeps no request
history). Keeps 240 samples of history + last 100 request events.
No proxy, no prod changes: pure read-only observation.

Port 8091. Run: python -I panel8107.py  (never exits; kill to stop)
"""
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = r"C:\Users\shogu\Strata-Hub\panel8107"
ENGINE = "http://127.0.0.1:8107"
PORT = 8091
POLL_S = 2.0
HIST_MAX = 240          # 8 minutes at 2 s
EVENTS_MAX = 100
# Long-term journal (operator review 07.11: the 8-min in-memory history
# is not enough to analyse degradation over 20-50 min). One JSONL file
# per day per stream: samples-YYYY-MM-DD.jsonl (every poll) and
# events-YYYY-MM-DD.jsonl (request open/close from id_task transitions).
JDIR = os.path.join(HERE, "journal")
try:
    os.makedirs(JDIR, exist_ok=True)
except Exception:
    pass
J_SZ = {"samples": 0, "events": 0}
J_ERR = {"once": False}

LOCK = threading.Lock()
STATE = {
    "ts": None, "health": False, "model": None, "n_ctx": None,
    "engine_pid": None, "engine_started": None,
    "slot": {}, "params": {}, "counters": {}, "live": {},
    "gpu": {}, "events": [], "hist": [],
    "last_error": None, "polls": 0,
}
PREV = {"counters": {}, "ts": None}
CUR_TASK = None          # task id of the open request event
LAST_SEEN = None         # last observed slot id_task (dedup baseline)


def log(msg):
    line = time.strftime("[%m-%d %H:%M:%S] ") + msg
    print(line, flush=True)


def fetch(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def jpath(kind):
    return os.path.join(JDIR, "%s-%s.jsonl"
                        % (kind, time.strftime("%Y-%m-%d")))


def jlog(kind, obj):
    """Append one record to the long-term journal (per-day JSONL)."""
    try:
        p = jpath(kind)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        J_SZ[kind] = os.path.getsize(p)
    except Exception as e:
        if not J_ERR["once"]:
            log("journal write error: %r" % e)
            J_ERR["once"] = True


def engine_uptime(pid):
    """Engine start time via Win32_Process (cached per pid)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" "
             ")| ForEach-Object { $_.CreationDate.ToString("
             "'yyyy-MM-dd HH:mm:ss') }" % pid],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            return out.splitlines()[0].strip()
    except Exception:
        pass
    return None


def read_gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,"
             "temperature.gpu,clocks.sm,power.draw,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout.strip()
        v = [x.strip() for x in out.split(",")]
        if len(v) >= 6:
            return {"mem_used": int(float(v[0])), "mem_total": int(float(v[1])),
                    "temp": int(float(v[2])), "sm_clock": int(float(v[3])),
                    "power_w": float(v[4]), "util": int(float(v[5]))}
    except Exception as e:
        return {"error": str(e)[:80]}
    return {}


def parse_metrics(text):
    counters = {}
    for line in text.splitlines():
        m = re.match(r"^(llamacpp:[a-z_]+)\s+([0-9.eE+-]+)$", line.strip())
        if m:
            counters[m.group(1)] = float(m.group(2))
    return counters


def poll_once():
    global CUR_TASK
    st = {}
    # engine liveness + identity
    try:
        st["health"] = fetch(ENGINE + "/health", 2).get("status") == "ok"
    except Exception:
        st["health"] = False
    if st["health"]:
        try:
            props = fetch(ENGINE + "/props", 3)
            st["model"] = (props.get("model_path") or "").split("\\")[-1]
            st["n_ctx"] = (props.get("default_generation_settings") or {})\
                .get("n_ctx")
        except Exception:
            pass
        try:
            slots = fetch(ENGINE + "/slots", 3)
            s = slots[0] if slots else {}
            st["slot"] = {k: s.get(k) for k in
                          ("id", "n_ctx", "is_processing", "id_task",
                           "n_prompt_tokens", "n_prompt_tokens_processed",
                           "n_prompt_tokens_cache", "speculative")}
            st["params"] = {k: (s.get("params") or {}).get(k) for k in
                            ("temperature", "top_k", "top_p", "min_p",
                             "max_tokens", "seed", "stream")}
        except Exception as e:
            st["last_error"] = "slots: %r" % e
        try:
            with urllib.request.urlopen(ENGINE + "/metrics",
                                        timeout=3) as r:
                st["counters"] = parse_metrics(r.read().decode())
        except Exception as e:
            st["last_error"] = "metrics: %r" % e
    # engine process identity
    if st["health"] and STATE.get("engine_pid") is None:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter "
                 "\"Name='llama-server.exe'\" | Where-Object { "
                 "$_.CommandLine -match '8107' } | Select-Object -First 1 "
                 "-ExpandProperty ProcessId)"],
                capture_output=True, text=True, timeout=10).stdout.strip()
            pid = int(out.splitlines()[0]) if out.strip() else None
            st["engine_pid"] = pid
            st["engine_started"] = engine_uptime(pid) if pid else None
        except Exception:
            pass
    else:
        st["engine_pid"] = STATE.get("engine_pid")
        st["engine_started"] = STATE.get("engine_started")
    if not st["health"]:
        st["engine_pid"] = None
        st["engine_started"] = None
    st["gpu"] = read_gpu()
    return st


def derive(new, now):
    """Live tok/s from counter diffs + request events from id_task."""
    global PREV, CUR_TASK, LAST_SEEN
    live = {}
    c = new.get("counters") or {}
    if PREV["counters"] and now - (PREV["ts"] or now) > 0:
        dt = now - PREV["ts"]
        pt = c.get("llamacpp:prompt_tokens_total", 0) - \
            PREV["counters"].get("llamacpp:prompt_tokens_total", 0)
        ct = c.get("llamacpp:tokens_predicted_total", 0) - \
            PREV["counters"].get("llamacpp:tokens_predicted_total", 0)
        live = {"p_ts": round(pt / dt, 1), "c_ts": round(ct / dt, 1),
                "p_tok": int(pt), "c_tok": int(ct)}
    PREV = {"counters": c, "ts": now}
    # request event tracking via id_task. LAST_SEEN = last observed task
    # (always updated); CUR_TASK = task of the currently open event.
    slot = new.get("slot") or {}
    task = slot.get("id_task")
    busy = bool(slot.get("is_processing"))
    events = STATE.get("events") or []
    if task is not None and task != LAST_SEEN and \
            (busy or LAST_SEEN is not None):
        # close the previously open event
        if CUR_TASK is not None and events:
            if events[0].get("task") == CUR_TASK and \
                    events[0]["state"] == "active":
                events[0]["state"] = "done"
                events[0]["ended"] = now
                jlog("events", {"ev": "close", "task": CUR_TASK,
                                "ended": now,
                                "c_tok": events[0].get("c_tok", 0)})
        CUR_TASK = task if busy else None
        events.insert(0, {"task": task, "state": "active" if busy
                          else "done", "started": now, "ended": None
                          if busy else now,
                          "prompt": slot.get("n_prompt_tokens"),
                          "c_tok": (live or {}).get("c_tok", 0),
                          "p_ts": (live or {}).get("p_ts"),
                          "c_ts": (live or {}).get("c_ts")})
        del events[EVENTS_MAX:]
        jlog("events", dict(events[0], ev="open"))
    elif task is not None and CUR_TASK == task and events:
        e0 = events[0]
        if e0.get("task") == CUR_TASK:
            e0["prompt"] = slot.get("n_prompt_tokens")
            e0["state"] = "active" if busy else e0["state"]
            if not busy and e0["state"] == "active":
                e0["state"] = "done"
                e0["ended"] = now
                jlog("events", {"ev": "close", "task": e0.get("task"),
                                "ended": now, "c_tok": e0.get("c_tok", 0)})
                CUR_TASK = None
            if live:
                e0["c_tok"] = e0.get("c_tok", 0) + live.get("c_tok", 0)
                e0["p_ts"] = live.get("p_ts") or e0.get("p_ts")
                e0["c_ts"] = live.get("c_ts") or e0.get("c_ts")
    LAST_SEEN = task if task is not None else LAST_SEEN
    return live, events


def poller():
    while True:
        try:
            new = poll_once()
            now = time.time()
            live, events = derive(new, now)
            new["live"] = live
            new["events"] = events
            new["ts"] = now
            new["journal"] = {"samples": os.path.basename(jpath("samples")),
                              "events": os.path.basename(jpath("events")),
                              "samples_bytes": J_SZ["samples"],
                              "events_bytes": J_SZ["events"]}
            new["polls"] = STATE.get("polls", 0) + 1
            if new["health"]:
                s = new.get("slot") or {}
                g = new.get("gpu") or {}
                hist = STATE.get("hist") or []
                sample = {
                    "ts": now,
                    "c_ts": live.get("c_ts", 0),
                    "p_ts": live.get("p_ts", 0),
                    "ctx": s.get("n_prompt_tokens") or 0,
                    "busy": 1 if s.get("is_processing") else 0,
                    "mem": g.get("mem_used") or 0,
                    "temp": g.get("temp") or 0}
                hist.append(sample)
                del hist[:-HIST_MAX]
                jlog("samples", sample)
                new["hist"] = hist
            else:
                new["hist"] = STATE.get("hist") or []
            with LOCK:
                STATE.clear()
                STATE.update(new)
        except Exception as e:
            log("poll error %r" % e)
        time.sleep(POLL_S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = open(HERE + r"\panel8107.html", "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/api/state":
            with LOCK:
                body = json.dumps(STATE, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            self.send_response(404)
            body = b"not found"
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=poller, daemon=True).start()
    log("panel8107 backend on http://127.0.0.1:%d (engine %s)"
        % (PORT, ENGINE))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
