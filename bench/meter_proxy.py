"""Local metering proxy for OpenRouter.

Agents point OPENROUTER_BASE_URL at http://127.0.0.1:PORT/<tag>/api/v1. Every request is forwarded
unchanged to https://openrouter.ai/api/v1 (with usage accounting switched on) and one JSON line per
call is appended to <logdir>/<tag>.jsonl: model, tokens, cached tokens, cost, latency, status.
"""
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://openrouter.ai"
LOGDIR = sys.argv[2] if len(sys.argv) > 2 else "logs"
LOCK = threading.Lock()
ALLOWED = set(filter(None, (os.environ.get("BENCH_ALLOWED_MODELS") or "openai/gpt-6-luna").split(",")))


def context():
    bundle = os.environ.get("SSL_CERT_FILE") or ("/root/.ccr/ca-bundle.crt" if os.path.exists("/root/.ccr/ca-bundle.crt") else None)
    return ssl.create_default_context(cafile=bundle) if bundle else ssl.create_default_context()


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(context=context()))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.forward(self.rfile.read(length))

    def forward(self, data):
        parts = self.path.lstrip("/").split("/", 1)
        tag, rest = (parts[0], "/" + parts[1]) if len(parts) == 2 else ("untagged", self.path)
        model = ""
        if data:
            try:
                body = json.loads(data)
                model = body.get("model", "")
                if ALLOWED and model not in ALLOWED:
                    # luna only: any other model is refused here and never reaches OpenRouter
                    refusal = json.dumps({"error": {"message": "model %s not allowed by bench proxy" % model}}).encode()
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(refusal)))
                    self.end_headers()
                    self.wfile.write(refusal)
                    return
                body["usage"] = {"include": True}
                data = json.dumps(body).encode()
            except ValueError:
                pass
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection", "accept-encoding")}
        request = urllib.request.Request(UPSTREAM + rest, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        began = time.time()
        status, payload = 0, b""
        try:
            with OPENER.open(request, timeout=900) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
        except Exception as error:
            status, payload = 502, json.dumps({"error": {"message": "%s: %s" % (type(error).__name__, error)}}).encode()
        record = {"t": began, "sec": round(time.time() - began, 2), "status": status, "model": model, "path": rest}
        try:
            reply = json.loads(payload)
            usage = reply.get("usage") or {}
            record.update(prompt=usage.get("prompt_tokens"), completion=usage.get("completion_tokens"),
                          cached=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                          reasoning=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                          cost=usage.get("cost"), provider=reply.get("provider"),
                          finish=((reply.get("choices") or [{}])[0] or {}).get("finish_reason"))
        except Exception:
            pass
        if data is not None:
            with LOCK, open(os.path.join(LOGDIR, tag + ".jsonl"), "a") as handle:
                handle.write(json.dumps(record) + "\n")
        self.send_response(status or 502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    os.makedirs(LOGDIR, exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
