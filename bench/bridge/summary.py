"""Table of every bridged run (agent driven by a Claude sub-agent): python summary.py [ROOT]"""
import glob
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/home/user/bench_work/bridge"
rows = []
for path in sorted(glob.glob(os.path.join(root, "**", "result.json"), recursive=True)):
    d = json.load(open(path))
    name = os.path.relpath(os.path.dirname(path), root)
    agent = "agent41_fast" if "agent41_fast__" in name or "agent41__" not in name else "agent41"
    rows.append((d["instance"][-5:], agent, name, d["resolved"], "%s/%s" % (d["f2p_pass"], d["f2p"]),
                 "%s/%s" % (d["p2p_pass"], d["p2p"]), d["requests"], d["agent_sec"], len(d.get("sweep") or [])))
print("| tâche | agent | run | résolu | F2P | P2P | tours | s | sweep |\n|---|---|---|---|---|---|---|---|---|")
for r in sorted(rows):
    print("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % r)
