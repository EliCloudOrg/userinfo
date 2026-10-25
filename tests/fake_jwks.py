"""假 JWKS 服务器：走真实 HTTP + 真实 RS256 验签路径。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.helpers import public_jwk


class FakeJwksServer:
    def __init__(self, key, kid: str, *, max_age: int = 300) -> None:
        self.kid = kid
        self.keys: dict[str, dict] = {kid: public_jwk(key, kid)}
        self.max_age = max_age
        self.requests = 0

        server = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.split("?")[0] != "/jwks.json":
                    self.send_error(404)
                    return
                server.requests += 1
                body = json.dumps({"keys": list(server.keys.values())}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", f"public, max-age={server.max_age}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "FakeJwksServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/jwks.json"

    def rotate(self, key, kid: str) -> None:
        self.keys[kid] = public_jwk(key, kid)
