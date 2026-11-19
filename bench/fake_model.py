"""Scripted stand-in for OpenRouter: zero real model calls.

usage: python fake_model.py PORT SCRIPT.json LOG.jsonl
SCRIPT is a list of assistant messages (OpenAI chat format, with tool_calls) returned in order; every request
body is appended to LOG so tests can check what the agent sends (reasoning, seed, provider, max_tokens...).
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, SCRIPT, LOG = int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]
LOCK = threading.Lock()
STATE = {"i": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with LOCK:
            i = STATE["i"]
            STATE["i"] += 1
            with open(LOG, "a") as handle:
                handle.write(json.dumps(body) + "\n")
        message = SCRIPT[i] if i < len(SCRIPT) else {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "end%d" % i, "type": "function",
                            "function": {"name": "finish", "arguments": json.dumps({"summary": "done"})}}]}
        reply = {"choices": [{"message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                 "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.0}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
