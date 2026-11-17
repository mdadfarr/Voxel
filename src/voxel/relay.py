"""Localhost relay: the only place the user's Anthropic key is ever used.

The builder process never holds the key. Its LLM calls go to this relay with a
one-time job token; the relay swaps the token for the real key and forwards the
call to https://api.anthropic.com, and nowhere else.

Rules this module keeps:
- The destination is a constant. Nothing in a request can change it.
- Only POST /v1/messages is accepted, and only with a live job token.
- No request body or header is ever logged, printed or put in an error message.
- A response that contains the key (it should never) is scrubbed before it goes back.
- close(token) forgets the key and the token.
"""

from __future__ import annotations

import hmac
import http.client
import importlib.util
import json
import ssl
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

REDACTED = "[removed]"
MESSAGES_PATH = "/v1/messages"
MAX_BODY = 64 * 1024 * 1024  # bytes; renders are sent as base64 images
MAX_REQUESTS = 600  # per job, whatever they cost
UPSTREAM_TIMEOUT = 660.0  # seconds; a builder call may think for minutes
# Request headers passed on to Anthropic. Everything else is dropped.
FORWARD_HEADERS = ("content-type", "anthropic-version", "anthropic-beta", "accept")


@dataclass(frozen=True)
class Upstream:
    host: str
    port: int = 443
    tls: bool = True


ANTHROPIC = Upstream("api.anthropic.com")  # the only destination outside tests


class Secret:
    """Holds a secret so that repr, str and tracebacks never show it."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def clear(self) -> None:
        self._value = ""

    def __repr__(self) -> str:
        return "<secret>"

    __str__ = __repr__


def scrub(data: bytes, secret: str) -> bytes:
    """`data` with every copy of `secret` replaced."""
    if not secret:
        return data
    return data.replace(secret.encode(), REDACTED.encode())


@dataclass
class _Grant:
    key: Secret
    spend_cap: float
    spent: float = 0.0
    requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def litellm_price() -> Callable[[str, dict], float]:
    """Cost of one Anthropic response, from litellm's bundled price map (the same
    numbers the builder's budget uses). litellm itself is not imported: importing
    it loads .env. An unknown model costs 0 here and only counts as a request."""
    prices: dict = {}
    spec = importlib.util.find_spec("litellm")
    if spec and spec.origin:
        path = Path(spec.origin).parent / "model_prices_and_context_window_backup.json"
        try:
            prices = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prices = {}

    def price(model: str, usage: dict) -> float:
        row = prices.get(model) or prices.get(f"anthropic/{model}") or {}

        def part(count_name: str, cost_name: str) -> float:
            count = usage.get(count_name) or 0
            cost = row.get(cost_name) or 0
            return count * cost if isinstance(count, (int, float)) and isinstance(cost, (int, float)) else 0.0

        return (
            part("input_tokens", "input_cost_per_token")
            + part("output_tokens", "output_cost_per_token")
            + part("cache_creation_input_tokens", "cache_creation_input_token_cost")
            + part("cache_read_input_tokens", "cache_read_input_token_cost")
        )

    return price


def _error(kind: str, message: str) -> bytes:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()


class Relay:
    """One relay for the site. open() a token for a job, close() it when the job ends."""

    def __init__(self, upstream: Upstream = ANTHROPIC, price: Callable[[str, dict], float] | None = None):
        self._upstream = upstream
        self._price = price or litellm_price()
        self._grants: dict[str, _Grant] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    # --- job tokens ----------------------------------------------------------

    def open(self, token: str, key: Secret, spend_cap: float) -> None:
        with self._lock:
            self._grants[token] = _Grant(key, spend_cap)

    def close(self, token: str) -> None:
        """Forget the token and the key. Calls with it are refused from now on."""
        with self._lock:
            grant = self._grants.pop(token, None)
        if grant is not None:
            grant.key.clear()

    def shutdown(self) -> None:
        with self._lock:
            tokens = list(self._grants)
        for token in tokens:
            self.close(token)
        self._server.shutdown()
        self._server.server_close()

    def _grant_for(self, token: str) -> _Grant | None:
        with self._lock:
            for known, grant in self._grants.items():
                if hmac.compare_digest(known.encode(), token.encode()):
                    return grant
        return None

    # --- forwarding ----------------------------------------------------------

    def _forward(self, key: str, headers: dict[str, str], body: bytes) -> tuple[int, str, bytes]:
        """POST the body to the fixed upstream with the real key. Never follows a
        redirect and never uses a proxy: the key goes to that one host only."""
        up = self._upstream
        if up.tls:
            conn = http.client.HTTPSConnection(
                up.host, up.port, timeout=UPSTREAM_TIMEOUT, context=ssl.create_default_context()
            )
        else:
            conn = http.client.HTTPConnection(up.host, up.port, timeout=UPSTREAM_TIMEOUT)
        try:
            conn.request("POST", MESSAGES_PATH, body=body, headers={**headers, "x-api-key": key})
            response = conn.getresponse()
            data = response.read()
            kind = response.getheader("Content-Type") or "application/json"
            return response.status, kind, data
        finally:
            conn.close()

    def _handler(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # never log: no paths, headers or bodies
                pass

            def log_error(self, *args):
                pass

            def _send(self, code: int, body: bytes, kind: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def _refuse(self, code: int = 403, message: str = "refused by the Voxel relay"):
                self._send(code, _error("permission_error", message))

            def do_GET(self):
                self._refuse()

            do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_CONNECT = do_GET

            def do_POST(self):
                # Only this relay's own address, and only the messages endpoint. An
                # absolute URL ("POST https://other.host/...") is a proxy request: refused.
                host = (self.headers.get("Host") or "").strip().lower()
                if host not in (f"127.0.0.1:{relay.port}", f"localhost:{relay.port}"):
                    return self._refuse()
                if self.path.partition("?")[0] != MESSAGES_PATH:
                    return self._refuse()
                token = (self.headers.get("x-api-key") or "").strip()
                if not token:
                    auth = (self.headers.get("Authorization") or "").strip()
                    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
                grant = relay._grant_for(token) if token else None
                if grant is None:
                    return self._refuse(401, "unknown or expired job token")
                try:
                    length = int(self.headers.get("Content-Length") or "")
                except ValueError:
                    return self._refuse(411, "Content-Length is required")
                if length < 0 or length > MAX_BODY:
                    return self._refuse(413, "request too large")
                body = self.rfile.read(length)
                with grant.lock:
                    if grant.requests >= MAX_REQUESTS or grant.spent >= grant.spend_cap:
                        return self._refuse(403, "this build reached its spending cap")
                    grant.requests += 1
                key = grant.key.reveal()
                if not key:  # closed while this call waited
                    return self._refuse(401, "unknown or expired job token")
                headers = {
                    name: value
                    for name in FORWARD_HEADERS
                    if (value := self.headers.get(name)) is not None
                }
                try:
                    status, kind, data = relay._forward(key, headers, body)
                except Exception:
                    # Nothing from the exception is passed on: it could quote the request.
                    return self._send(502, _error("api_error", "the relay could not reach Anthropic"))
                data = scrub(data, key)
                if status == 200:
                    relay._book(grant, data)
                self._send(status, data, scrub(kind.encode(), key).decode("latin-1"))

        return Handler

    def _book(self, grant: _Grant, data: bytes) -> None:
        try:
            reply = json.loads(data)
            cost = self._price(str(reply.get("model") or ""), reply.get("usage") or {})
        except Exception:
            return
        with grant.lock:
            grant.spent += max(cost, 0.0)
