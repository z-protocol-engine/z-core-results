"""Run agent41_fast on one bench task with a Claude sub-agent as its model (through the bridge), then grade it.

usage: python run_bridge_task.py INSTANCE PORT   -> /home/user/bench_work/bridge/<INSTANCE>/result.json
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

iid = sys.argv[1] if "__" in sys.argv[1] else "sympy__sympy-" + sys.argv[1]
port = int(sys.argv[2])
out = "/home/user/bench_work/bridge/" + iid
subprocess.run(["rm", "-rf", out])
os.makedirs(out + "/ipc")
server = subprocess.Popen([sys.executable, os.path.join(HERE, "bridge_model.py"), str(port), out + "/ipc"])
time.sleep(1)
row = {r["instance_id"]: r for r in json.load(open(run_bench.DATA))}[iid]
repo = out + "/repo"
run_bench.checkout(repo, row["base_commit"])
open(out + "/statement.txt", "w").write(row["problem_statement"])
env = dict(os.environ, PATH=os.path.join(run_bench.VENV, "bin") + ":" + os.environ["PATH"],
           OPENROUTER_BASE_URL="http://127.0.0.1:%d/api/v1" % port, OPENROUTER_API_KEY="sk-or-v1-dummy",
           AGENT_TIMEOUT="5400", AGENT_OVERRIDES=json.dumps({"CALL_TIMEOUT_SEC": 3600.0}))
for key in ("SANDBOX_PROXY_URL", "VIRTUAL_ENV", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    env.pop(key, None)
began = time.time()
try:
    with open(out + "/agent.log", "w") as handle:
        subprocess.run([sys.executable, os.path.join(BENCH, "load_agent.py"), os.path.join(BENCH, "agents", "agent41_fast.py"),
                        out + "/statement.txt", out + "/agent.patch"], cwd=repo, env=env, stdout=handle,
                       stderr=subprocess.STDOUT, timeout=6000)
finally:
    patch = open(out + "/agent.patch").read() if os.path.exists(out + "/agent.patch") else ""
    open(out + "/ipc/DONE", "w").write(patch or "(empty patch)")
    server.kill()
result = run_bench.evaluate(row, patch, out)
log = open(out + "/agent.log").read()
result.update(instance=iid, agent_sec=round(time.time() - began), patch_bytes=len(patch),
              requests=len([f for f in os.listdir(out + "/ipc") if f.startswith("req_")]),
              sweep=[l for l in log.splitlines() if l.startswith("[SWEEP]")],
              edits_tolerant=log.count("matched ignoring"))
json.dump(result, open(out + "/result.json", "w"), indent=1)
print(json.dumps(result))
