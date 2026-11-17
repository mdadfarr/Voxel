"""A stand-in for api.anthropic.com for tests: records what it was sent and
answers from a script. It never talks to the network."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def message(content: list[dict], stop: str = "end_turn", model: str = "claude-haiku-5-5") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def text(value: str) -> dict:
    return message([{"type": "text", "text": value}])


def tool_use(name: str, arguments: dict) -> dict:
    return message([{"type": "tool_use", "id": "toolu_test1", "name": name, "input": arguments}], stop="tool_use")


class FakeAnthropic:
    """reply(body, headers) -> (status, dict). Calls seen are kept in .calls."""

    def __init__(self, reply=None):
        self.calls: list[dict] = []
        self.reply = reply or (lambda body, headers: (200, text("ok")))
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                headers = {k.lower(): v for k, v in self.headers.items()}
                fake.calls.append({"path": self.path, "headers": headers, "body": body})
                status, data = fake.reply(body, headers)
                out = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
