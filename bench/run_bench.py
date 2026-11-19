"""Mini SWE-bench Verified (sympy) bench, no Docker.

For each (variant, instance): check out the repo at base_commit, run the agent there (repo python = 3.9 venv),
then apply its patch + the official test_patch to a fresh checkout and run the official tests with bin/test.
Resolved = every FAIL_TO_PASS and PASS_TO_PASS test passes. Cost and call latency come from meter_proxy.py.

usage: python run_bench.py --variants v1,v2 --instances id1,id2 [--workers 4] [--run NAME]
       python run_bench.py --gold --instances ...   (harness self-check with the reference patches)
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("BENCH_WORK", "/home/user/bench_work")
SRC = os.path.join(WORK, "sympy_src")
VENV = os.path.join(WORK, "venv39")
DATA = os.path.join(HERE, "data", "sympy_454_500.json")
PORT = int(os.environ.get("BENCH_PORT", "8765"))
UPLOAD = "/root/.claude/uploads/a63ec59c-ecd4-5293-81db-49757466a820/ed18c4e8-agentcon41.py"

LUNA = "openai/gpt-6-luna"
VARIANTS = {
    # agent 41 unchanged except every model forced to luna (luna only: no Sonnet, no Haiku)
    "a41_luna": {"file": os.path.join(HERE, "agents", "agent41.py"),
                 "overrides": {"DRIVER_MODEL": LUNA, "SECOND_LOOK_MODEL": LUNA, "RELIEF_MODEL": LUNA}},
    "a41_fast": {"file": os.path.join(HERE, "agents", "agent41_fast.py"), "overrides": {}},
    # same, without the forced review round before finish
    "a41_norev": {"file": os.path.join(HERE, "agents", "agent41_fast.py"), "overrides": {"REVIEW_MIN_SEC": 1e9}},
    # no review round, but a second look by luna itself in a fresh conversation (against run-to-run variance)
    "a41_look": {"file": os.path.join(HERE, "agents", "agent41_fast.py"),
                 "overrides": {"REVIEW_MIN_SEC": 1e9}},
    "zcore": {"file": "/home/user/ridges/agent.py", "overrides": {}},
}


def sh(command, cwd, timeout=600, env=None):
    done = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
    return done.returncode, done.stdout + done.stderr


def checkout(dest, commit):
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    sh("git clone -q --shared %s %s && cd %s && git checkout -q %s && git remote remove origin" % (SRC, dest, dest, commit), WORK)
    sh("git -c user.email=b@b -c user.name=b branch -q -f bench HEAD && git checkout -q bench", dest)


def test_files(test_patch):
    return sorted(set(re.findall(r"^diff --git a/(\S+) b/", test_patch, re.M)))


def evaluate(row, patch, out_dir):
    repo = os.path.join(out_dir, "eval_repo")
    checkout(repo, row["base_commit"])
    result = {"applied": True}
    if patch.strip():
        with open(os.path.join(out_dir, "model.patch"), "w") as handle:
            handle.write(patch)
        code, text = sh("git apply --whitespace=nowarn %s" % os.path.join(out_dir, "model.patch"), repo)
        if code:
            code, text = sh("patch -p1 --batch --forward < %s" % os.path.join(out_dir, "model.patch"), repo)
        if code:
            result.update(applied=False, apply_error=text[-500:])
    tp = os.path.join(out_dir, "test.patch")
    with open(tp, "w") as handle:
        handle.write(row["test_patch"])
    sh("git checkout -q -- %s" % " ".join(test_files(row["test_patch"])), repo)
    sh("git apply %s" % tp, repo)
    env = dict(os.environ, PATH=os.path.join(VENV, "bin") + ":" + os.environ["PATH"],
               PYTHONWARNINGS="ignore::UserWarning,ignore::SyntaxWarning")
    began = time.time()
    try:
        code, log = sh("python bin/test -C --verbose %s" % " ".join(test_files(row["test_patch"])), repo,
                       timeout=1800, env=env)
    except subprocess.TimeoutExpired:
        log = "TIMEOUT"
    with open(os.path.join(out_dir, "test.log"), "w") as handle:
        handle.write(log)
    status = {}
    for name, mark in re.findall(r"^\s*(test_\w+)\s+(ok|F|E|f|s|X|x|Slow|w)\b", log, re.M):
        status[name] = mark
    want_f2p = json.loads(row["FAIL_TO_PASS"])
    want_p2p = json.loads(row["PASS_TO_PASS"])
    ok = lambda name: status.get(name) == "ok"
    result.update(f2p_pass=sum(ok(n) for n in want_f2p), f2p=len(want_f2p),
                  p2p_pass=sum(ok(n) for n in want_p2p), p2p=len(want_p2p), test_sec=round(time.time() - began, 1))
    result["resolved"] = (result["applied"] and result["f2p_pass"] == result["f2p"]
                          and result["p2p_pass"] == result["p2p"])
    shutil.rmtree(repo, ignore_errors=True)
    return result


def meter(tag):
    path = os.path.join(WORK, "meter", tag + ".jsonl")
    calls = []
    if os.path.exists(path):
        calls = [json.loads(line) for line in open(path) if line.strip()]
    return {"calls": len(calls), "cost": round(sum(c.get("cost") or 0 for c in calls), 4),
            "model_sec": round(sum(c.get("sec") or 0 for c in calls), 1),
            "prompt": sum(c.get("prompt") or 0 for c in calls), "cached": sum(c.get("cached") or 0 for c in calls),
            "completion": sum(c.get("completion") or 0 for c in calls),
            "errors": sum(1 for c in calls if c.get("status") != 200),
            "length_cut": sum(1 for c in calls if c.get("finish") == "length"),
            "models": sorted(set(c.get("model") for c in calls))}


def run_one(run, variant, row):
    iid = row["instance_id"]
    tag = "%s__%s__%s" % (run, variant, iid)
    out_dir = os.path.join(WORK, "runs", run, variant, iid)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    record = {"run": run, "variant": variant, "instance": iid}
    if variant == "gold":
        patch, agent_sec = row["patch"], 0.0
    elif variant == "empty":
        patch, agent_sec = "", 0.0
    else:
        spec = VARIANTS[variant]
        repo = os.path.join(out_dir, "repo")
        checkout(repo, row["base_commit"])
        with open(os.path.join(out_dir, "statement.txt"), "w") as handle:
            handle.write(row["problem_statement"])
        env = dict(os.environ, PATH=os.path.join(VENV, "bin") + ":" + os.environ["PATH"],
                   OPENROUTER_BASE_URL="http://127.0.0.1:%d/%s/api/v1" % (PORT, tag),
                   OPENROUTER_API_KEY="sk-or-v1-dummy", AGENT_OVERRIDES=json.dumps(spec["overrides"]))
        env.pop("SANDBOX_PROXY_URL", None)
        env.pop("VIRTUAL_ENV", None)
        began = time.time()
        try:
            with open(os.path.join(out_dir, "agent.log"), "w") as log:
                subprocess.run([sys.executable, os.path.join(HERE, "load_agent.py"), spec["file"],
                                os.path.join(out_dir, "statement.txt"), os.path.join(out_dir, "agent.patch")],
                               cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
        except subprocess.TimeoutExpired:
            record["killed"] = True
        agent_sec = time.time() - began
        patch = open(os.path.join(out_dir, "agent.patch")).read() if os.path.exists(os.path.join(out_dir, "agent.patch")) else ""
        shutil.rmtree(repo, ignore_errors=True)
    record.update(agent_sec=round(agent_sec, 1), patch_bytes=len(patch))
    record.update(evaluate(row, patch, out_dir))
    record.update(meter(tag))
    with open(os.path.join(WORK, "runs", run, "results.jsonl"), "a") as handle:
        handle.write(json.dumps(record) + "\n")
    print("%-10s %-22s resolved=%-5s %6.0fs $%.4f calls=%s f2p=%s/%s p2p=%s/%s" % (
        variant, iid, record["resolved"], record["agent_sec"], record["cost"], record["calls"],
        record["f2p_pass"], record["f2p"], record["p2p_pass"], record["p2p"]), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="a41_luna")
    parser.add_argument("--instances", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--run", default=time.strftime("run%m%d_%H%M"))
    args = parser.parse_args()
    rows = {r["instance_id"]: r for r in json.load(open(DATA))}
    ids = [i if "__" in i else "sympy__sympy-" + i for i in args.instances.split(",")]
    jobs = [(v, rows[i]) for i in ids for v in args.variants.split(",")]
    os.makedirs(os.path.join(WORK, "runs", args.run), exist_ok=True)
    with cf.ThreadPoolExecutor(args.workers) as pool:
        records = list(pool.map(lambda job: run_one(args.run, *job), jobs))
    print("\nvariant     solved   avg_agent_s  avg_cost  avg_calls")
    for variant in args.variants.split(","):
        mine = [r for r in records if r["variant"] == variant]
        print("%-10s %3d/%-3d %10.0f   $%.4f %8.1f" % (
            variant, sum(r["resolved"] for r in mine), len(mine), sum(r["agent_sec"] for r in mine) / len(mine),
            sum(r["cost"] for r in mine) / len(mine), sum(r["calls"] for r in mine) / len(mine)))


if __name__ == "__main__":
    main()
