"""IBF-AgentBench v0.5 runner — one real repo task through the agent contour.

Usage:
  python -I agentbench_runner.py --task <task_id> --out <run_dir>
      [--gateway http://127.0.0.1:8109] [--model agentbench] [--seed 42]

Per task (tasks/<id>.json):
  1. isolated copy = git worktree of agentbench/mirror at the task's base
     commit, placed in <out>/copy; verifier test overlay applied BEFORE the
     loop (test-driven setup, IBF practice); venv junction if needs_venv.
  2. agent loop through the OpenAI-compatible gateway (agentgw 8109 = MCP
     contour): harness tools (list_dir/read_file/write_file/run_tests/
     git_diff/finish); caps: rounds / tool calls / wall.
  3. verification: full verifier test file must be green (rc==0) for bugfix;
     answer-file schema check for diagnosis; clean-pass + seeded-mutation-fail
     for integration guards.
  4. report.json + transcript.jsonl in <out>; error class:
     infra | tool | budget | model | none.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
MIRROR = os.path.join(HERE, "mirror")
VENV_PY = r"C:\IBF\DiagnosticPipeline\.venv\Scripts\python.exe"

SYSTEM_PROMPT = (
    "Ты агент-разработчик, работающий в ИЗОЛИРОВАННОЙ КОПИИ репозитория IBF "
    "(Diagnostic Framework). Все пути инструментов — относительно корня копии. "
    "Правила: (1) правки только в рабочем дереве копии, никаких git commit; "
    "(2) тесты-приёмщики защищены от записи — их не менять, они часть условия; "
    "(3) проверяй себя запуском run_tests; (4) пиши код в стиле окружающего "
    "кода; (5) когда считаешь работу завершённой — вызови finish с кратким "
    "итогом. Диалог на русском."
)


def http_json(url, payload, timeout=1200):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Transcript:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")

    def w(self, obj):
        self.f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.f.flush()


def prepare_copy(task, out_dir):
    """git worktree at base commit + test overlay + optional venv junction."""
    copy = os.path.join(out_dir, "copy")
    subprocess.run(["git", "-C", MIRROR, "worktree", "prune"],
                   capture_output=True, text=True)
    if os.path.exists(copy):
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Remove-Item -LiteralPath '%s' -Recurse -Force" % copy],
                       capture_output=True, text=True)
    r = subprocess.run(["git", "-C", MIRROR, "worktree", "add", "--force",
                        "--detach", copy, task["base"]],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError("worktree add failed: %s" % r.stderr[-400:])
    for f in task.get("overlay", []):
        r = subprocess.run(["git", "-C", MIRROR, "show",
                            "%s:%s" % (task["overlay_commit"], f)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            raise RuntimeError("overlay source missing: %s" % f)
        dst = os.path.join(copy, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="") as fh:
            fh.write(r.stdout)
        # keep index clean: mark as intent-to-add? no - leave as working change
    if task.get("needs_venv"):
        target = os.path.join(copy, "DiagnosticPipeline", ".venv")
        if not os.path.exists(target):
            r = subprocess.run(["cmd", "/c", "mklink", "/J", target,
                                r"C:\IBF\DiagnosticPipeline\.venv"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError("venv junction failed: %s" % r.stderr)
    ev_dir = os.path.join(copy, "evidence")
    for ev in task.get("evidence_files", []):
        src = os.path.join(HERE, "evidence", ev)
        if os.path.isfile(src):
            os.makedirs(ev_dir, exist_ok=True)
            with open(src, "rb") as f_in, open(os.path.join(ev_dir, ev), "wb") as f_out:
                f_out.write(f_in.read())
    return copy


def verify_bugfix(task, copy, report):
    r = subprocess.run([VENV_PY, "-m", "pytest"] + task["verify_tests"] +
                       ["-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=copy, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1200)
    report["verify_rc"] = r.returncode
    report["verify_tail"] = r.stdout[-2500:]
    return r.returncode == 0


def verify_diagnosis(task, copy, report):
    ans_path = os.path.join(copy, task["answer_path"])
    if not os.path.isfile(ans_path):
        report["verify_tail"] = "answer file missing: %s" % task["answer_path"]
        return False
    try:
        ans = json.load(open(ans_path, encoding="utf-8"))
    except Exception as e:
        report["verify_tail"] = "answer unparsable: %s" % e
        return False
    missing = [k for k in task["answer_required_keys"] if k not in ans]
    if missing:
        report["verify_tail"] = "missing answer keys: %s" % missing
        return False
    blob = json.dumps(ans, ensure_ascii=False).lower()
    misses = [pat for pat in task.get("answer_must_contain", [])
              if pat.lower() not in blob]
    if misses:
        report["verify_tail"] = "answer misses required facts: %s" % misses
        return False
    for field, pattern in (task.get("answer_regex") or {}).items():
        val = ans
        for part in field.split("."):
            val = val.get(part) if isinstance(val, dict) else None
        if not isinstance(val, str) or not re.match(pattern, val.strip() or ""):
            report["verify_tail"] = "answer_regex %s: bad value %r" % (field, val)
            return False
    extra = task.get("answer_extra") or {}
    if "victims_min" in extra:
        vict = ans.get("victims")
        if not isinstance(vict, list) or len(vict) < extra["victims_min"]:
            report["verify_tail"] = "victims list too short: %r" % vict
            return False
    report["verify_tail"] = "answer ok"
    return True


def _apply_mutation(copy, m):
    """mutate spec: kind=replace (old->new) | remove_line (pattern regex).
    Returns True if applied. Caller must revert via _revert_file."""
    target = os.path.join(copy, m["file"])
    src = open(target, encoding="utf-8").read()
    if m.get("kind") == "remove_line":
        rx = re.compile(m["pattern"], re.MULTILINE)
        new_src, n = rx.subn("", src)
        if n == 0:
            return False
        open(target, "w", encoding="utf-8", newline="").write(new_src)
        return True
    if m["old"] not in src:
        return False
    open(target, "w", encoding="utf-8", newline="").write(
        src.replace(m["old"], m["new"], 1))
    return True


def _revert_file(copy, rel):
    subprocess.run(["git", "checkout", "--", rel], cwd=copy,
                   capture_output=True, text=True)


def _run_guard(copy, guard, timeout=900):
    r = subprocess.run([VENV_PY, "-m", "pytest", guard, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=copy, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout[-1200:]


def verify_integration(task, copy, report):
    """Step machine: [{run: must_pass|must_fail, mutate?: {...}}]."""
    guard = task["guard_test_file"]
    if not os.path.isfile(os.path.join(copy, guard)):
        report["verify_tail"] = "guard test missing: %s" % guard
        return False
    steps = task["verify_steps"]
    for i, step in enumerate(steps):
        if "mutate" in step:
            m = step["mutate"]
            if not _apply_mutation(copy, m):
                report["verify_tail"] = "mutation anchor not found: %s" % m["file"]
                return False
        rc, out = _run_guard(copy, guard)
        report["step%d_rc" % i] = rc
        if step["run"] == "must_pass" and rc != 0:
            report["verify_tail"] = "step %d expected green:\n%s" % (i, out)
            if "mutate" in step:
                _revert_file(copy, step["mutate"]["file"])
            return False
        if step["run"] == "must_fail" and rc == 0:
            report["verify_tail"] = "step %d expected red but guard passed " \
                                    "(mutation survived)" % i
            if "mutate" in step:
                _revert_file(copy, step["mutate"]["file"])
            return False
        if "mutate" in step:
            _revert_file(copy, step["mutate"]["file"])
    report["verify_tail"] = "all %d verify steps ok" % len(steps)
    return True


def trim_context(messages, budget=200_000, keep_tail=10):
    """Sliding context: old tool results collapse to placeholders when the
    total conversation grows past `budget` chars (131K-token server limit).
    First system+user and the last `keep_tail` messages stay intact."""
    total = sum(len(m.get("content") or "") for m in messages)
    if total <= budget:
        return messages
    head = messages[:2]
    tail = messages[-keep_tail:]
    mid = messages[2:-keep_tail]
    freed = total - budget
    out_mid = []
    for m in reversed(mid):
        c = m.get("content") or ""
        if m.get("role") == "tool" and len(c) > 500 and freed > 0:
            freed -= len(c)
            m = dict(m, content="[результат вынесен из контекста — "
                                "перечитай файл, если он снова нужен]")
        out_mid.append(m)
    return head + list(reversed(out_mid)) + tail


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gateway", default="http://127.0.0.1:8109/v1/chat/completions")
    ap.add_argument("--model", default="agentbench")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-rounds", type=int, default=80)
    ap.add_argument("--max-tool-calls", type=int, default=250)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--wall-timeout", type=float, default=3000)
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)

    os.makedirs(a.out, exist_ok=True)
    task = json.load(open(os.path.join(HERE, "tasks", a.task + ".json"),
                          encoding="utf-8"))
    report = {"task": a.task, "kind": task["kind"], "base": task["base"],
              "gateway": a.gateway, "model": a.model, "seed": a.seed,
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tr = Transcript(os.path.join(a.out, "transcript.jsonl"))
    tr.w({"event": "start", "task": a.task, "base": task["base"]})

    t0 = time.time()
    error_class = "none"
    try:
        copy = prepare_copy(task, a.out)
    except Exception as e:
        report.update({"success": False, "error_class": "infra", "error": str(e),
                       "wall_s": round(time.time() - t0, 1)})
        finish_report(report, a, task, tr, False)
        return 1

    sys.path.insert(0, HERE)
    import abtools
    box = abtools.ToolBox(copy, VENV_PY, task["verify_tests"],
                          task.get("protected", []))
    tools = abtools.TOOLS_SCHEMA
    if not task["verify_tests"]:
        # diagnosis tasks: no pytest surface for the agent
        tools = [t for t in tools
                 if t["function"]["name"] not in ("run_tests",)]
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task["statement"]}]
    rounds = tool_calls = red_tests = 0
    first_edit_seen = False
    last_edit_round = 0
    edits_count = 0
    fix_cycles = 0
    auto_red = 0
    nudged = set()
    finish_summary = None
    answer_path_abs = os.path.join(copy, task.get("answer_path", "answer.json"))

    try:
        while rounds < a.max_rounds:
            if time.time() - t0 > a.wall_timeout:
                error_class = "budget"
                report["error"] = "wall timeout %.0fs" % (time.time() - t0)
                break
            rounds += 1
            messages = trim_context(messages)
            payload = {"model": a.model, "messages": messages,
                       "tools": tools,
                       "temperature": a.temperature, "seed": a.seed,
                       "max_tokens": a.max_tokens}
            try:
                resp = http_json(a.gateway, payload)
            except urllib.error.HTTPError as e:
                error_class = "infra" if e.code >= 500 else "tool"
                report["error"] = "gateway HTTP %s" % e.code
                break
            except Exception as e:
                error_class = "infra"
                report["error"] = "gateway unreachable: %r" % e
                break
            tr.w({"event": "resp", "round": rounds,
                  "finish": resp["choices"][0].get("finish_reason"),
                  "usage": resp.get("usage")})
            choice = resp["choices"][0]
            msg = choice["message"]
            messages.append({"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": msg.get("tool_calls") or None})
            tcs = msg.get("tool_calls") or []
            if not tcs:
                # plain answer without finish -> treat as attempt to stop
                finish_summary = (msg.get("content") or "")[-500:]
                break
            for tc in tcs:
                tool_calls += 1
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                tr.w({"event": "tool", "round": rounds, "name": name,
                      "args_brief": str(args)[:300]})
                finished = False
                auto_test = False
                try:
                    if name == "finish":
                        finish_summary = str(args.get("summary"))[:800]
                        result = "finish accepted"
                        finished = True
                    else:
                        result = str(box.call(name, args))[:8000]
                        if name == "apply_patch" and task.get("verify_tests") \
                                and result.startswith("PATCH OK"):
                            auto_test = True
                    ok = True
                except Exception as e:
                    result = "TOOL ERROR: %r" % e
                    ok = False
                    tr.w({"event": "tool_error", "name": name, "err": str(e)})
                if name in ("write_file", "apply_patch") and ok and not finished:
                    first_edit_seen = True
                    last_edit_round = rounds
                    edits_count += 1
                if auto_test:
                    # review 05.11: patch response carries diff + test result
                    test_out = str(box.call("run_tests", {}))[:4000]
                    # review 05.11 late (final acceptance): red auto-tests must
                    # count toward first_verification_pass, else the metric is
                    # inflated by silent edit->auto-red->edit sequences
                    if "failed=" in test_out and "failed=none" not in test_out:
                        auto_red += 1
                    result = result[:4000] + "\n\n[АВТО-ТЕСТ после правки]\n" + test_out
                if name == "run_tests":
                    if first_edit_seen and "failed=" in result and \
                            "failed=none" not in result:
                        red_tests += 1
                        fix_cycles += 1
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": result[:8000]})
                if finished:
                    break
                if tool_calls > a.max_tool_calls:
                    error_class = "budget"
                    report["error"] = "tool call cap %d" % a.max_tool_calls
                    break
            if finish_summary is not None or error_class == "budget":
                break
            # ---- no-progress control (review 05.11; harness-side, model unchanged) ----
            stalled = rounds - last_edit_round
            need = []
            if task["kind"] in ("bugfix", "integration") and edits_count == 0 \
                    and rounds >= 12:
                need = [t for t in (12, 28, 45) if rounds >= t and t not in nudged]
                if need:
                    nudged.add(need[0])
                    messages.append({"role": "user", "content":
                        "КОНТРОЛЬ ПРОГРЕССА: %d раундов без единого изменения "
                        "файлов. Либо внесите конкретную правку (apply_patch — "
                        "точечная замена по якорю), либо вызовите finish с "
                        "честным итогом." % rounds})
                    tr.w({"event": "nudge", "round": rounds, "reason": "no_edits"})
            elif task["kind"] == "diagnosis" and \
                    not os.path.isfile(answer_path_abs) and rounds >= 12:
                need = [t for t in (12, 28, 45) if rounds >= t and t not in nudged]
                if need:
                    nudged.add(need[0])
                    messages.append({"role": "user", "content":
                        "КОНТРОЛЬ ПРОГРЕССА: %s ещё не записан. Завершите "
                        "диагностику и запишите отчёт write_file'ом в корень "
                        "копии, либо вызовите finish." % task.get("answer_path")})
                    tr.w({"event": "nudge", "round": rounds, "reason": "no_answer"})
            elif edits_count > 0 and stalled >= 15 and \
                    rounds - max(rounds - stalled, 0) > 0 and stalled not in nudged:
                nudged.add(stalled)
                messages.append({"role": "user", "content":
                    "КОНТРОЛЬ ПРОГРЕССА: %d раундов после последней правки. "
                    "Проверьте себя run_tests и вызовите finish, если работа "
                    "завершена." % stalled})
                tr.w({"event": "nudge", "round": rounds, "reason": "stalled_after_edit"})
        else:
            error_class = "budget"
            report["error"] = "round cap %d" % a.max_rounds
    finally:
        wall = time.time() - t0

    # ---- verification ----
    success = False
    if error_class == "none":
        if task["kind"] == "bugfix":
            success = verify_bugfix(task, copy, report)
        elif task["kind"] == "diagnosis":
            success = verify_diagnosis(task, copy, report)
        else:
            success = verify_integration(task, copy, report)
        if not success:
            error_class = "model"
    elif error_class == "budget":
        # review 05.11: distinguish "never touched the deliverable"
        if task["kind"] == "diagnosis":
            if not os.path.isfile(answer_path_abs):
                error_class = "no_progress"
        elif edits_count == 0:
            error_class = "no_progress"
    else:
        success = False

    report.update({"success": success, "error_class": error_class,
                   "rounds": rounds, "tool_calls": tool_calls,
                   "edits_count": edits_count,
                   "run_tests_red_after_edit": red_tests,
                   "fix_cycles": fix_cycles,
                   "auto_red_count": auto_red,
                   "first_verification_pass": bool(success and fix_cycles == 0
                                                   and auto_red == 0),
                   "first_try": bool(success and fix_cycles == 0
                                     and auto_red == 0),
                   "finish_summary": finish_summary,
                   "wall_s": round(wall, 1)})
    finish_report(report, a, task, tr, success)
    return 0 if success else 1


def finish_report(report, a, task, tr, success):
    report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rp = os.path.join(a.out, "report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    tr.w({"event": "report", **{k: report.get(k) for k in
                                ("success", "error_class", "rounds",
                                 "tool_calls", "fix_cycles", "wall_s")}})
    print("[agentbench] %s -> success=%s class=%s wall=%.0fs rounds=%s" %
          (a.task, success, report.get("error_class"),
           report.get("wall_s", 0), report.get("rounds")))


if __name__ == "__main__":
    sys.exit(main())
