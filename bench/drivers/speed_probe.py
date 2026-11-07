r"""Fresh live speed probe against the RUNNING prod server (8107, b11985-mix,
freeze-abx5b) — no restart, no flag changes. Three requests:
  1. decode @short ctx (max_tokens 384)
  2. cold prefill @40K (M1 payload, cache_prompt=false)
  3. cold prefill @117K (L1 payload, cache_prompt=false) + identical re-send
     (cache hit)
Log: engine_axis/speed-probe.log
"""
import io
import json
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
AXIS = r"C:\Users\shogu\engine_axis"
URL = "http://127.0.0.1:8107/v1/chat/completions"
LOG = AXIS + r"\speed-probe.log"


def log(msg):
    line = time.strftime("[%m-%d %H:%M:%S] ") + msg
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def send(tag, payload, timeout=2400):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    wall = time.time() - t0
    tm = d.get("timings") or {}
    u = d.get("usage") or {}
    log("%-12s ptok=%-7s ctok=%-5s ttft=%-7.2f p/s=%-9.0f c/s=%-6.1f "
        "wall=%.1f finish=%s"
        % (tag, u.get("prompt_tokens"), u.get("completion_tokens"),
           tm.get("prompt_ms", 0) / 1000.0, tm.get("prompt_per_second") or 0,
           tm.get("predicted_per_second") or 0, wall,
           d["choices"][0].get("finish_reason")))
    return d


def load_payload(name):
    with open(AXIS + r"\replay_payloads\%s.json" % name, encoding="utf-8") as f:
        p = json.load(f)
    return {"model": "nanoplus-v3", "messages": p["messages"],
            "tools": p.get("tools"), "temperature": 0.0}


log("=== live speed probe start (8107 prod b11985-mix) ===")
send("decode-384", dict(load_payload("S2"), max_tokens=384,
                        cache_prompt=False))
send("cold-M1", dict(load_payload("M1"), max_tokens=16, cache_prompt=False))
send("cold-L1", dict(load_payload("L1"), max_tokens=8, cache_prompt=False))
send("warm-L1", dict(load_payload("L1"), max_tokens=8, cache_prompt=True))
log("=== probe done ===")
