#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agentgw - tool-augmented gateway for the "Peakstone Agent" track (O-5).

An OpenAI-compatible proxy on 127.0.0.1:8109. The bench (peakstone-bench)
talks to it as if it were the model; internally each completion request is
served by a tool-calling loop against the real llama-server (UPSTREAM) with
MCP tools:

  - pyapi   (stdio, Strata-Hub\\pyapi\\pyapi_server.py): package_info /
            inspect_symbol / find_examples against the ACTUAL peakstone-venv
  - localdocs (HTTP, docs-mcp-server on 6280): search_docs / read_page /
            list_libraries / find_version

Design invariants:
  - sampling (temp 0.2 / seed 42) and scoring/signing stay NATIVE: the bench
    never knows tools exist; only seed is dropped after iteration 1 (fresh
    draw per round).
  - minimal surface (O-1): exactly 7 tool schemas, fetched live from the
    servers' tools/list and filtered.
  - results are capped; loop is capped (MAX_ITERATIONS); per-call timeouts.
Port 8109 on purpose: NEVER 8107/8108 (Hermes silent-swap trap).

Run:  python -X utf8 -I agentgw.py   (log: agentgw.log next to this file)
"""

import json
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "http://127.0.0.1:8107/v1/chat/completions"
LOCALDOCS_URL = "http://127.0.0.1:6280/mcp"
PYAPI_SCRIPT = r"C:\Users\shogu\Strata-Hub\pyapi\pyapi_server.py"
PYAPI_PYTHON = r"C:\Users\shogu\Strata-Hub\peakstone-venv\Scripts\python.exe"
LISTEN = ("127.0.0.1", 8109)

PYAPI_TOOLS = {"package_info", "inspect_symbol", "find_examples"}
LOCALDOCS_TOOLS = {"search_docs", "read_page", "list_libraries", "find_version"}

MAX_ITERATIONS = 8
UPSTREAM_TIMEOUT = 1800       # per iteration, seconds: 16384 tokens at ~15 t/s
                              # needs ~1100 s; 600 s killed dp-01-class iterations
TOOL_TIMEOUT = 60             # per tool call, seconds (heavy FTS on big indexes)
TOOL_RESULT_CAP = 4000        # chars fed back to the model

SYSTEM_NOTE = (
    "Additional capabilities: you may call tools. "
    "package_info / inspect_symbol / find_examples inspect the ACTUAL installed "
    "Python environment (exact signatures and docstrings of the installed versions). "
    "search_docs / read_page / list_libraries / find_version search local library "
    "documentation. When the task involves library APIs, verify uncertain signatures "
    "with tools before writing code, then answer the task itself."
)

LOG_LOCK = threading.Lock()


def log_jsonl(record):
    record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_LOCK:
        with open(r"C:\Users\shogu\Strata-Hub\agentgw\agentgw.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------
# MCP stdio client (pyapi) - newline-delimited JSON-RPC over stdin/stdout
# ----------------------------------------------------------------------------
class McpStdio:
    def __init__(self, name, argv):
        self.name = name
        self.lock = threading.Lock()
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
        )
        self._qid = 0
        self._rq = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.initialize()

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._rq.put(json.loads(line))
            except Exception:
                pass

    def _request(self, method, params=None, timeout=TOOL_TIMEOUT):
        with self.lock:
            self._qid += 1
            qid = self._qid
            msg = {"jsonrpc": "2.0", "id": qid, "method": method}
            if params is not None:
                msg["params"] = params
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    item = self._rq.get(timeout=1.0)
                except queue.Empty:
                    continue
                if item.get("id") == qid:
                    if "error" in item:
                        raise RuntimeError("%s: %s" % (method, item["error"]))
                    return item.get("result", {})
            raise RuntimeError("%s: timeout after %ss" % (method, timeout))

    def initialize(self):
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agentgw", "version": "0.1.0"},
        })
        # notification: no response by protocol - just send it
        with self.lock:
            note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            self.proc.stdin.write(json.dumps(note) + "\n")
            self.proc.stdin.flush()

    def list_tools(self):
        return self._request("tools/list").get("tools", [])

    def call(self, name, arguments, timeout=TOOL_TIMEOUT):
        return self._request("tools/call", {"name": name, "arguments": arguments}, timeout)


# ----------------------------------------------------------------------------
# MCP streamable-HTTP client (localdocs)
# ----------------------------------------------------------------------------
class McpHttp:
    def __init__(self, name, url):
        self.name = name
        self.url = url
        self.lock = threading.Lock()
        self.session = None
        self._qid = 0
        self.initialize()

    def _post(self, payload, timeout=TOOL_TIMEOUT):
        data = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["mcp-session-id"] = self.session
        req = urllib.request.Request(self.url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sid = r.headers.get("mcp-session-id")
            if sid:
                self.session = sid
            body = r.read().decode("utf-8", "replace")
        # body may be plain JSON, an SSE stream, or EMPTY (202 for notifications)
        if not body.strip():
            return {}
        if body.lstrip().startswith("{"):
            return json.loads(body)
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except Exception:
                    pass
        raise RuntimeError("%s: unparsable response" % self.name)

    def initialize(self):
        resp = self._post({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "agentgw", "version": "0.1.0"}},
        })
        if "error" in resp:
            raise RuntimeError("localdocs initialize: %s" % resp["error"])
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _req(self, method, params=None, timeout=TOOL_TIMEOUT):
        with self.lock:
            self._qid += 1
            msg = {"jsonrpc": "2.0", "id": self._qid, "method": method}
            if params is not None:
                msg["params"] = params
            resp = self._post(msg, timeout)
        if "error" in resp:
            raise RuntimeError("%s: %s" % (method, resp["error"]))
        return resp.get("result", {})

    def list_tools(self):
        return self._req("tools/list").get("tools", [])

    def call(self, name, arguments, timeout=TOOL_TIMEOUT):
        return self._req("tools/call", {"name": name, "arguments": arguments}, timeout)


# ----------------------------------------------------------------------------
# Tool backends
# ----------------------------------------------------------------------------
class Gateways:
    def __init__(self):
        self.pyapi = None
        self.docs = None
        self.schemas = []

    def start(self):
        self.pyapi = McpStdio("pyapi", [
            PYAPI_PYTHON, "-I", PYAPI_SCRIPT,
            "--python", PYAPI_PYTHON,
            "--allow", "pandas,numpy,matplotlib,networkx,more_itertools,sortedcontainers,pydantic,dateutil,jsonschema,rich",
        ])
        self.docs = McpHttp("localdocs", LOCALDOCS_URL)
        schemas = []
        for t in self.pyapi.list_tools():
            if t.get("name") in PYAPI_TOOLS:
                schemas.append(self._openai_schema(t))
        for t in self.docs.list_tools():
            if t.get("name") in LOCALDOCS_TOOLS:
                schemas.append(self._openai_schema(t))
        got = {s["function"]["name"] for s in schemas}
        want = PYAPI_TOOLS | LOCALDOCS_TOOLS
        missing = want - got
        if missing:
            raise RuntimeError("missing tools: %s" % sorted(missing))
        self.schemas = schemas
        return sorted(got)

    @staticmethod
    def _openai_schema(t):
        return {"type": "function", "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
        }}

    def call(self, name, arguments):
        if name in PYAPI_TOOLS:
            raw = self.pyapi.call(name, arguments)
        elif name in LOCALDOCS_TOOLS:
            raw = self.docs.call(name, arguments)
        else:
            return "ERROR: unknown tool %r" % name
        # MCP tool result: {"content": [{"type": "text", "text": "..."}], ...}
        parts = []
        for item in raw.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
        text = "\n".join(parts) or json.dumps(raw, ensure_ascii=False)
        if raw.get("isError"):
            text = "TOOL ERROR: " + text
        if len(text) > TOOL_RESULT_CAP:
            text = text[:TOOL_RESULT_CAP] + "\n...[truncated %d chars]" % (len(text) - TOOL_RESULT_CAP)
        return text


GW = Gateways()


# ----------------------------------------------------------------------------
# Tool loop + HTTP handler
# ----------------------------------------------------------------------------
def upstream_call(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(UPSTREAM, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:
        return json.load(r)


def run_tool_loop(req):
    t0 = time.time()
    msgs = [dict(m) for m in req.get("messages", [])]
    sys_idx = next((i for i, m in enumerate(msgs) if m.get("role") == "system"), None)
    if sys_idx is not None:
        msgs[sys_idx]["content"] = (msgs[sys_idx].get("content") or "") + "\n\n" + SYSTEM_NOTE
    else:
        msgs.insert(0, {"role": "system", "content": SYSTEM_NOTE})

    # MERGE semantics (operator order 04.11): the bench's own tool schemas are
    # PRESERVED and come first; local MCP schemas are APPENDED (name collisions
    # -> the bench's schema wins). Bench-owned tools are NOT executed here:
    # when the model calls one, that assistant message is returned to the bench
    # immediately as the final response (the bench verifies/exports it).
    bench_tools = [t for t in (req.get("tools") or []) if isinstance(t, dict)]
    bench_names = set()
    for t in bench_tools:
        fn = t.get("function") or {}
        if fn.get("name"):
            bench_names.add(fn["name"])
    local_schemas = [s for s in GW.schemas if s["function"]["name"] not in bench_names]

    usage_sum = {"prompt_tokens": 0, "completion_tokens": 0}
    tool_calls_log = []
    finish = "stop"
    final_msg = {"role": "assistant", "content": ""}
    iterations = 0

    for i in range(MAX_ITERATIONS):
        iterations = i + 1
        payload = {k: v for k, v in req.items()
                   if k not in ("stream", "tools", "tool_choice") and v is not None}
        payload["messages"] = msgs
        payload["tools"] = bench_tools + local_schemas
        payload["tool_choice"] = req.get("tool_choice") or "auto"
        payload["stream"] = False
        if i > 0:
            payload.pop("seed", None)  # fresh draw per round
        resp = upstream_call(payload)
        u = resp.get("usage") or {}
        usage_sum["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
        usage_sum["completion_tokens"] += u.get("completion_tokens", 0) or 0
        choice = resp["choices"][0]
        finish = choice.get("finish_reason") or "stop"
        msg = choice.get("message") or {}
        tcs = msg.get("tool_calls") or []
        if tcs:
            bench_calls = [tc for tc in tcs
                           if (tc.get("function") or {}).get("name", "") in bench_names]
            if bench_calls:
                # bench-owned tool calls belong to the bench: pass through as the
                # final answer, DON'T execute locally, DON'T feed results back
                tool_calls_log.extend((tc.get("function") or {}).get("name", "") + "*" for tc in bench_calls)
                final_msg = msg
                finish = choice.get("finish_reason") or "tool_calls"
                break
            assistant = {"role": "assistant", "content": msg.get("content")}
            if msg.get("reasoning_content"):
                assistant["reasoning_content"] = msg["reasoning_content"]
            assistant["tool_calls"] = tcs
            msgs.append(assistant)
            for tc in tcs:
                fn = (tc.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if name in bench_names:
                    result = "TOOL ERROR: %r is a client-side tool; it is not executed here" % name
                else:
                    try:
                        result = GW.call(name, args)
                    except Exception as e:
                        result = "TOOL ERROR: %s: %s" % (type(e).__name__, str(e)[:300])
                tool_calls_log.append(name)
                msgs.append({"role": "tool", "tool_call_id": tc.get("id"),
                             "name": name, "content": result})
            continue
        # no tool calls -> final answer (even on finish=length: honest pass-through)
        final_msg = msg
        break

    log_jsonl({
        "kind": "completion", "iterations": iterations,
        "tool_calls": tool_calls_log,
        "bench_tools": sorted(bench_names),
        "prompt_tokens": usage_sum["prompt_tokens"],
        "completion_tokens": usage_sum["completion_tokens"],
        "finish": finish, "wall_s": round(time.time() - t0, 1),
        "max_tokens_requested": req.get("max_tokens"),
    })
    return {
        "id": "agentgw-%d" % int(time.time() * 1000),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.get("model", "agentgw"),
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": final_msg.get("content") or "",
                "reasoning_content": final_msg.get("reasoning_content") or "",
                **({"tool_calls": final_msg["tool_calls"]} if final_msg.get("tool_calls") else {}),
            },
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": usage_sum["prompt_tokens"],
            "completion_tokens": usage_sum["completion_tokens"],
            "total_tokens": usage_sum["prompt_tokens"] + usage_sum["completion_tokens"],
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet default logging
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self._send(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [
                {"id": "agentgw", "object": "model", "owned_by": "agentgw"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            result = run_tool_loop(req)
            if req.get("stream"):
                self._send_sse(result)
            else:
                self._send(200, result)
        except Exception as e:
            log_jsonl({"kind": "error", "err": "%s: %s" % (type(e).__name__, str(e)[:400])})
            try:
                self._send(502, {"error": {"message": "%s: %s" % (type(e).__name__, str(e)[:300])}})
            except Exception:
                pass

    def _send_sse(self, result):
        """OpenAI streaming response. The bench forces stream=True on every
        run (repetition-loop detection) and parses SSE only - a plain JSON
        body reads as an empty answer. Re-emit the aggregated tool-loop
        result as proper chunks, ending with a usage-only chunk."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n")

        choice = result["choices"][0]
        msg = choice["message"]
        cid = result["id"]
        created = result["created"]
        model = result["model"]
        base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}

        first = dict(base)
        delta = {"role": "assistant"}
        if msg.get("reasoning_content"):
            delta["reasoning_content"] = msg["reasoning_content"]
        first["choices"] = [{"index": 0, "delta": delta, "finish_reason": None}]
        emit(first)

        content = msg.get("content") or ""
        CH = 200
        for i in range(0, len(content), CH):
            chunk = dict(base)
            chunk["choices"] = [{"index": 0, "delta": {"content": content[i:i + CH]}, "finish_reason": None}]
            emit(chunk)

        last = dict(base)
        last["choices"] = [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}]
        emit(last)

        usage_chunk = dict(base)
        usage_chunk["choices"] = []
        usage_chunk["usage"] = result["usage"]
        emit(usage_chunk)

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    names = GW.start()
    log_jsonl({"kind": "startup", "tools": names})
    print("agentgw ready on %s:%d, tools: %s" % (LISTEN[0], LISTEN[1], ", ".join(names)), flush=True)
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()


if __name__ == "__main__":
    main()
