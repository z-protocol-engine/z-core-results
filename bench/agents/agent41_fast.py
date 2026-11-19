from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

# General problem-solving agent: read the instruction, explore the working directory, build the
# requested deliverable, check it, and hand back a unified diff of the working directory.

DRIVER_MODEL = os.getenv("RIDGES_AGENT_MODEL", "openai/gpt-6-luna")
DRIVER_TEMPERATURE = 0.0
REQUEST_SEED = 29
# provider route proven in our agent.py; dropped for the rest of the run after a few failures
PROVIDER_ROUTES = {"openai/gpt-6-luna": {"only": ["azure/eu"], "allow_fallbacks": False}}
PROVIDER_FALLBACK_AFTER = 3
CALL_TIMEOUT_SEC = 360.0
REVIEW_EFFORT = "high"
FALLBACK_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_WALL_SEC = 900.0
DEFAULT_COST_USD = 0.29
STARTUP_REFUSAL_SEC = 60.0
COST_SHARE = 0.92
GUARD_SHARE = 0.7
HANDIN_RESERVE_SEC = 40.0
CALL_MARGIN_SEC = 8.0
SHORT_WINDOW_SEC = 420.0
REVIEW_MIN_SEC = 150.0
SECOND_LOOK_MIN_SEC = float(os.getenv("A41_SECOND_LOOK_MIN_SEC", "1e9"))
SECOND_LOOK_MIN_MONEY = 0.10
SECOND_LOOK_GUARD_SHARE = 0.95
SECOND_LOOK_MODEL = DRIVER_MODEL
REPLY_TOKENS = 32000
REPLY_TOKENS_SHORT = 32000
THINK_TOKENS_MAX = 8000
THINK_TOKENS_MIN = 1024
THINK_MONEY_SHARE = 0.3
CALL_MONEY_SHARE = 0.55
THINK_SHARE_OF_CALL = 3
RELIEF_MODEL = DRIVER_MODEL
RELIEF_MODEL_TOKENS = 16000
CALL_TOKENS_MIN = 2000
PROMPT_COST_MARGIN = 1.2
RELIEF_CALLS = 2
RELIEF_TOKENS = 12000
RELIEF_MONEY_SHARE = 0.6
TURN_CEILING = 80
OUTPUT_CAP = 12000
READ_CAP = 30000
READ_BYTES_CAP = 5 * 1024 * 1024
TRANSCRIPT_CAP = 600000
SHRINK_TO = 0.6
COMMAND_CEILING_SEC = 300.0
COMMAND_SHARE = 0.3
PATCH_FILE_CAP = 10 * 1024 * 1024
PATCH_TOTAL_CAP = 20 * 1024 * 1024
SAVED_CAP = 10 * 1024 * 1024
CHECKPOINT_CAP = 40 * 1024 * 1024
IGNORED_SAVE_MAX = 1024 * 1024
INVENTORY_CAP = 300000
NEW_FILES_CAP = 20000
IMAGE_MAX_BYTES = 4 * 1024 * 1024
PDF_MAX_BYTES = 8 * 1024 * 1024
VIEWS_PER_TURN = 4
VIEW_BYTES_PER_TURN = 8 * 1024 * 1024
VIEWS_KEPT = 2
VIEWS_DROP_AT = 6
JUNK_PATH = re.compile(
    r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.cache|\.tox|\.ipynb_checkpoints)(/|$)"
    r"|\.(pyc|pyo)$|(^|/)\.DS_Store$|(^|/)nohup\.out$")
GIVING_UP = re.compile(r"\b(not (yet )?(complete|done|finished|solved)|incomplete|did not manage|gave up)\b", re.I)
PRICES = {
    "openai/gpt-6-luna": (0.11e-6, 0.55e-6),
    "~openai/gpt-luna-latest": (0.11e-6, 0.55e-6),
    "openai/gpt-6-luna-pro": (0.11e-6, 0.55e-6),
    "openai/gpt-6.1-sol": (2.2e-6, 11e-6),
}


def say(text: str) -> None:
    try:
        sys.stderr.write(text.rstrip() + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def num_env(name: str, default: float) -> float:
    try:
        value = float((os.getenv(name) or "").strip())
    except (TypeError, ValueError):
        return default
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        return default
    return value


def read_capped(path: str, cap: int) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size <= cap:
                return handle.read().decode("utf-8", "replace")
            head = handle.read(cap * 2 // 3)
            handle.seek(size - cap // 3)
            tail = handle.read()
        return (head.decode("utf-8", "replace") + "\n... [%d bytes omitted] ...\n" % (size - len(head) - len(tail))
                + tail.decode("utf-8", "replace"))
    except OSError:
        return ""


def clip(text: str, cap: int) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    tail = cap - head
    return text[:head] + "\n... [%d characters omitted] ...\n" % (len(text) - cap) + text[-tail:]


class Budget:
    def __init__(self) -> None:
        self.started = time.time()
        self.wall = num_env("AGENT_TIMEOUT", DEFAULT_WALL_SEC)
        self.deadline = self.started + self.wall - HANDIN_RESERVE_SEC - min(60.0, self.wall * 0.05)
        self.ceiling = num_env("RIDGES_MAX_COST_USD", DEFAULT_COST_USD)
        self.soft = self.ceiling * COST_SHARE
        self.spent = 0.0
        self.calls = 0

    def left(self) -> float:
        return self.deadline - time.time()

    def money_left(self) -> float:
        return self.soft - self.spent

    def short(self) -> bool:
        return self.wall <= SHORT_WINDOW_SEC

    def charge(self, model: str, usage: dict) -> float:
        self.calls += 1
        quoted = usage.get("cost") if isinstance(usage, dict) else None
        if isinstance(quoted, (int, float)) and quoted >= 0:
            cost = float(quoted)
        else:
            prompt_price, reply_price = PRICES.get(model, (2e-6, 8e-6))
            prompt = int(usage.get("prompt_tokens") or 0)
            cached = min(prompt, int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0))
            cost = ((prompt - cached) * prompt_price + cached * prompt_price * 0.1
                    + int(usage.get("completion_tokens") or 0) * reply_price)
        self.spent += cost
        return cost


class OutOfRoom(Exception):
    pass


class AttachmentRefused(Exception):
    pass


def base_urls() -> list:
    out = []
    proxy = (os.getenv("SANDBOX_PROXY_URL") or "").strip().rstrip("/")
    for candidate in ((os.getenv("OPENROUTER_BASE_URL") or "").strip().rstrip("/"),
                      proxy + "/api/v1" if proxy else "", proxy + "/v1" if proxy else "",
                      FALLBACK_BASE_URL):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def has_attachments(messages: list) -> bool:
    return any(isinstance(m.get("content"), list) and any(p.get("type") in ("image_url", "file") for p in m["content"])
               for m in messages)


def drop_attachments(messages: list, keep: int = 0) -> None:
    # replace the pictures and documents in all but the newest `keep` attachment messages with a short note
    seen = 0
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, list) or not any(p.get("type") in ("image_url", "file") for p in content):
            continue
        seen += 1
        if seen > keep:
            message["content"] = [p if p.get("type") not in ("image_url", "file") else
                                  {"type": "text", "text": "[attachment no longer shown; view the file again if needed]"}
                                  for p in content]


def for_model(messages: list, model: str) -> list:
    # private keys stay local; reasoning state goes back only to the model that produced it
    out = []
    for message in messages:
        if any(key.startswith("_") for key in message) or "reasoning_details" in message:
            same = message.get("_model") == model
            message = {k: v for k, v in message.items()
                       if not k.startswith("_") and (k != "reasoning_details" or same)}
        out.append(message)
    return out


def with_cache_marks(messages: list) -> list:
    # mark the system text and the two newest plain-text turns so the provider can reuse the prefix
    marked = list(messages)
    picks = [0] + [i for i in range(len(marked) - 1, 0, -1)
                   if marked[i].get("role") in ("user", "tool") and marked[i].get("content")][:2]
    for i in picks:
        content = marked[i].get("content") if i < len(marked) else None
        if isinstance(content, str) and content:
            marked[i] = dict(marked[i], content=[{"type": "text", "text": content,
                                                  "cache_control": {"type": "ephemeral"}}])
        elif isinstance(content, list) and content:
            parts = [dict(part) for part in content]
            for part in reversed(parts):
                if part.get("type") == "text":
                    part["cache_control"] = {"type": "ephemeral"}
                    break
            marked[i] = dict(marked[i], content=parts)
    return marked


class Model:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.bases = base_urls()
        self.key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("RIDGES_OPENROUTER_API_KEY")
                    or os.getenv("AI_PROXY_KEY") or "")
        self.effort = "medium" if budget.short() else "high"
        self.prompt_cost = 0.0
        self.relief = 0
        self.relief_model_ok = True
        self.guarded = False
        self.answered = False
        self.refused_since = 0.0
        self.driver = DRIVER_MODEL
        self.guard_share = GUARD_SHARE
        self.route_failures = 0

    def think_tokens(self) -> int:
        reply_price = PRICES.get(self.driver, (2e-6, 8e-6))[1]
        affordable = int(THINK_MONEY_SHARE * max(0.0, self.budget.money_left()) / reply_price)
        ceiling = THINK_TOKENS_MAX if self.effort in ("high", "xhigh") else THINK_TOKENS_MAX // 2
        return max(THINK_TOKENS_MIN, min(ceiling, affordable))

    def free_money(self) -> float:
        # money left after paying for the next prompt, judged by what the last prompt cost
        return self.budget.money_left() - self.prompt_cost * PROMPT_COST_MARGIN

    def call_tokens(self) -> int:
        # thinking and answer together may use at most a share of the free money, so one reply cannot end the run
        reply_price = PRICES.get(self.driver, (2e-6, 8e-6))[1]
        affordable = int(CALL_MONEY_SHARE * max(0.0, self.free_money()) / reply_price)
        ceiling = REPLY_TOKENS_SHORT if self.budget.short() else REPLY_TOKENS
        if self.relief:
            # right after a cut-off reply: room for a whole answer, little thinking
            roomy = int(RELIEF_MONEY_SHARE * max(0.0, self.free_money()) / reply_price)
            affordable = max(affordable, min(RELIEF_TOKENS, roomy))
        return max(CALL_TOKENS_MIN, min(ceiling + THINK_TOKENS_MAX, affordable))

    def body(self, messages: list, tools: list, model: str) -> dict:
        body = {
            "model": model,
            "messages": (with_cache_marks(for_model(messages, model)) if model.startswith("anthropic/")
                         else for_model(messages, model)),
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": min(self.call_tokens(), REPLY_TOKENS_SHORT if self.budget.short() else REPLY_TOKENS),
            "reasoning": {"effort": self.effort, "exclude": True},
            "usage": {"include": True},
        }
        if model == RELIEF_MODEL:
            # a cheap writer for the reply that did not fit: a whole long file costs cents, not dimes
            body["max_tokens"] = RELIEF_MODEL_TOKENS
            del body["reasoning"]
        elif model.startswith("anthropic/"):
            # an explicit thinking budget sized to the money left, at most a third of the call's tokens
            total = self.call_tokens()
            think = (THINK_TOKENS_MIN if self.relief
                     else max(THINK_TOKENS_MIN, min(self.think_tokens(), total // THINK_SHARE_OF_CALL)))
            body["reasoning"] = {"max_tokens": think, "exclude": True}
            body["max_tokens"] = max(total, think + CALL_TOKENS_MIN)
        else:
            # the reasoning is returned so it can be handed back on the next call
            body["reasoning"] = {"effort": self.effort, "context": "all_turns"}
            body["temperature"] = DRIVER_TEMPERATURE
            body["seed"] = REQUEST_SEED
            route = PROVIDER_ROUTES.get(model)
            if route and self.route_failures < PROVIDER_FALLBACK_AFTER:
                body["provider"] = dict(route)
        return body

    def ask(self, messages: list, tools: list) -> dict:
        driver_price = PRICES.get(self.driver, (2e-6, 8e-6))[1]
        if (not self.guarded and self.relief_model_ok and RELIEF_MODEL != self.driver
                and (self.budget.spent >= self.guard_share * self.budget.soft
                     or self.free_money() < CALL_TOKENS_MIN * driver_price)):
            # most of the money is spent: carry on with the cheap model instead of stopping
            self.guarded = True
            self.prompt_cost = 0.0
            say("[MODEL] $%.3f spent; continuing with %s" % (self.budget.spent, RELIEF_MODEL))
        model = (RELIEF_MODEL if (self.guarded or self.relief == RELIEF_CALLS) and self.relief_model_ok
                 and RELIEF_MODEL != self.driver else self.driver)
        payload = json.dumps(self.body(messages, tools, model)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        last = ""
        for attempt in range(6):
            for base in self.bases:
                room = self.budget.left() - CALL_MARGIN_SEC
                if room < 15:
                    raise OutOfRoom("no time left for another model call")
                if self.budget.money_left() <= 0 or self.free_money() < CALL_TOKENS_MIN * PRICES.get(model, (2e-6, 8e-6))[1]:
                    raise OutOfRoom("inference budget used up")
                try:
                    request = urllib.request.Request(base + "/chat/completions", data=payload, headers=headers)
                    began = time.time()
                    with urllib.request.urlopen(request, timeout=min(room, CALL_TIMEOUT_SEC)) as response:
                        reply = json.loads(response.read().decode("utf-8", "replace"))
                    if base != self.bases[0]:
                        self.bases.remove(base)
                        self.bases.insert(0, base)
                    usage = reply.get("usage") or {}
                    cost = self.budget.charge(model, usage)
                    if model == (RELIEF_MODEL if self.guarded else self.driver):
                        reply_price = PRICES.get(model, (2e-6, 8e-6))[1]
                        self.prompt_cost = max(0.0, cost - int(usage.get("completion_tokens") or 0) * reply_price)
                    choices = reply.get("choices") or []
                    if not choices:
                        last = "empty reply: %s" % str(reply.get("error") or "")[:200]
                        continue
                    message = choices[0].get("message") or {}
                    details = usage.get("prompt_tokens_details") or {}
                    say("[MODEL] call %d %s%.0fs $%.4f total $%.4f left %.0fs finish=%s in=%s cached=%s out=%s"
                        % (self.budget.calls, "" if model == self.driver else model + " ", time.time() - began, cost, self.budget.spent,
                           self.budget.left(), choices[0].get("finish_reason"), usage.get("prompt_tokens"),
                           details.get("cached_tokens"), usage.get("completion_tokens")))
                    self.answered = True
                    message["_finish"] = choices[0].get("finish_reason") or ""
                    message["_model"] = model
                    self.relief = RELIEF_CALLS if message["_finish"] == "length" else max(0, self.relief - 1)
                    return message
                except urllib.error.HTTPError as error:
                    text = ""
                    try:
                        text = error.read().decode("utf-8", "replace")[:300]
                    except Exception:
                        pass
                    last = "HTTP %s %s" % (error.code, text)
                    if error.code in (400, 404, 429, 502, 503) and PROVIDER_ROUTES.get(model):
                        self.route_failures += 1
                        if self.route_failures == PROVIDER_FALLBACK_AFTER:
                            say("[ROUTE] provider route dropped after %d failures" % self.route_failures)
                        payload = json.dumps(self.body(messages, tools, model)).encode("utf-8")
                    if model != self.driver and error.code in (400, 401, 403, 404, 422):
                        # the cheap writer is not available here: carry on with the driver
                        self.relief_model_ok = False
                        self.guarded = False
                        model = self.driver
                        payload = json.dumps(self.body(messages, tools, model)).encode("utf-8")
                        say("[MODEL] %s; continuing with %s" % (last[:120], model))
                        continue
                    if error.code in (402, 403) and not self.answered:
                        # before the first reply a refusal may be the service starting up: retried for a while
                        self.refused_since = self.refused_since or time.time()
                        if time.time() - self.refused_since > STARTUP_REFUSAL_SEC:
                            raise OutOfRoom(last)
                    elif "budget" in text.lower() or error.code in (402, 403):
                        raise OutOfRoom(last)
                    if error.code in (400, 413, 415, 422) and has_attachments(messages):
                        raise AttachmentRefused(last)
                    if error.code in (400, 413) and "context" in text.lower():
                        raise ValueError("context too long")
                except OutOfRoom:
                    raise
                except Exception as error:
                    # any transport fault (timeouts, cut-off replies, bad JSON) is retried, never fatal
                    last = "%s: %s" % (type(error).__name__, str(error)[:200])
                say("[MODEL] %s via %s" % (last, base))
            time.sleep(min(3.0 * 2 ** attempt, 15.0, max(0.0, self.budget.left() - 30)))
        raise OutOfRoom("model unavailable: " + last)


class Workspace:
    def __init__(self, root: str) -> None:
        self.root = root
        self.ensure_git()
        self.base = self.git(["rev-parse", "HEAD"], log=False).strip()
        self.branch = self.git(["symbolic-ref", "-q", "HEAD"], log=False).strip()
        self.start_entries = set(self.other_entries())
        self.inventory = self.scan_start()
        self.saved: dict = {}
        plain = set(self.untracked_plain())
        total = 0
        # keep the start content of untracked files, and of small ignored ones, so edits to them can be carried
        for path in sorted(self.inventory, key=lambda p: (p not in plain, self.inventory[p][0])):
            size = self.inventory[path][0]
            full = os.path.join(self.root, path)
            if (path not in plain and size > IGNORED_SAVE_MAX) or total + size > SAVED_CAP:
                continue
            if os.path.isfile(full) and not os.path.islink(full):
                try:
                    with open(full, "rb") as handle:
                        self.saved[path] = handle.read()
                    total += size
                except OSError:
                    pass

    def git(self, args: list, env: dict | None = None, timeout: float = 180.0,
            data: bytes | None = None, log: bool = True) -> str:
        done = subprocess.run(["git"] + args, cwd=self.root, capture_output=True, timeout=timeout,
                              env=env, input=data)
        if done.returncode != 0 and log:
            say("[GIT] %s failed: %s" % (" ".join(args[:3]), done.stderr.decode("utf-8", "replace")[:200]))
        return done.stdout.decode("utf-8", "surrogateescape")

    def ensure_git(self) -> None:
        if shutil.which("git") is None:
            return
        probe = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True)
        if probe.returncode == 0:
            return
        for command in (["git", "init", "-q"],
                        ["git", "-c", "user.email=agent@local", "-c", "user.name=agent", "add", "-A"],
                        ["git", "-c", "user.email=agent@local", "-c", "user.name=agent",
                         "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "start"]):
            subprocess.run(command, cwd=self.root, capture_output=True)

    def untracked_plain(self) -> list:
        return [p for p in self.git(["ls-files", "--others", "--exclude-standard", "-z"]).split("\0") if p]

    def other_entries(self) -> list:
        # untracked files and directories, ignored ones included, with whole new directories collapsed
        return [p for p in self.git(["ls-files", "--others", "--directory", "-z"]).split("\0") if p]

    def walk_entry(self, entry: str, skip_junk: bool):
        full = os.path.join(self.root, entry)
        if entry.endswith("/") and os.path.isdir(full) and not os.path.islink(full.rstrip("/")):
            for folder, dirs, files in os.walk(full):
                dirs[:] = [d for d in dirs if d != ".git" and not (skip_junk and JUNK_PATH.search(d + "/"))]
                for name in files:
                    yield os.path.relpath(os.path.join(folder, name), self.root)
        else:
            yield entry.rstrip("/")

    def scan_start(self) -> dict:
        # every file that already existed outside the index at the start, with its size and mtime
        found = {}
        for entry in self.start_entries:
            for rel in self.walk_entry(entry, skip_junk=False):
                try:
                    info = os.lstat(os.path.join(self.root, rel))
                except OSError:
                    continue
                found[rel] = (info.st_size, info.st_mtime_ns, info.st_mode)
                if len(found) >= INVENTORY_CAP:
                    say("[PATCH] start inventory capped at %d files" % INVENTORY_CAP)
                    return found
        return found

    def new_files(self) -> list:
        out = []
        for entry in self.other_entries():
            if JUNK_PATH.search(entry.rstrip("/")):
                continue
            for rel in self.walk_entry(entry, skip_junk=True):
                if rel in self.inventory or JUNK_PATH.search(rel):
                    continue
                out.append(rel)
                if len(out) > NEW_FILES_CAP:
                    say("[PATCH] more than %d new files; the rest are left out" % NEW_FILES_CAP)
                    return out
        return out

    def changed_saved(self) -> list:
        changed = []
        for path, data in self.saved.items():
            full = os.path.join(self.root, path)
            try:
                with open(full, "rb") as handle:
                    now = handle.read()
            except OSError:
                now = None
            if now != data:
                changed.append(path)
        return changed

    def changed_paths(self) -> list:
        tracked = [p for p in self.git(["diff", "--name-only", "-z", self.base]).split("\0") if p]
        seen, out = set(), []
        for path in tracked + self.new_files() + self.changed_saved():
            if path not in seen:
                seen.add(path)
                out.append(path)
        return out

    def patch(self) -> str:
        if not self.base:
            return ""
        keep, total = [], 0
        for path in self.changed_paths():
            if JUNK_PATH.search(path) or path in self.saved and path not in self.changed_saved():
                continue
            full = os.path.join(self.root, path)
            if os.path.islink(full):
                continue
            if os.path.isfile(full):
                size = os.path.getsize(full)
                if size > PATCH_FILE_CAP or total + size > PATCH_TOTAL_CAP:
                    say("[PATCH] left out %s (%d bytes)" % (path, size))
                    continue
                total += size
            keep.append(path)
        for path, (size, mtime, _) in self.inventory.items():
            if path in self.saved:
                continue
            try:
                info = os.lstat(os.path.join(self.root, path))
                if (info.st_size, info.st_mtime_ns) != (size, mtime):
                    say("[PATCH] cannot carry the change to %s (its start content was not kept)" % path)
            except OSError:
                pass
        if not keep:
            return ""
        as_binary = [path for path in keep if not self.plain_text(path)]
        index = tempfile.mktemp(prefix="agent-index-")
        env = dict(os.environ, GIT_INDEX_FILE=index)
        info_dir = os.path.join(self.root, self.git(["rev-parse", "--git-dir"]).strip() or ".git", "info")
        info_file = os.path.join(info_dir, "attributes")
        old_info = None
        try:
            if os.path.exists(info_file):
                with open(info_file, "rb") as handle:
                    old_info = handle.read()
            os.makedirs(info_dir, exist_ok=True)
            # the repository's info/attributes outranks every .gitattributes file
            with open(info_file, "wb") as handle:
                handle.write(old_info or b"")
                for path in as_binary:
                    line = "\n/" + re.sub(r"\s", "?", re.sub(r"([*?\[\]\\])", r"\\\1", path)) + " binary\n"
                    handle.write(os.fsencode(line))
            self.git(["read-tree", self.base], env=env)
            for path in keep:
                if path in self.saved:
                    blob = self.git(["hash-object", "-w", "--no-filters", "--stdin"], data=self.saved[path]).strip()
                    start_mode = (self.inventory.get(path) or (0, 0, 0))[2]
                    if blob:
                        self.git(["update-index", "--add", "--cacheinfo", "%s,%s,%s" % (
                            "100755" if start_mode & 0o100 else "100644", blob, path)], env=env)
            start_tree = self.git(["write-tree"], env=env).strip() or self.base
            # stage exact bytes: no clean filters or line-ending conversion, and files inside nested repositories too
            records = []
            for path in keep:
                full = os.path.join(self.root, path)
                if os.path.isfile(full) and not os.path.islink(full):
                    blob = self.git(["hash-object", "-w", "--no-filters", "--", path]).strip()
                    if not blob:
                        continue
                    mode = "100755" if os.stat(full).st_mode & 0o100 else "100644"
                    records.append(os.fsencode("%s %s\t%s" % (mode, blob, path)) + b"\0")
                elif not os.path.exists(full):
                    records.append(os.fsencode("0 %s\t%s" % ("0" * len(self.base), path)) + b"\0")
            self.git(["update-index", "-z", "--index-info"], env=env, data=b"".join(records))
            return self.git(["-c", "core.autocrlf=false", "diff", "--cached", "--binary", "--no-color",
                             "--no-renames", "--no-textconv", "--no-ext-diff", start_tree], env=env)
        finally:
            try:
                os.remove(index)
            except OSError:
                pass
            try:
                if old_info is None:
                    os.remove(info_file)
                else:
                    with open(info_file, "wb") as handle:
                        handle.write(old_info)
            except OSError:
                pass

    def checkpoint(self, paths: list) -> dict | None:
        # content and mode of every changed path (None when absent), or None when too large to hold
        kept, total = {}, 0
        for path in paths:
            full = os.path.join(self.root, path)
            if os.path.isfile(full) and not os.path.islink(full):
                total += os.path.getsize(full)
                if total > CHECKPOINT_CAP:
                    say("[RUN] result too large to checkpoint; skipping the second look")
                    return None
                with open(full, "rb") as handle:
                    kept[path] = (handle.read(), os.stat(full).st_mode & 0o777)
            elif not os.path.lexists(full):
                kept[path] = None
        return kept

    def rollback(self, kept: dict) -> None:
        for path in self.changed_paths():
            if path in kept:
                continue
            full = os.path.join(self.root, path)
            start = self.saved.get(path)
            if start is None:
                done = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (self.base, path)], cwd=self.root,
                                      capture_output=True, timeout=60)
                start = done.stdout if done.returncode == 0 else None
            try:
                if start is None:
                    if os.path.lexists(full) and not os.path.isdir(full):
                        os.remove(full)
                else:
                    os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
                    with open(full, "wb") as handle:
                        handle.write(start)
            except OSError:
                pass
        for path, value in kept.items():
            full = os.path.join(self.root, path)
            try:
                if value is None:
                    if os.path.lexists(full) and not os.path.isdir(full):
                        os.remove(full)
                    continue
                os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
                with open(full, "wb") as handle:
                    handle.write(value[0])
                os.chmod(full, value[1])
            except OSError:
                pass

    def plain_text(self, path: str) -> bool:
        # text diffs only for UTF-8 content without carriage returns, before and after
        versions = []
        try:
            with open(os.path.join(self.root, path), "rb") as handle:
                versions.append(handle.read())
        except OSError:
            pass
        if path in self.saved:
            versions.append(self.saved[path])
        else:
            done = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (self.base, path)], cwd=self.root,
                                  capture_output=True, timeout=60)
            if done.returncode == 0:
                versions.append(done.stdout)
        for data in versions:
            if b"\r" in data:
                return False
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                return False
        return True

    def restore(self) -> None:
        if not self.base:
            return
        created = self.new_files()
        if self.branch:
            self.git(["checkout", "-q", "-f", self.branch.replace("refs/heads/", "", 1)])
            self.git(["reset", "-q", "--hard", self.base])
        else:
            self.git(["checkout", "-q", "-f", "--detach", self.base])
        for entry in self.other_entries():
            if entry in self.start_entries:
                continue
            full = os.path.join(self.root, entry)
            try:
                if os.path.isdir(full) and not os.path.islink(full):
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    os.remove(full)
            except OSError:
                pass
        for path in created:
            full = os.path.join(self.root, path)
            if os.path.lexists(full) and not os.path.isdir(full):
                try:
                    os.remove(full)
                except OSError:
                    pass
        for path, data in self.saved.items():
            full = os.path.join(self.root, path)
            try:
                os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
                with open(full, "wb") as handle:
                    handle.write(data)
            except OSError:
                pass

    def applies(self, patch: str) -> bool:
        path = tempfile.mktemp(prefix="agent-check-", suffix=".diff")
        try:
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
                handle.write(patch)
            done = subprocess.run(["git", "apply", "--check", path], cwd=self.root, capture_output=True)
            if done.returncode != 0:
                say("[PATCH] check failed: %s" % done.stderr.decode("utf-8", "replace")[:300])
            return done.returncode == 0
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def salvage(self, patch: str) -> str:
        sections = re.split(r"(?m)^(?=diff --git )", patch)
        kept = [s for s in sections if s.startswith("diff --git ") and self.applies(s)]
        say("[PATCH] kept %d of %d file sections" % (len(kept), len([s for s in sections if s.startswith("diff --git ")])))
        return "".join(kept)


class Shell:
    def __init__(self, root: str, budget: Budget) -> None:
        self.root = root
        self.budget = budget
        self.jobs: dict = {}
        self.count = 0

    def run(self, command: str, timeout: float) -> str:
        room = self.budget.left() - 15
        if room < 5:
            raise OutOfRoom("no time left to run a command")
        # one blocking command may use at most part of the remaining time, so a slow step cannot end the run
        limit = max(5.0, min(timeout or 120.0, COMMAND_CEILING_SEC, max(30.0, room * COMMAND_SHARE)))
        sink = tempfile.NamedTemporaryFile(prefix="agent-out-", suffix=".log", delete=False)
        process = subprocess.Popen(["bash", "-lc", command], cwd=self.root, stdout=sink,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            try:
                process.wait(timeout=limit)
                note = "\n[exit code %d]" % process.returncode
                try:
                    # children left behind with & would keep running and slow everything after
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    process.kill()
                note = ("\n[stopped after %.0fs; use a shorter command, run it in the background with "
                        "background=true, or narrow the work]" % limit)
            sink.close()
            return read_capped(sink.name, OUTPUT_CAP) + note
        finally:
            try:
                os.remove(sink.name)
            except OSError:
                pass

    def start(self, command: str) -> str:
        self.count += 1
        name = "job%d" % self.count
        log = tempfile.NamedTemporaryFile(prefix="agent-job-", suffix=".log", delete=False)
        process = subprocess.Popen(["bash", "-lc", command], cwd=self.root, stdout=log,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
        self.jobs[name] = (process, log.name, command)
        return "started %s in the background; read it later with check_job" % name

    def check(self, name: str, wait: float) -> str:
        if name not in self.jobs:
            return "no job named %s" % name
        process, path, command = self.jobs[name]
        try:
            process.wait(timeout=max(0.0, min(wait or 0, self.budget.left() - 20, 120)))
        except subprocess.TimeoutExpired:
            pass
        text = read_capped(path, OUTPUT_CAP)
        state = "still running" if process.poll() is None else "finished with exit code %d" % process.returncode
        return clip(text, OUTPUT_CAP) + "\n[%s: %s]" % (name, state)

    def close(self) -> None:
        for process, path, _ in self.jobs.values():
            if process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    pass
            try:
                os.remove(path)
            except OSError:
                pass


TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the working directory (bash -lc). Output is truncated. Use background=true for long builds or servers and read them with check_job.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "number", "description": "seconds (not milliseconds), default 120"},
            "background": {"type": "boolean"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "check_job",
        "description": "Read the output of a background job, optionally waiting up to wait seconds.",
        "parameters": {"type": "object", "properties": {
            "job": {"type": "string"}, "wait": {"type": "number"}}, "required": ["job"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file with line numbers. Use start and count for large files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start": {"type": "integer"}, "count": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content, or add the content to the end of the file with append=true. Write a long file in parts of at most about 150 lines: the first part, then appends.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace one exact occurrence of old_text with new_text in a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "view_file",
        "description": "Look at a picture (PNG, JPEG, GIF, WebP) or a PDF yourself; it is shown to you right after this step. Use it whenever what a picture, scan, chart or document page shows matters.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Call when the deliverable is complete and checked. Give a short summary of what was produced and how it was checked.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
                       "required": ["summary"]}}},
]

BRIEF = """You are an expert engineer solving one unfamiliar task inside a container. You have a shell and file tools.
The working directory is %(root)s. The task statement says what to deliver and where.

What is kept: when you finish, the regular files you created or changed inside %(root)s are kept (their content
and executable bit), and nothing else is. Version-control metadata such as a .git folder is not kept. Anything installed or written elsewhere, running processes, environment variables and symlinks are lost.
So:
- Put every deliverable (code, scripts, output files, answers) at the exact path and in the exact format the task
  states. If the task names a path outside %(root)s, still create it, and also keep any source you need inside %(root)s.
- There is no internet and no package index. Use the tools, languages and libraries already in the container;
  check what exists (which, --version, python3 -c "import x") before relying on it.
- When the task asks for an answer, give exactly one definite answer in the requested format, with no alternatives
  or hedging next to it.
- If the deliverable must run later (a script, a program, a service), make it work from a clean start: no reliance
  on files you created outside %(root)s or on state from your session.
- A program you write should work for any valid input the statement allows, not only for the example data, models
  or files in the container. Do not build in sizes, counts, values or answers you observed while exploring unless
  the statement fixes them; derive them at run time. Test it on variations you construct (other sizes, other seeds,
  edge cases).
- Do scratch work (test scripts, notes, temporary data, experiments) in %(scratch)s, outside %(root)s, so that
  only the real deliverables end up in %(root)s. Leave no stray files in %(root)s.
- The deliverable will be used beyond the examples: on other inputs, by other programs, and called exactly as the
  statement describes. Write down early the interface the statement fixes (names, argument order, positional or
  named arguments, defaults, input and output formats, commands, error behaviour) and build to that. Where the
  statement leaves the calling form open, also accept the conventional forms for that kind of program.
- Run each usage the statement shows literally, from a fresh shell, not only the way your own code calls it. When
  a result is a number, estimate what it should be from the domain and the data, and make sure it agrees.
- Keep a deliverable's dependencies small: prefer the standard library and the libraries the task's own code, files
  or statement already use. A module that merely happens to be importable in your session may be missing where the
  deliverable runs later.

Way of working:
1. Read the task statement carefully. Write down the exact deliverables, paths, formats, interfaces, and every
   stated requirement and constraint.
2. Explore: list the working directory, read the relevant files, look at existing tests, examples and data.
3. Plan the simplest approach that meets every requirement. Prefer robust, standard solutions.
4. Build it. Make edits with the file tools; run things with the shell.
5. Check your work against the statement: run it, test edge cases from the statement, verify output format and file
   locations exactly. If the task gives example inputs or a check command, run them. Fix and re-check.
6. When everything is in place and checked, call finish.

Keep each reply short: write a long file in several parts (create it, then append) rather than in one huge reply.
Be efficient: batch independent tool calls in one reply, keep command output short (head, tail, grep), and do not
repeat work. Time left and budget are shown to you; leave time to check your result before the clock runs out.
A partial but working deliverable is far better than an unfinished one. As soon as the deliverable is verified
(the reproduction passes and the relevant existing tests pass), call finish: speed matters."""


SECOND_LOOK = """You are an expert engineer giving a second, independent look at a task inside a container. A colleague
has worked on the task statement below in %(root)s and believes the deliverable is finished. Treat that as a claim
to test, not as a fact. You have a shell and file tools.

What is kept: when you finish, the regular files inside %(root)s are kept as they are then (content and executable
bit), and nothing else is. There is no internet and no package index; use what the container already has.

Your job:
1. Read the statement closely. List every deliverable and requirement: paths, names, formats, values, interfaces,
   commands, constraints, and anything the statement says must not change.
2. Look at what is there now and run the deliverable yourself, from a clean start, exactly as the statement says
   it will be used.
3. Probe beyond the example data in the container wherever the statement allows other inputs: other sizes, seeds,
   edge cases, the exact output format. Look for values, sizes or answers that were built in from the examples.
4. Change something only when a command you ran shows a defect. Keep everything that works; never remove a
   deliverable without putting a working one in its place. Re-run your checks after any fix.
5. Do scratch work in %(scratch)s, not in %(root)s. Remove stray files from %(root)s that are not deliverables,
   then call finish with what you checked and what you changed.

Be efficient: batch independent tool calls and keep command output short. Time left and budget are shown to you."""


class Agent:
    def __init__(self, statement: str, root: str) -> None:
        self.statement = statement
        self.root = root
        self.budget = Budget()
        self.workspace = Workspace(root)
        self.shell = Shell(root, self.budget)
        self.model = Model(self.budget)
        self.finished = False
        self.finish_asks = 0
        self.reviewed = False
        self.pushed = False
        self.second_look = False
        self.money_wrapped = 0
        self.scratch = outside_scratch(root)
        self.summary = ""
        self.wrapped = 0
        self.views = []

    def resolve(self, path: str) -> str:
        path = os.path.expanduser(str(path or ""))
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    def tool(self, name: str, args: dict) -> str:
        if name == "bash":
            command = str(args.get("command") or "")
            if not command.strip():
                return "error: empty command"
            if args.get("background"):
                return self.shell.start(command)
            return self.shell.run(command, float(args.get("timeout") or 120))
        if name == "check_job":
            return self.shell.check(str(args.get("job") or ""), float(args.get("wait") or 0))
        if name == "read_file":
            path = self.resolve(args.get("path"))
            try:
                with open(path, "r", errors="replace") as handle:
                    lines = handle.read(READ_BYTES_CAP).splitlines()
            except OSError as error:
                return "error: %s" % error
            start = max(1, int(args.get("start") or 1))
            count = int(args.get("count") or 0) or len(lines)
            chunk = lines[start - 1:start - 1 + count]
            body = "\n".join("%6d  %s" % (start + i, line) for i, line in enumerate(chunk))
            return clip(body, READ_CAP) + "\n[%d lines in file]" % len(lines)
        if name == "write_file":
            path = self.resolve(args.get("path"))
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "a" if args.get("append") else "w") as handle:
                    handle.write(str(args.get("content") or ""))
            except OSError as error:
                return "error: %s" % error
            if args.get("append"):
                return "appended to %s" % path
            return "wrote %s" % path + self.syntax_note(path)
        if name == "edit_file":
            path = self.resolve(args.get("path"))
            old, new = str(args.get("old_text") or ""), str(args.get("new_text") or "")
            try:
                with open(path, "r", errors="surrogateescape") as handle:
                    text = handle.read()
            except OSError as error:
                return "error: %s" % error
            hits = text.count(old) if old else 0
            if hits != 1:
                return "error: old_text found %d times; it must match exactly once" % hits
            with open(path, "w", errors="surrogateescape") as handle:
                handle.write(text.replace(old, new, 1))
            return "edited %s" % path + self.syntax_note(path)
        if name == "view_file":
            return self.view_file(self.resolve(args.get("path")))
        if name == "finish":
            return self.finish_check(str(args.get("summary") or ""))
        return "error: unknown tool %s" % name

    def view_file(self, path: str) -> str:
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as handle:
                head = handle.read(16)
        except OSError as error:
            return "error: %s" % error
        kinds = ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"),
                 (b"%PDF", "application/pdf"))
        mime = next((kind for sig, kind in kinds if head.startswith(sig)), None)
        if mime is None and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            mime = "image/webp"
        if mime is None:
            return "error: %s is not a PNG, JPEG, GIF or WebP picture or a PDF; convert a copy first" % path
        limit = PDF_MAX_BYTES if mime == "application/pdf" else IMAGE_MAX_BYTES
        if size > limit:
            return ("error: %s is %d bytes; make a smaller copy (scaled down, cropped, or only the pages that "
                    "matter) in %s and view that" % (path, size, self.scratch))
        if len(self.views) >= VIEWS_PER_TURN:
            return "error: at most %d files per step; view the rest in the next step" % VIEWS_PER_TURN
        if sum(size for _, _, size in self.views) + size > VIEW_BYTES_PER_TURN:
            return "error: too much to show in one step; view %s in the next step" % path
        with open(path, "rb") as handle:
            url = "data:%s;base64,%s" % (mime, base64.b64encode(handle.read()).decode())
        if mime == "application/pdf":
            part = {"type": "file", "file": {"filename": os.path.basename(path), "file_data": url}}
        else:
            part = {"type": "image_url", "image_url": {"url": url}}
        self.views.append((path, part, size))
        return "%s is shown in the next message" % path

    def syntax_note(self, path: str) -> str:
        if path.endswith(".py"):
            done = subprocess.run([sys.executable, "-m", "py_compile", path], capture_output=True)
            if done.returncode != 0:
                return "\nWARNING: the file does not compile:\n" + clip(done.stderr.decode("utf-8", "replace"), 1500)
        return ""

    def finish_check(self, summary: str) -> str:
        missing = []
        for path in sorted(set(re.findall(r"(?<![\w.-])(/[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]{1,8})\b", self.statement))):
            if not os.path.abspath(path).startswith(os.path.abspath(self.root) + os.sep):
                continue
            if not os.path.exists(path):
                missing.append(path)
        changed = self.workspace.changed_paths()
        running = [name for name, (process, _, _) in self.shell.jobs.items() if process.poll() is None]
        outside = [m for m in missing if not os.path.abspath(m).startswith(os.path.abspath(self.root) + os.sep)]
        if self.finish_asks < 2 and self.budget.left() > 60:
            if running and self.finish_asks == 0:
                self.finish_asks += 1
                return ("Not finished: background jobs are still running (%s). They are stopped when you finish. "
                        "Wait for them or confirm their output is already saved, then call finish again." % ", ".join(running))
            if not changed:
                self.finish_asks += 1
                return ("Not finished: nothing in %s has changed, so there is no deliverable to collect. "
                        "Create the requested files inside the working directory and call finish again." % self.root)
            if missing:
                self.finish_asks += 1
                return ("Not finished: the task statement mentions these paths, and they do not exist yet:\n"
                        + "\n".join("  " + m for m in missing[:12])
                        + ("\nPaths outside %s are not kept; make sure every deliverable you can place lives under %s."
                           % (self.root, self.root) if outside else "")
                        + "\nCreate them if they are deliverables (or confirm they are only inputs), then call finish again.")
        if self.second_look:
            self.finished = True
            say("[FINISH] second look: %s" % summary[:300])
            return "finished"
        window = max(1.0, self.budget.deadline - self.budget.started)
        if (not self.pushed and GIVING_UP.search(summary) and self.budget.left() > window * 0.3
                and self.budget.money_left() > 0.03):
            self.pushed = True
            return ("Not finished: your summary says part of the task is not done, and %.0f seconds remain. "
                    "Use them. List what is still missing, then try a different route for it: look for other "
                    "tools, libraries or files already in the container (search the file system, check python "
                    "modules, man pages, existing scripts), or write the missing piece yourself. Call finish again "
                    "when it is done or when every route is exhausted." % self.budget.left())
        if not self.reviewed and not self.budget.short() and self.budget.left() > REVIEW_MIN_SEC and self.budget.money_left() > 0.02:
            self.reviewed = True
            self.model.effort = REVIEW_EFFORT
            shown = self.deliverable_view(changed)
            return ("Before finishing, review the result against the task statement once more.\n"
                    "1. List every requirement in the statement: each path, name, format, value, interface, "
                    "command and constraint, including the small ones.\n"
                    "2. For each one, run a quick command that shows it holds now (run the program, print the "
                    "file, compare the exact strings). Check edge cases the statement mentions.\n"
                    "3. These files differ from the start state:\n" + shown + "\n"
                    "   Make sure each change is intended; undo changes to files the task did not ask you to change, "
                    "and remove scratch files you no longer need.\n"
                    "4. If you wrote a program, confirm it does not depend on the particular example data in the "
                    "container: nothing observed during exploration is built in unless the statement fixes it, and it "
                    "still works on a variation you construct now.\n"
                    "Fix anything that does not hold, then call finish again with what you checked.")
        self.finished = True
        self.summary = summary
        say("[FINISH] %s" % summary[:300])
        return "finished"

    def deliverable_view(self, changed: list) -> str:
        # what will be handed in: each changed path, and the exact bytes of small files
        rows = []
        for path in changed[:40]:
            full = os.path.join(self.root, path)
            if not os.path.lexists(full):
                rows.append("  %s  (deleted)" % path)
                continue
            try:
                size = os.path.getsize(full)
                if size <= 300:
                    with open(full, "rb") as handle:
                        rows.append("  %s  exact bytes: %r" % (path, handle.read()))
                else:
                    rows.append("  %s  (%d bytes)" % (path, size))
            except OSError:
                rows.append("  %s" % path)
        if len(changed) > 40:
            rows.append("  ... and %d more" % (len(changed) - 40))
        return "\n".join(rows)

    def transcript_size(self, messages: list) -> int:
        return sum(len(json.dumps({k: v for k, v in m.items() if k != "reasoning_details"}))
                   if not isinstance(m.get("content"), list) else 2000 for m in messages)

    def shrink(self, messages: list) -> None:
        # once over the cap, trim well below it so the kept prefix stays stable for several turns
        if sum(1 for m in messages if isinstance(m.get("content"), list)) > VIEWS_DROP_AT:
            # dropping rewrites history and costs a cache miss, so do it rarely and all at once
            drop_attachments(messages, VIEWS_KEPT)
        if self.transcript_size(messages) <= TRANSCRIPT_CAP:
            return
        target = TRANSCRIPT_CAP * SHRINK_TO
        for message in messages[2:-8]:
            # old reasoning state is the bulkiest part of a turn and is not needed to continue
            message.pop("reasoning_details", None)
        if self.transcript_size(messages) <= target:
            return
        for keep_tail, limit in ((8, 600), (6, 200), (4, 80)):
            for message in messages[2:-keep_tail]:
                content = message.get("content") or ""
                if message.get("role") in ("tool", "assistant") and len(content) > limit:
                    message["content"] = content[:limit // 2] + "\n... [older text trimmed] ...\n" + content[-limit // 2:]
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    if len(function.get("arguments") or "") > limit * 4:
                        function["arguments"] = trim_arguments(function["arguments"], limit)
            if self.transcript_size(messages) <= target:
                return

    def status_line(self) -> str:
        return "[time left %.0fs, budget left $%.3f]" % (self.budget.left(), self.budget.money_left())

    def run(self) -> None:
        listing = self.shell.run("pwd; ls -la | head -50; git status --short | head -20", 30)
        messages = [
            {"role": "system", "content": BRIEF % {"root": self.root, "scratch": self.scratch}},
            {"role": "user", "content": "TASK STATEMENT\n\n" + self.statement
             + "\n\nWORKING DIRECTORY\n" + listing + "\n\n" + self.status_line()
             + ("\n\nThis is a short run: act quickly, make the deliverable early, then refine." if self.budget.short() else "")},
        ]
        self.loop(messages)
        if (self.finished and not self.budget.short() and self.budget.left() > SECOND_LOOK_MIN_SEC
                and self.budget.money_left() >= SECOND_LOOK_MIN_MONEY and not self.model.guarded):
            self.look_again()

    def look_again(self) -> None:
        # a fresh pair of eyes: new conversation, no memory of how the result was reached
        changed = self.workspace.changed_paths()
        if not changed:
            return
        kept = self.workspace.checkpoint(changed)
        if kept is None:
            return
        self.second_look = True
        self.finished = False
        self.wrapped = 1
        self.model.effort = REVIEW_EFFORT
        # a different model checks the result, with room to finish its check before any step-down
        self.model.driver = SECOND_LOOK_MODEL
        self.model.guard_share = SECOND_LOOK_GUARD_SHARE
        self.model.relief = 0
        self.model.prompt_cost = 0.0
        shown = self.deliverable_view(changed)
        say("[RUN] second look by %s with %.0fs and $%.3f left"
            % (self.model.driver, self.budget.left(), self.budget.money_left()))
        messages = [
            {"role": "system", "content": SECOND_LOOK % {"root": self.root, "scratch": self.scratch}},
            {"role": "user", "content": "TASK STATEMENT\n\n" + self.statement
             + "\n\nFILES CHANGED FROM THE START STATE\n" + shown
             + "\n\nYOUR COLLEAGUE'S NOTE (unverified)\n" + clip(self.summary, 2000)
             + "\n\n" + self.status_line()},
        ]
        self.loop(messages)
        if not self.finished:
            # the second look did not complete, so keep the result as it stood before it began
            say("[RUN] second look did not finish; restoring the earlier result")
            self.workspace.rollback(kept)
        self.finished = True

    def loop(self, messages: list) -> None:
        for turn in range(1, TURN_CEILING + 1):
            if self.finished:
                return
            left = self.budget.left()
            if left < 20 or self.budget.money_left() <= 0:
                say("[LOOP] stopping: time %.0fs money $%.3f" % (left, self.budget.money_left()))
                return
            window = max(1.0, self.budget.deadline - self.budget.started)
            spent_share = self.budget.spent / max(1e-9, self.budget.soft)
            if self.money_wrapped == 0 and spent_share > 0.85:
                self.money_wrapped = 1
            if self.money_wrapped == 0 and spent_share > 0.6:
                self.money_wrapped = 1
                messages.append({"role": "user", "content": "Most of the inference budget is used %s. Put a working "
                                 "deliverable in place now if there is none yet, then keep calls short." % self.status_line()})
            elif self.money_wrapped == 1 and spent_share > 0.85:
                self.money_wrapped = 2
                messages.append({"role": "user", "content": "The inference budget is almost gone %s. Save the "
                                 "deliverable in place now and call finish." % self.status_line()})
            if self.wrapped == 0 and left < window * 0.25:
                self.wrapped = 1
                messages.append({"role": "user", "content": "Time is running short %s. Make sure the deliverable "
                                 "exists at the stated path and works; finish soon." % self.status_line()})
            elif self.wrapped == 1 and left < window * 0.10:
                self.wrapped = 2
                messages.append({"role": "user", "content": "Almost out of time %s. Save what you have in place now "
                                 "and call finish." % self.status_line()})
            self.shrink(messages)
            try:
                reply = self.model.ask(messages, TOOLS)
            except ValueError:
                for message in messages[2:-4]:
                    if message.get("role") == "tool":
                        message["content"] = (message.get("content") or "")[:200]
                continue
            except AttachmentRefused as refused:
                say("[LOOP] attachments refused (%s); resending without them" % str(refused)[:120])
                drop_attachments(messages)
                messages.append({"role": "user", "content": "The files you asked to view could not be shown, so "
                                 "you have not seen them. Get their content another way with the tools in the "
                                 "container (text extraction, image libraries, OCR if installed). " + self.status_line()})
                continue
            except OutOfRoom as stop:
                say("[LOOP] %s" % stop)
                return
            if self.second_look and self.model.guarded:
                # the cheap model must not re-judge the driver's result: end the second look before acting
                say("[LOOP] budget step-down during the second look; ending it")
                return
            calls = [c for c in (reply.get("tool_calls") or []) if isinstance(c, dict)]
            entry = {"role": "assistant", "content": reply.get("content") or ""}
            if reply.get("reasoning_details"):
                entry["reasoning_details"] = reply["reasoning_details"]
                entry["_model"] = reply.get("_model")
            if calls:
                entry["tool_calls"] = [{"id": c.get("id") or "call_%d_%d" % (turn, i), "type": "function",
                                        "function": {"name": (c.get("function") or {}).get("name", ""),
                                                     "arguments": valid_arguments((c.get("function") or {}).get("arguments"))}}
                                       for i, c in enumerate(calls)]
            messages.append(entry)
            if not calls:
                cut = (" Your last reply was cut off at the length limit: nothing in it was run or saved. Write at "
                       "most about 150 lines per tool call (write_file, then write_file with append=true)."
                       if reply.get("_finish") == "length" else "")
                messages.append({"role": "user", "content": "Continue with a tool call. When the deliverable is "
                                 "complete and checked, call finish." + cut + " " + self.status_line()})
                continue
            for call in entry["tool_calls"]:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                    result = self.tool(name, args)
                except OutOfRoom as stop:
                    result = "error: %s" % stop
                except Exception as error:
                    result = "error: %s: %s" % (type(error).__name__, str(error)[:300])
                say("[TOOL] %s %s -> %d chars" % (name, str(call["function"]["arguments"])[:120].replace("\n", " "), len(result)))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            if not self.finished:
                messages[-1]["content"] += "\n" + self.status_line()
            if reply.get("_finish") == "length":
                messages[-1]["content"] += ("\nYour last reply was cut off at the length limit: nothing in it was run "
                                            "or saved. Write at most about 150 lines per tool call: write_file with "
                                            "the first part, then write_file with append=true for each next part.")
            if self.views:
                messages.append({"role": "user", "content": [part for _, part, _ in self.views] + [
                    {"type": "text", "text": "Above: requested view of " + ", ".join(path for path, _, _ in self.views)}]})
                self.views = []


def outside_scratch(root: str) -> str:
    base = os.path.abspath(root) + os.sep
    for parent in (None, "/tmp", "/var/tmp", os.path.dirname(os.path.abspath(root)) or "/"):
        try:
            path = tempfile.mkdtemp(prefix="scratch-", dir=parent)
        except OSError:
            continue
        if not (os.path.abspath(path) + os.sep).startswith(base):
            return path
        shutil.rmtree(path, ignore_errors=True)
    return tempfile.mkdtemp(prefix="scratch-")


def valid_arguments(arguments) -> str:
    # arguments cut off mid-JSON would be replayed every turn and can make the provider refuse the whole request
    try:
        json.loads(arguments or "{}")
        return arguments or "{}"
    except (TypeError, ValueError):
        return "{}"


def trim_arguments(arguments: str, limit: int) -> str:
    # shorten long string values but keep every key, so the call still matches its result
    try:
        value = json.loads(arguments)
    except ValueError:
        return json.dumps({"note": "older arguments trimmed"})
    if not isinstance(value, dict):
        return json.dumps({"note": "older arguments trimmed"})
    for key, item in value.items():
        if isinstance(item, str) and len(item) > limit:
            value[key] = item[:limit // 2] + "\n... [older text trimmed] ...\n" + item[-limit // 2:]
    return json.dumps(value)


def encodes(text: str) -> bool:
    try:
        text.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def agent_main(input: dict) -> str:
    statement = str((input or {}).get("problem_statement") or "").strip()
    root = os.getcwd()
    agent = None
    patch = ""
    try:
        agent = Agent(statement, root)
        say("[RUN] wall %.0fs budget $%.2f model %s effort %s root %s"
            % (agent.budget.wall, agent.budget.ceiling, DRIVER_MODEL, agent.model.effort, root))
        agent.run()
    except Exception as error:
        say("[RUN] stopped on %s: %s" % (type(error).__name__, str(error)[:300]))
    finally:
        if agent is not None:
            agent.shell.close()
    try:
        workspace = agent.workspace if agent is not None else Workspace(root)
        patch = workspace.patch()
        workspace.restore()
        if not encodes(patch):
            sections = re.split(r"(?m)^(?=diff --git )", patch)
            patch = "".join(part for part in sections if encodes(part))
            say("[PATCH] dropped sections that are not valid UTF-8")
        if patch.strip() and not workspace.applies(patch):
            patch = workspace.salvage(patch)
    except Exception as error:
        say("[PATCH] could not be built: %s" % error)
    say("[RUN] done: patch %d bytes, $%.4f over %d calls"
        % (len(patch), agent.budget.spent if agent else 0.0, agent.budget.calls if agent else 0))
    return patch
