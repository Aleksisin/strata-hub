"""Build supervisor queue jobs for the IBF-AgentBench baseline (10 tasks).

python -I mkagentbench.py [task_id ...]   # default = all tasks in tasks/
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.realpath(__file__))
SUP_PENDING = r"C:\Users\shogu\Strata-Hub\supervisor\queue\pending"
PY = r"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"
RUNNER = os.path.join(HERE, "agentbench_runner.py")

ap = argparse.ArgumentParser()
ap.add_argument("ids", nargs="*")
ap.add_argument("--prefix", default="abx-")
a = ap.parse_args()


def main():
    ids = a.ids
    if not ids:
        ids = sorted(f[:-5] for f in os.listdir(os.path.join(HERE, "tasks"))
                     if f.endswith(".json"))
    os.makedirs(SUP_PENDING, exist_ok=True)
    for tid in ids:
        task = json.load(open(os.path.join(HERE, "tasks", tid + ".json"),
                              encoding="utf-8"))
        run_id = a.prefix + tid
        run_dir = os.path.join(HERE, "runs", run_id)
        job = {
            "run_id": run_id,
            "kind": "agentbench",
            "cmd": [PY, "-I", RUNNER, "--task", tid, "--out", run_dir],
            "workdir": HERE,
            "run_dir": run_dir,
            "timeout_s": 4200,
            "validate": {"kind": "agentbench_task"},
            "manifest": {
                "model": {"name": "nanoplus-v3 8107 via agentgw",
                          "gguf_path": None},
                "engine": {"name": "agentgw", "port": 8109,
                           "binary_path": r"C:\Users\shogu\Strata-Hub\agentgw\agentgw.py"},
                "inference": {"context": 131072, "max_tokens": 16384,
                              "temperature": 0.2, "seed": 42},
                "tools": {"profile": "agent-full",
                          "gateway_path": r"C:\Users\shogu\Strata-Hub\agentgw\agentgw.py",
                          "allowlist_path": None},
                "benchmark": {"suite_path": os.path.join(HERE, "tasks", tid + ".json"),
                              "ids_file": None},
            },
        }
        dst = os.path.join(SUP_PENDING, run_id + ".json")
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=1)
        print("queued", run_id)
    print("total:", len(ids))


if __name__ == "__main__":
    main()
