"""Harness tool implementations for IBF-AgentBench.

Agent tools (OpenAI function-calling), executed harness-side:
  list_dir, read_file, write_file, apply_patch, run_tests, git_diff,
  git_log, git_show, finish

Guards: all paths stay inside the isolated copy; protected paths
(verifier tests) are read-only for the agent; run_tests has its own
timeout and returns a compact tail. apply_patch = point edit:
exact-context replacement, refusal on ambiguous match, atomic write,
compact diff in the response.

Review 05.11 (operator): apply_patch added as first technical priority
- bf2/i1 showed 80 rounds without a single edit: write_file forces a
disproportionately large operation for a small fix in a big file.
"""
import difflib
import os
import re
import subprocess


class ToolBox:
    def __init__(self, copy_dir, venv_py, test_files, protected, run_timeout=600):
        self.copy = os.path.realpath(copy_dir)
        self.venv_py = venv_py
        self.test_files = list(test_files)   # verifier files (run_tests target)
        self.protected = [os.path.realpath(os.path.join(copy_dir, p)) for p in protected]
        self.run_timeout = run_timeout
        self.read_counts = {}    # relpath -> times read (no-progress hint)
        self.log = []            # (tool, args, ok, brief) trail for report

    # ---- path guard ----
    def _resolve(self, path):
        if path is None or not isinstance(path, str):
            raise ValueError("path required")
        p = os.path.realpath(os.path.join(self.copy, path))
        if p != self.copy and not p.startswith(self.copy + os.sep):
            raise ValueError("path escapes the copy: %s" % path)
        if ".git" in p.split(os.sep):
            raise ValueError("git internals are off-limits: %s" % path)
        return p

    def _guard_write(self, p):
        for prot in self.protected:
            if p == prot:
                raise ValueError("PROTECTED (verifier test, read-only): %s" %
                                 os.path.relpath(p, self.copy))

    # ---- tools ----
    def list_dir(self, path=""):
        p = self._resolve(path)
        if not os.path.isdir(p):
            raise ValueError("not a directory: %s" % path)
        rows = []
        for name in sorted(os.listdir(p))[:200]:
            fp = os.path.join(p, name)
            rows.append(("%-12s %s" % ("<dir>" if os.path.isdir(fp) else
                                       os.path.getsize(fp), name)))
        return "\n".join(rows) or "(empty)"

    def read_file(self, path, start_line=1, max_lines=200):
        """Line-based windowed read (review 05.11 rev.3 contract).
        next_start_line = FIRST UNISSUED line (never start+max_lines and
        never last-issued+2): lines are never skipped at the size-cap
        boundary. Response carries: line numbers, total lines, issued
        range, TRUNCATED/next_start_line or END, out-of-range message.
        Hard size cap ~12 KB preserved."""
        p = self._resolve(path)
        if not os.path.isfile(p):
            raise ValueError("not a file: %s" % path)
        rel = os.path.relpath(p, self.copy)
        self.read_counts[rel] = self.read_counts.get(rel, 0) + 1
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(1, int(start_line or 1))
        if start > total:
            return ("OUT OF RANGE: start_line %d > total lines %d in %s. "
                    "Use start_line <= %d or search_in_file." %
                    (start, total, rel, total))
        ml = max(20, min(int(max_lines or 200), 400))
        out, used, i = [], 0, start
        capped = False
        while i <= total and i < start + ml:
            piece = "%5d: %s" % (i, lines[i - 1].rstrip("\n"))
            if out and used + len(piece) > 12000:
                capped = True
                break
            out.append(piece)
            used += len(piece) + 1
            i += 1
        last_issued = i - 1 if out else None
        next_start = i  # first UNISSUED line, always
        truncated = next_start <= total
        text = "\n".join(out)
        if capped:
            text += "\n...[окно ограничено размером (~12 КБ) на строке %d]" % i
        text += "\n[ФАЙЛ %s: всего %d строк | выданы строки %d–%d | %s]" % (
            rel, total, start, last_issued if last_issued else start - 1,
            ("TRUNCATED | next_start_line=%d" % next_start) if truncated
            else "КОНЕЦ ФАЙЛА")
        n = self.read_counts[rel]
        if n >= 3 and start <= 1:
            text += ("\n[НАПОМИНАНИЕ: начало файла читается %d-й раз. "
                     "Для глубины — start_line, для точек входа — "
                     "search_in_file; для правки — apply_patch.]") % n
        return text

    def search_in_file(self, path, pattern):
        """Regex search: match line numbers + context lines per match.
        Explicitly reports when more than the shown matches exist and
        when the output hits the size cap (nothing silently hidden)."""
        p = self._resolve(path)
        if not os.path.isfile(p):
            raise ValueError("not a file: %s" % path)
        rel = os.path.relpath(p, self.copy)
        if not pattern or not isinstance(pattern, str):
            raise ValueError("pattern required")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ValueError("bad regex: %s" % e)
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        hits = [i for i, l in enumerate(lines, start=1) if rx.search(l)]
        if not hits:
            return "NO MATCHES for %r in %s (%d lines)" % (pattern, rel, len(lines))
        budget, shown, out = 5600, 0, [
            "matches: %d for %r in %s (%d lines)" % (len(hits), pattern, rel, len(lines))]
        for i in hits:
            if shown >= 10:
                out.append("...и ещё %d совпадений сверх показанных 10 — "
                           "уточните pattern" % (len(hits) - 10))
                break
            block = ["--- match at line %d ---" % i]
            used = 0
            for j in range(max(1, i - 2), min(len(lines), i + 3) + 1):
                piece = "%5d: %s" % (j, lines[j - 1].rstrip("\n"))
                if used + len(piece) > 3400:
                    block.append("...[контекст совпадения ограничен размером]")
                    break
                block.append(piece)
                used += len(piece) + 1
            if budget - len("\n".join(block)) < 0:
                out.append("...вывод ограничен размером: показано %d из %d "
                           "совпадений — сузьте pattern" % (shown, len(hits)))
                break
            budget -= len("\n".join(block))
            out.extend(block)
            shown += 1
        return "\n".join(out)[:8000]

    def write_file(self, path, content):
        p = self._resolve(path)
        self._guard_write(p)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(content if isinstance(content, str) else str(content))
        return "wrote %s (%d chars)" % (os.path.relpath(p, self.copy), len(content))

    def apply_patch(self, path, old, new):
        """Point edit: exact-context replacement, atomic, guarded.
        Refuses on 0 matches (anchor not found) and on >1 (ambiguous).
        Returns a compact unified diff of the changed region."""
        p = self._resolve(path)
        self._guard_write(p)
        if not os.path.isfile(p):
            raise ValueError("not a file: %s" % path)
        if not old or not isinstance(old, str) or not isinstance(new, str):
            raise ValueError("apply_patch requires string 'old' and 'new'")
        with open(p, encoding="utf-8") as f:
            src = f.read()
        n = src.count(old)
        if n == 0:
            raise ValueError(
                "PATCH FAILED: anchor not found in %s — скопируйте точный "
                "фрагмент (с пробелами/отступами) из свежего read_file" % path)
        if n > 1:
            raise ValueError(
                "PATCH FAILED: якорь неоднозначен — %d совпадений в %s; "
                "добавьте окружающих строк для уникальности" % (n, path))
        patched = src.replace(old, new, 1)
        tmp = p + ".patch.tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(patched)
        os.replace(tmp, p)  # atomic
        diff = "".join(difflib.unified_diff(
            src.splitlines(True), patched.splitlines(True),
            fromfile=os.path.relpath(p, self.copy), n=2))
        return "PATCH OK %s (%d -> %d chars)\n%s" % (
            os.path.relpath(p, self.copy), len(src), len(patched),
            diff[:4000] or "(no line changes)")

    def git_diff(self):
        r = subprocess.run(["git", "diff", "--stat"], cwd=self.copy,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        r2 = subprocess.run(["git", "diff"], cwd=self.copy,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
        tail = r2.stdout[-12000:]
        return (r.stdout or "") + "\n" + (tail or "(no diff)")

    def git_log(self, n=30, path=None):
        """Read-only history: git log --oneline -n N [-- path]."""
        n = max(1, min(int(n or 30), 60))
        cmd = ["git", "log", "--oneline", "-n", str(n)]
        if path:
            cmd += ["--", str(path)]
        r = subprocess.run(cmd, cwd=self.copy, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return r.stdout or r.stderr

    def git_show(self, ref):
        """Read-only: git show <ref> --stat (file list, not full diff)."""
        ref = str(ref).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{4,40}|HEAD|HEAD^|HEAD~\d+", ref):
            raise ValueError("ref must be a commit sha or HEAD form")
        r = subprocess.run(["git", "show", ref, "--stat", "--format=short"],
                           cwd=self.copy, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return (r.stdout or r.stderr)[-6000:]

    def run_tests(self):
        t0 = __import__("time").time()
        try:
            r = subprocess.run(
                [self.venv_py, "-m", "pytest"] + self.test_files +
                ["-q", "--no-header", "-p", "no:cacheprovider"],
                cwd=self.copy, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.run_timeout)
            rc, out = r.returncode, r.stdout[-4000:] + \
                (("\n[stderr tail]\n" + r.stderr[-1500:]) if r.stderr.strip() else "")
        except subprocess.TimeoutExpired:
            return "run_tests TIMEOUT after %ss" % self.run_timeout
        dt = __import__("time").time() - t0
        failed = re.findall(r"FAILED (?:[^:]+::)+(\w+)", r.stdout)
        brief = "rc=%s %.0fs failed=%s" % (rc, dt, failed[:12] if failed else "none")
        self.log.append(("run_tests", {}, rc == 0, brief))
        return "[run_tests %s]\n%s" % (brief, out)

    # ---- dispatch ----
    def call(self, name, args):
        if name == "finish":
            return "finish accepted"
        fn = getattr(self, name, None)
        if fn is None:
            raise ValueError("unknown tool %s" % name)
        return fn(**args)


TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List one directory of the isolated repo copy. "
                       "Path is relative to the repo root; '' = root.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file WINDOW with LINE NUMBERS: returns the "
                       "total line count, a truncated flag and "
                       "next_start_line for continuation. Big files are "
                       "read in successive windows (start_line 1, then "
                       "next_start_line, ...) or navigated via "
                       "search_in_file. Hard size cap ~12 KB.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "max_lines": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_in_file",
        "description": "Regex search inside one file: returns match line "
                       "numbers and a few context lines per match (up to 10 "
                       "matches). Use it to locate functions/anchors in big "
                       "files before read_file windows.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "pattern": {"type": "string"}},
            "required": ["path", "pattern"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create/overwrite a file inside the copy. "
                       "Verifier test files are PROTECTED (read-only).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "apply_patch",
        "description": "Point edit of an existing file: replaces the EXACT "
                       "string 'old' (must match once — copy it verbatim "
                       "with indentation from a fresh read_file) with 'new'. "
                       "Atomic; refuses on missing or ambiguous anchor; "
                       "returns a compact diff. Preferred over write_file "
                       "for small changes in big files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"}},
            "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "run_tests",
        "description": "Run the acceptance tests (pytest) in the copy. "
                       "Returns compact output tail: failed cell ids and "
                       "summary. Use it to check your work.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "git_diff",
        "description": "Show your uncommitted changes in the copy "
                       "(git diff).",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "git_log",
        "description": "Read-only commit history: git log --oneline -n N "
                       "[-- path]. Use it to explore how the copy evolved.",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer"},
            "path": {"type": "string"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "git_show",
        "description": "Read-only: git show <sha> --stat (commit message "
                       "and changed files). ref must be a commit sha or "
                       "HEAD/HEAD^/HEAD~N.",
        "parameters": {"type": "object", "properties": {
            "ref": {"type": "string"}},
            "required": ["ref"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Finish the task. Call ONLY when you believe the "
                       "work is complete.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "What you did and why"}},
            "required": ["summary"]}}},
]
