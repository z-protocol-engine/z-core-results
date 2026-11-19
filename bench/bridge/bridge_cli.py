"""Sub-agent side of the bridge.

  python bridge_cli.py DIR next          wait for the agent's next request, print what is new in it
  python bridge_cli.py DIR reply FILE    answer it; FILE is JSON: {"content": "...", "tool_calls":
                                         [{"name": "bash", "arguments": {"command": "..."}}, ...]}
  python bridge_cli.py DIR status        is the agent still running / finished
"""
import json
import os
import sys
import time

DIR, cmd = sys.argv[1], sys.argv[2]
state_path = os.path.join(DIR, "cli_state.json")
state = json.load(open(state_path)) if os.path.exists(state_path) else {"answered": -1, "shown": 0}


def pending():
    i = state["answered"] + 1
    return i, os.path.join(DIR, "req_%d.json" % i)


def show(message):
    role = message.get("role")
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(p.get("text", "[%s attached]" % p.get("type")) for p in content if isinstance(p, dict))
    if role == "tool":
        return "=== TOOL RESULT (%s) ===\n%s" % (message.get("tool_call_id"), content)
    if role == "assistant":
        calls = message.get("tool_calls") or []
        return "=== YOUR EARLIER TURN === %s" % "; ".join(c["function"]["name"] for c in calls)
    return "=== %s ===\n%s" % (role.upper(), content)


if cmd == "next":
    i, path = pending()
    began = time.time()
    while not os.path.exists(path):
        if os.path.exists(os.path.join(DIR, "DONE")):
            print("AGENT FINISHED. Final patch:\n" + open(os.path.join(DIR, "DONE")).read()[:6000])
            sys.exit(0)
        if time.time() - began > 540:
            print("still waiting for the agent; run next again")
            sys.exit(0)
        time.sleep(0.5)
    body = json.load(open(path))
    messages = body.get("messages") or []
    if len(messages) < state["shown"]:
        print("=== NOTE: the agent shortened the conversation (old turns trimmed); showing the latest messages ===")
        state["shown"] = max(0, len(messages) - 4)
    new = messages[state["shown"]:]
    if not new and state.get("last_shown") == i:
        print("REQUEST %d was already shown above and still needs your reply: send it with reply." % i)
        sys.exit(0)
    state["last_shown"] = i
    state["shown"] = len(messages)
    json.dump(state, open(state_path, "w"))
    if i == 0:
        names = [t["function"]["name"] for t in body.get("tools") or []]
        print("TOOLS: %s\n" % json.dumps([t["function"] for t in body.get("tools") or []])[:4000])
        print("(tool names: %s)" % ", ".join(names))
    print("REQUEST %d (%d messages total)\n" % (i, len(messages)))
    print("\n\n".join(show(m) for m in new if m.get("role") != "assistant"))
elif cmd == "reply":
    i, path = pending()
    spec = json.load(open(sys.argv[3]))
    message = {"role": "assistant", "content": spec.get("content") or ""}
    calls = spec.get("tool_calls") or []
    if calls:
        message["tool_calls"] = [{"id": "call_%d_%d" % (i, k), "type": "function",
                                  "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {})}}
                                 for k, c in enumerate(calls)]
    tmp = os.path.join(DIR, "resp_%d.json.tmp" % i)
    json.dump(message, open(tmp, "w"))
    os.replace(tmp, os.path.join(DIR, "resp_%d.json" % i))
    state["answered"] = i
    json.dump(state, open(state_path, "w"))
    print("sent reply %d with %d tool call(s)" % (i, len(calls)))
elif cmd == "status":
    print("finished" if os.path.exists(os.path.join(DIR, "DONE")) else "running")
