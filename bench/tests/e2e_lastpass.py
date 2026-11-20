"""Offline check (scripted fake model, 0 real calls): an agent that only explores gets the last-pass notice,
then an edit-only turn; multi-command bash and the empty-grep hint work."""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
sys.path.insert(0, BENCH)
import run_bench  # noqa: E402

IID = "sympy__sympy-23950"
OUT = "/home/user/bench_work/e2e_lastpass"
call = lambda i, name, args: {"id": "c%d" % i, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
script = [{"role": "assistant", "content": "", "tool_calls": [call(1, "bash", {"commands": [
    "grep -n 'def as_set' sympy/sets/contains.py", "grep -rn 'no_such_identifier_xyz' sympy/sets"]})]}]
script += [{"role": "assistant", "content": "", "tool_calls": [call(i, "bash", {"command": "echo exploring %d" % i})]}
           for i in range(2, 30)]
os.makedirs(OUT, exist_ok=True)
json.dump(script, open(OUT + "/script.json", "w"))
log = OUT + "/requests.jsonl"
open(log, "w").close()
server = subprocess.Popen([sys.executable, os.path.join(BENCH, "fake_model.py"), "8798", OUT + "/script.json", log])
time.sleep(1)
try:
    row = {r["instance_id"]: r for r in json.load(open(run_bench.DATA))}[IID]
    repo = OUT + "/repo"
    run_bench.checkout(repo, row["base_commit"])
    open(OUT + "/statement.txt", "w").write(row["problem_statement"])
    env = dict(os.environ, PATH=os.path.join(run_bench.VENV, "bin") + ":" + os.environ["PATH"],
               OPENROUTER_BASE_URL="http://127.0.0.1:8798/api/v1", OPENROUTER_API_KEY="sk-or-v1-dummy",
               AGENT_OVERRIDES="{}")
    for key in ("SANDBOX_PROXY_URL", "VIRTUAL_ENV", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(key, None)
    with open(OUT + "/agent.log", "w") as handle:
        subprocess.run([sys.executable, os.path.join(BENCH, "load_agent.py"), os.path.join(BENCH, "agents", "agent41_fast.py"),
                        OUT + "/statement.txt", OUT + "/agent.patch"], cwd=repo, env=env, stdout=handle,
                       stderr=subprocess.STDOUT, timeout=600)
finally:
    server.kill()
agent_log = open(OUT + "/agent.log").read()
bodies = [json.loads(l) for l in open(log)]
tools_per_call = [sorted(t["function"]["name"] for t in b.get("tools") or []) for b in bodies]
first_tool_result = next(m["content"] for m in bodies[1]["messages"] if m.get("role") == "tool")
checks = {
    "multi-command bash labelled": "=== $ grep -n 'def as_set'" in first_tool_result,
    "empty grep hint": "no matches" in first_tool_result,
    "calls-left status line": "more replies at your pace" in first_tool_result,
    "last-pass notice fired": "[LAST-PASS]" in agent_log,
    "edit-only turn offered": ["edit_file"] in tools_per_call,
    "normal tools before": len(tools_per_call[0]) > 2,
}
for name, ok in checks.items():
    print("%-32s %s" % (name, "OK" if ok else "FAIL"))
sys.exit(0 if all(checks.values()) else 1)
