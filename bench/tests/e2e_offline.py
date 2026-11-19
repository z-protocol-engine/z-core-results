"""End-to-end run of agent41_fast on a real sympy task with the scripted fake model (0 real calls).

Checks: the agent loop runs, a de-indented edit is applied (stage 3), a cut-off call is not executed,
the request carries luna's settings, and the patch resolves the task with the official tests.
"""
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
OUT = "/home/user/bench_work/e2e_offline"
call = lambda i, name, args: {"id": "c%d" % i, "type": "function", "function": {"name": name, "arguments": args}}
script = [
    {"role": "assistant", "content": "", "tool_calls": [call(1, "bash", json.dumps({"command": "grep -n 'def as_set' -A1 sympy/sets/contains.py"}))]},
    {"role": "assistant", "content": "", "tool_calls": [call(2, "edit_file", json.dumps({
        "path": "sympy/sets/contains.py",
        "old_text": "def as_set(self):\n    raise NotImplementedError()",
        "new_text": "def as_set(self):\n    return self.args[1]"}))]},
    {"role": "assistant", "content": "", "tool_calls": [call(3, "finish", '{"summary": "half wri')]},
    {"role": "assistant", "content": "", "tool_calls": [call(4, "bash", json.dumps({"command": "python -c \"from sympy import *; x=Symbol('x'); print(Contains(x, S.Reals).as_set())\""}))]},
    {"role": "assistant", "content": "", "tool_calls": [call(5, "finish", json.dumps({"summary": "Contains.as_set now returns its set; verified."}))]},
]
os.makedirs(OUT, exist_ok=True)
json.dump(script, open(OUT + "/script.json", "w"))
log = OUT + "/requests.jsonl"
open(log, "w").close()
server = subprocess.Popen([sys.executable, os.path.join(BENCH, "fake_model.py"), "8799", OUT + "/script.json", log])
time.sleep(1)
try:
    row = {r["instance_id"]: r for r in json.load(open(run_bench.DATA))}[IID]
    repo = OUT + "/repo"
    run_bench.checkout(repo, row["base_commit"])
    open(OUT + "/statement.txt", "w").write(row["problem_statement"])
    env = dict(os.environ, PATH=os.path.join(run_bench.VENV, "bin") + ":" + os.environ["PATH"],
               OPENROUTER_BASE_URL="http://127.0.0.1:8799/api/v1", OPENROUTER_API_KEY="sk-or-v1-dummy",
               AGENT_OVERRIDES="{}")
    for key in ("SANDBOX_PROXY_URL", "VIRTUAL_ENV", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(key, None)
    with open(OUT + "/agent.log", "w") as handle:
        subprocess.run([sys.executable, os.path.join(BENCH, "load_agent.py"), os.path.join(BENCH, "agents", "agent41_fast.py"),
                        OUT + "/statement.txt", OUT + "/agent.patch"], cwd=repo, env=env, stdout=handle,
                       stderr=subprocess.STDOUT, timeout=600)
finally:
    server.kill()
patch = open(OUT + "/agent.patch").read()
agent_log = open(OUT + "/agent.log").read()
bodies = [json.loads(l) for l in open(log)]
result = run_bench.evaluate(row, patch, OUT)
checks = {
    "edit applied (indentation ignored)": "matched ignoring indentation" in agent_log or "return self.args[1]" in patch,
    "cut-off call not run": "arguments cut off -> not run" in agent_log,
    "reasoning high + all_turns sent": all(b.get("reasoning", {}).get("context") == "all_turns" for b in bodies),
    "seed/temperature/provider sent": all(b.get("seed") == 29 and b.get("temperature") == 0.0 and b.get("provider") for b in bodies),
    "only luna": {b["model"] for b in bodies} == {"openai/gpt-6-luna"},
    "task resolved by official tests": result["resolved"],
}
for name, ok in checks.items():
    print("%-36s %s" % (name, "OK" if ok else "FAIL"))
print("requests:", len(bodies), "max_tokens:", sorted({b.get("max_tokens") for b in bodies}))
sys.exit(0 if all(checks.values()) else 1)
