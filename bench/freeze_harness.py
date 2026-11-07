"""Freeze the AgentBench harness: SHA256 of everything executable that
affects a run, plus the fixed caps. Operator review 05.11 point 1: all
ten tasks must use an identical version; the manifest is the reference
for the abx2-* repeat baseline.

python -I freeze_harness.py            # writes harness-freeze-abx2.json
"""
import hashlib
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.realpath(__file__))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


FILES = {
    "runner": os.path.join(HERE, "agentbench_runner.py"),
    "tools": os.path.join(HERE, "abtools.py"),
    "task_loader": os.path.join(HERE, "mkagentbench.py"),
    "gateway": r"C:\Users\shogu\Strata-Hub\agentgw\agentgw.py",
    "engine_binary": r"C:\Users\shogu\.unsloth\llama.cpp\build\bin\Release\llama-server.exe",
    "model_gguf": r"C:\Users\shogu\Strata-Hub\models\Qwen3.8-27B-EfficientThink-NanoPlus-V3"
                  r"\Qwen3.8-27B-EfficientThink-VAL-APEX-I-NanoPlus-V3.gguf",
    "supervisor": r"C:\Users\shogu\Strata-Hub\supervisor\supervisor.py",
}

CAPS = {
    "max_rounds": 80, "max_tool_calls": 250, "wall_timeout_s": 3000,
    "max_tokens_per_request": 16384, "temperature": 0.2, "seed": 42,
    "read_file_cap_bytes": 12000, "read_repeat_hint_at": 3,
    "trim_budget_chars": 200000, "trim_keep_tail": 10,
    "http_timeout_s": 1200,
    "nudge_rounds_no_edits": [12, 28, 45],
    "stalled_after_edit_hint": 15,
    "job_timeout_s": 4200,
}

TOOLS = ["list_dir", "read_file", "write_file", "apply_patch", "run_tests",
         "git_diff", "git_log", "git_show", "finish"]


def main():
    series = sys.argv[1] if len(sys.argv) > 1 else "abx2"
    man = {"frozen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "series": series, "model": "nanoplus-v3 8107 via agentgw",
           "caps": CAPS, "tools": TOOLS, "files": {}}
    for k, p in FILES.items():
        if os.path.isfile(p):
            man["files"][k] = {"path": p, "sha256": sha256(p),
                               "bytes": os.path.getsize(p)}
        else:
            man["files"][k] = {"path": p, "error": "missing"}
    out = os.path.join(HERE, "harness-freeze-%s.json" % man["series"])
    with open(out, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print("frozen ->", out)
    for k, v in man["files"].items():
        print("  %-16s %s %s" % (k, (v.get("sha256") or v.get("error"))[:16], v["path"][-40:]))


if __name__ == "__main__":
    main()
