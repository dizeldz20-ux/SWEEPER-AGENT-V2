"""Agent HTTP client.

Lets the dashboard (or any consumer) talk to a remote ipracticom-sweeper
agent over HTTP. Wraps the agent's REST API with a small typed interface.

Usage:
    client = AgentClient("http://10.0.0.5:8787", token="secret")
    snapshot = client.get_snapshot()
    client.trigger_run()
    client.send_test_notify()

In open mode (no token), the client omits the Authorization header.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from ipracticom_sweeper.config import get_server_id


class AgentError(Exception):
    """Raised on HTTP error or invalid response."""


class AgentClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        # Strip trailing slash to keep urljoin predictable
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # --- Internal helpers --------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path: str) -> Any:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(url, headers=self._headers())
            if r.status_code >= 400:
                raise AgentError(f"GET {path}: {r.status_code} {r.text}")
            if r.status_code == 204 or not r.content:
                return None
            return r.json()
        except httpx.RequestError as e:
            raise AgentError(f"GET {path}: connection error: {e}") from e

    def _post(self, path: str, json: Any = None, timeout: float | None = None) -> Any:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            headers = self._headers()
            if json is not None:
                headers["Content-Type"] = "application/json"
            with httpx.Client(timeout=timeout or self.timeout) as c:
                r = c.post(url, headers=headers, json=json)
            if r.status_code >= 400:
                raise AgentError(f"POST {path}: {r.status_code} {r.text}")
            if r.status_code == 204 or not r.content:
                return None
            return r.json()
        except httpx.RequestError as e:
            raise AgentError(f"POST {path}: connection error: {e}") from e

    def _patch(self, path: str, json: Any = None) -> Any:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            headers = self._headers()
            if json is not None:
                headers["Content-Type"] = "application/json"
            with httpx.Client(timeout=self.timeout) as c:
                r = c.patch(url, headers=headers, json=json)
            if r.status_code >= 400:
                raise AgentError(f"PATCH {path}: {r.status_code} {r.text}")
            if r.status_code == 204 or not r.content:
                return None
            return r.json()
        except httpx.RequestError as e:
            raise AgentError(f"PATCH {path}: connection error: {e}") from e

    def _delete(self, path: str) -> None:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.delete(url, headers=self._headers())
            if r.status_code >= 400:
                raise AgentError(f"DELETE {path}: {r.status_code} {r.text}")
        except httpx.RequestError as e:
            raise AgentError(f"DELETE {path}: connection error: {e}") from e

    # --- Public API --------------------------------------------------------

    def healthz(self) -> dict[str, Any]:
        """Check agent liveness. Returns {ok, server_id, ts, auth}."""
        return self._get("/healthz")

    def get_snapshot(self) -> dict[str, Any] | None:
        """Get the latest cached pipeline result, or None if no run yet."""
        try:
            return self._get("/api/snapshot")
        except AgentError as e:
            # 404 = no cached snapshot yet; that's expected for a fresh install.
            if "404" in str(e):
                return None
            raise

    def trigger_run(self) -> dict[str, Any]:
        """Trigger a fresh sweep on the agent."""
        return self._post("/api/run")

    def send_test_notify(self) -> dict[str, Any]:
        """Trigger a test notification on the agent."""
        return self._post("/api/notify/test")

    # --- Connectors -------------------------------------------------------

    def list_connectors(self) -> list[dict[str, Any]]:
        return self._get("/api/connectors") or []

    def create_connector(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/connectors", json=payload)

    def update_connector(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._patch(f"/api/connectors/{name}", json=payload)

    def delete_connector(self, name: str) -> None:
        self._delete(f"/api/connectors/{name}")

    def test_connector(self, name: str) -> dict[str, Any]:
        """Trigger an immediate SSM collection. May take 5-30s, hence larger timeout."""
        return self._post(f"/api/connectors/{name}/test", timeout=60.0)

    # --- Identity helpers --------------------------------------------------

    @staticmethod
    def local_identity() -> dict[str, Any]:
        """Return this machine's identity (for self-mode displays)."""
        return {
            "kind": "local",
            "server_id": get_server_id(),
        }

    def remote_identity(self) -> dict[str, Any]:
        """Return the remote agent's identity (from /healthz)."""
        h = self.healthz()
        return {
            "kind": "remote",
            "base_url": self.base_url,
            "server_id": h.get("server_id"),
            "auth": h.get("auth"),
        }