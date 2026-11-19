"""Replay the traceback-caller gate on every patch recorded by the bench (no model call)."""
import json, os, sys, subprocess, importlib.util, collections
sys.path.insert(0, "/home/user/z-core-results/bench")
import run_bench
spec = importlib.util.spec_from_file_location("a", "/home/user/z-core-results/bench/agents/agent41_fast.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
rows = {r["instance_id"]: r for r in json.load(open(run_bench.DATA))}
runs = []
for f in ("w30a", "w30b"):
    for l in open("/home/user/bench_work/runs/%s/results.jsonl" % f):
        r = json.loads(l)
        if f == "w30b" and r["variant"] == "a41_norev": continue
        p = "/home/user/bench_work/runs/%s/%s/%s/model.patch" % (f, r["variant"], r["instance"])
        if os.path.exists(p): runs.append((r["instance"], r["resolved"], f + "/" + r["variant"], p))
by = collections.defaultdict(list)
for x in runs: by[x[0]].append(x)
stat = collections.Counter(); out = []
for iid, items in sorted(by.items()):
    repo = "/home/user/bench_work/sweep_repo"
    run_bench.checkout(repo, rows[iid]["base_commit"])
    for _, ok, v, p in items:
        subprocess.run("git checkout -q -- . && git clean -qfd && git apply --whitespace=nowarn %s" % p, shell=True, cwd=repo, capture_output=True)
        notes = [A.traceback_note(rows[iid]["problem_statement"], repo, open(p).read())]; notes = [n for n in notes if n]
        stat[(ok, bool(notes))] += 1
        out.append("%s %-14s %s %d %s" % (iid[-5:], v, "OK " if ok else "FAIL", len(notes), " || ".join(notes[:3])[:400]))
print("\n".join(out))
print(stat)
