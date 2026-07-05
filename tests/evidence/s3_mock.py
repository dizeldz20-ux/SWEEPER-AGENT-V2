"""Minimal S3 mock server for integration testing.

Implements just enough of the S3 API to support put_object + head_object,
which is all S3Exporter needs. Listeners are real HTTP on a random localhost
port, so boto3 talks to it as if it were a real S3 endpoint (with
endpoint_url override).

This is NOT a generic S3 simulator — it is purpose-built for testing the
Sweeper evidence exporter end-to-end. If you need more operations, add them.
"""
from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


# Storage: bucket -> key -> bytes
_storage: dict[str, dict[str, bytes]] = {}
_request_log: list[dict[str, Any]] = []


class _S3MockHandler(BaseHTTPRequestHandler):
    """Handles PUT /<bucket>/<key> and HEAD /<bucket>/<key>."""

    def do_PUT(self):  # noqa: N802
        # Path: /<bucket>/<key...>
        path = self.path.lstrip("/")
        parts = path.split("/", 1)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            self.send_error(400, "expected /<bucket>/<key>")
            return
        bucket, key = parts[0], parts[1]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        _storage.setdefault(bucket, {})[key] = body
        _request_log.append(
            {
                "method": "PUT",
                "bucket": bucket,
                "key": key,
                "body_size": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "content_type": self.headers.get("Content-Type", ""),
            }
        )
        self.send_response(200)
        self.send_header("ETag", f'"{hashlib.md5(body).hexdigest()}"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):  # noqa: N802
        path = self.path.lstrip("/")
        parts = path.split("/", 1)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            self.send_error(400)
            return
        bucket, key = parts[0], parts[1]
        body = _storage.get(bucket, {}).get(key)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", f'"{hashlib.md5(body).hexdigest()}"')
        self.end_headers()

    def log_message(self, format, *args):  # silence test noise
        pass


class S3MockServer:
    """Runs the S3 mock on a random localhost port. Thread-safe."""

    def __init__(self):
        _storage.clear()
        _request_log.clear()
        self.server = HTTPServer(("127.0.0.1", 0), _S3MockHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # --- introspection for assertions ---------------------------------------

    def stored_keys(self, bucket: str) -> list[str]:
        return sorted(_storage.get(bucket, {}).keys())

    def stored_body(self, bucket: str, key: str) -> bytes:
        return _storage.get(bucket, {}).get(key, b"")

    def request_log(self) -> list[dict[str, Any]]:
        return list(_request_log)
