"""OpenRouter stand-in that hands every request to a Claude sub-agent through files (0 OpenRouter call).

usage: python bridge_model.py PORT DIR
Request i is written to DIR/req_<i>.json; the server waits for DIR/resp_<i>.json (an assistant message in the
OpenAI chat format) and returns it. bridge_cli.py is the sub-agent's side.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, DIR = int(sys.argv[1]), sys.argv[2]
LOCK = threading.Lock()
STATE = {"i": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with LOCK:
            i = STATE["i"]
            STATE["i"] += 1
        tmp = os.path.join(DIR, "req_%d.json.tmp" % i)
        with open(tmp, "wb") as handle:
            handle.write(body)
        os.replace(tmp, os.path.join(DIR, "req_%d.json" % i))
        resp = os.path.join(DIR, "resp_%d.json" % i)
        began = time.time()
        while not os.path.exists(resp) and time.time() - began < 3600:
            time.sleep(0.5)
        if not os.path.exists(resp):
            self.reply(504, {"error": {"message": "bridge: no reply"}})
            return
        message = json.load(open(resp))
        self.reply(200, {"choices": [{"message": message,
                                      "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                         "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}})

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


os.makedirs(DIR, exist_ok=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
