"""Run one agent file's agent_main in the current directory and write the returned patch.

usage: python load_agent.py AGENT_FILE STATEMENT_FILE PATCH_OUT
Module constants can be overridden with AGENT_OVERRIDES='{"DRIVER_MODEL": "...", ...}' (applied after import).
"""
import importlib.util
import json
import os
import sys

agent_file, statement_file, patch_out = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("bench_agent", agent_file)
module = importlib.util.module_from_spec(spec)
sys.modules["bench_agent"] = module
spec.loader.exec_module(module)
for key, value in json.loads(os.environ.get("AGENT_OVERRIDES") or "{}").items():
    if isinstance(getattr(module, key, None), dict) and isinstance(value, dict):
        getattr(module, key).update(value)
    else:
        setattr(module, key, value)
with open(statement_file) as handle:
    statement = handle.read()
patch = module.agent_main({"problem_statement": statement})
with open(patch_out, "w") as handle:
    handle.write(patch or "")
