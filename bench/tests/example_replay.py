"""Replay the issue-examples and private-hook gates on every recorded bridged patch (no model call)."""
import collections
import glob
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import run_bench  # noqa: E402

spec = importlib.util.spec_from_file_location("a", os.path.join(HERE, "..", "agents", "agent41_fast.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
rows = {r["instance_id"]: r for r in json.load(open(run_bench.DATA))}
env = dict(os.environ, PATH=os.path.join(run_bench.VENV, "bin") + ":" + os.environ["PATH"])
os.environ.update(env)
runs = []
for path in sorted(glob.glob("/home/user/bench_work/bridge/**/result.json", recursive=True)):
    if "/old/" in path or "/v1_" in path:
        continue
    d = json.load(open(path))
    patch = os.path.join(os.path.dirname(path), "agent.patch")
    runs.append((d["instance"], d["resolved"], os.path.relpath(os.path.dirname(path), "/home/user/bench_work/bridge"), patch))
for f in ("w30a", "w30b"):
    if "--luna" not in sys.argv:
        break
    for line in open("/home/user/bench_work/runs/%s/results.jsonl" % f):
        r = json.loads(line)
        if f == "w30b" and r["variant"] == "a41_norev":
            continue
        patch = "/home/user/bench_work/runs/%s/%s/%s/model.patch" % (f, r["variant"], r["instance"])
        if os.path.exists(patch):
            runs.append((r["instance"], r["resolved"], "luna/%s/%s" % (f, r["variant"]), patch))
if "--luna" in sys.argv:
    runs = [r for r in runs if r[2].startswith("luna/")]
stat, base_cache = collections.Counter(), {}
work = "/home/user/bench_work/example_replay"
for iid, ok, name, patch in runs:
    row = rows[iid]
    lines = A.issue_examples(row["problem_statement"])
    if not lines:
        stat[(ok, "no examples")] += 1
        continue
    repo = os.path.join(work, "repo")
    run_bench.checkout(repo, row["base_commit"])
    prelude = [l for l in lines if l.startswith(("import ", "from "))]
    prelude = ["from sympy import *"] + prelude if "sympy" in row["problem_statement"] else prelude
    if iid not in base_cache:
        base_cache[iid] = A.run_examples(repo, lines, prelude)
    subprocess.run("git apply --whitespace=nowarn %s" % patch, shell=True, cwd=repo, capture_output=True)
    flags = A.example_flags(base_cache[iid], A.run_examples(repo, lines, prelude))
    hooks = A.hook_notes(open(patch).read())
    stat[(ok, "examples" if flags else "-", "hook" if hooks else "-")] += 1
    print("%s %-40s %s ex=%d hook=%d %s" % (iid[-5:], name, "OK  " if ok else "FAIL", len(flags), len(hooks),
                                            " | ".join(f.replace("\n", " ") for f in flags)[:220]), flush=True)
print(stat)
