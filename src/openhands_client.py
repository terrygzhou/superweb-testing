"""REST client for OpenHands Agent Server.

Compensates for missing SDK by polling events and using REST file operations.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from rich.console import Console

console = Console()


class OpenHandsError(Exception):
    """Raised when OpenHands Agent Server requests fail."""
    pass


def _event_summary(evt: dict) -> str:
    """Condense an event dict into a one-line summary for console output."""
    kind = evt.get("kind", "?")
    source = evt.get("source", "?")
    code = evt.get("code")
    detail = evt.get("detail")
    if code or (detail and "Error" in (detail or "")):
        return f"[red]{kind} ({source}) ⚠ {code or 'ERROR'}[/red]"
    return f"[dim]{kind} ({source})[/dim]"


# Terminal statuses for conversations
TERMINAL_STATUSES = frozenset(
    {"error", "stopped", "completed", "cancelled", "finished"}
)


class OpenHandsClient:
    """Manage the OpenHands Agent Server lifecycle and task submission."""

    def __init__(
        self,
        base_url: str = "http://localhost:3005",
        compose_file: Optional[str] = None,
        timeout: int = 600,
        model: str = "openai/Qwen3.6-27B",
        base_llm_url: str = "http://172.25.0.1:8080",
    ):
        self.base_url = base_url
        self.compose_file = compose_file
        self.timeout = timeout
        self.model = model
        # vLLM serves at /v1/, litellm appends /chat/completions
        self.base_llm_url = base_llm_url.rstrip("/") + "/v1"
        self._client = httpx.Client(base_url=self.base_url, timeout=60.0)

    # --- Lifecycle ---

    def start_server(self) -> None:
        """Start the OpenHands container via Docker Compose."""
        cmd = ["docker", "compose"]
        if self.compose_file:
            cmd.extend(["-f", self.compose_file])
        cmd.extend(["up", "-d"])
        console.print(f"[blue]Starting OpenHands: {' '.join(cmd)}[/blue]")
        subprocess.run(cmd, check=True)
        self.wait_for_ready()

    def stop_server(self) -> None:
        """Stop the OpenHands container."""
        cmd = ["docker", "compose"]
        if self.compose_file:
            cmd.extend(["-f", self.compose_file])
        cmd.append("down")
        console.print("[blue]Stopping OpenHands...[/blue]")
        subprocess.run(cmd, check=False)

    def status(self) -> dict:
        """Check container status."""
        cmd = ["docker", "compose"]
        if self.compose_file:
            cmd.extend(["-f", self.compose_file])
        cmd.extend(["ps", "--format", "json"])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return {"running": True, "info": result.stdout}
        except subprocess.CalledProcessError:
            return {"running": False}

    def wait_for_ready(self, retries: int = 60, interval: float = 2.0) -> None:
        """Wait until /health and /api/conversations are ready.

        Catches all exceptions — container startup can produce various
        socket-level errors (ConnectError, RemoteProtocolError, ReadError).
        """
        for i in range(retries):
            health_ok = False
            try:
                resp = self._client.get("/health", timeout=5.0)
                if resp.status_code == 200:
                    health_ok = True
            except Exception:
                pass

            if health_ok:
                try:
                    conv_resp = self._client.get("/api/conversations", timeout=5.0)
                    if conv_resp.status_code in (200, 422):
                        return
                except Exception:
                    pass

            if i < retries - 1:
                time.sleep(interval)
        raise OpenHandsError(
            f"OpenHands server did not become ready after {retries} attempts"
        )

    # --- Conversations ---

    def create_conversation(self, goal: str, workspace: str, n_retries: int = 3) -> str:
        """Create a new conversation (task) and return the conversation ID."""
        payload = {
            "workspace": {"working_dir": workspace, "kind": "LocalWorkspace"},
            "initial_message": {"content": [{"text": goal}]},
            "agent": {
                "llm": {
                    "model": self.model,
                    "base_url": self.base_llm_url,
                    "api_key": "dummy",
                },
            },
            "confirmation_policy": {"kind": "NeverConfirm"},
        }
        for attempt in range(n_retries):
            try:
                resp = self._client.post(
                    "/api/conversations", json=payload, timeout=30.0
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    return data.get("id", data.get("conversation_id", ""))
                raise OpenHandsError(
                    f"Failed to create conversation: {resp.status_code} {resp.text}"
                )
            except httpx.ConnectError:
                if attempt < n_retries - 1:
                    time.sleep(2)
                    continue
                raise OpenHandsError(
                    f"Failed to connect after {n_retries} attempts"
                )
        raise OpenHandsError("Unexpected code path")

    # --- Events (compensates for missing WebSocket/SDK streaming) ---

    def _get_events(
        self, conv_id: str, page_id: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """Fetch events via REST /events/search endpoint."""
        params = {"limit": limit, "sort_order": "TIMESTAMP_DESC"}
        if page_id:
            params["page_id"] = page_id
        try:
            resp = self._client.get(
                f"/api/conversations/{conv_id}/events/search",
                params=params,
                timeout=15.0,
            )
            return resp.json().get("items", [])
        except httpx.HTTPError:
            return []

    def _get_execution_status(self, conv_id: str) -> str:
        """Get current execution_status of a conversation."""
        try:
            resp = self._client.get(f"/api/conversations/{conv_id}", timeout=15.0)
            return resp.json().get("execution_status", "unknown")
        except httpx.HTTPError:
            return "unknown"

    def stream_events(
        self,
        conv_id: str,
        on_event: Optional[Callable[[dict], None]] = None,
        poll_interval: float = 2.0,
    ) -> list[dict]:
        """Poll events until conversation completes, calling on_event for each.

        Uses REST event polling as a drop-in for SDK event streaming.
        Returns all collected events.
        """
        deadline = time.time() + self.timeout
        all_events: list[dict] = []
        last_id: Optional[str] = None
        seen_ids: set[str] = set()

        while time.time() < deadline:
            events = self._get_events(conv_id, last_id)

            for evt in events:
                eid = evt.get("id")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    all_events.append(evt)
                    if on_event:
                        on_event(evt)

                if eid:
                    last_id = eid

            status = self._get_execution_status(conv_id)
            if status in TERMINAL_STATUSES:
                return all_events

            time.sleep(poll_interval)

        raise OpenHandsError(f"Conversation {conv_id} timed out after {self.timeout}s")

    # --- Backwards-compatible polling ---

    def poll_conversation(self, conv_id: str) -> dict:
        """Poll conversation status until done."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            status = self._get_execution_status(conv_id)
            if status in TERMINAL_STATUSES:
                return self._client.get(
                    f"/api/conversations/{conv_id}", timeout=15.0
                ).json()
            time.sleep(3)
        raise OpenHandsError(f"Conversation {conv_id} timed out after {self.timeout}s")

    def poll_conversation_with_events(
        self,
        conv_id: str,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """Stream events via REST polling until conversation finishes."""
        self.stream_events(conv_id, on_event=on_event)
        return self._client.get(f"/api/conversations/{conv_id}", timeout=15.0).json()

    # --- Utility ---

    def cancel_conversation(self, conv_id: str) -> bool:
        """Cancel a running conversation."""
        try:
            resp = self._client.post(
                f"/api/conversations/{conv_id}/goal/stop", timeout=15.0
            )
            return resp.status_code in (200, 201, 204)
        except httpx.HTTPError:
            return False

    def fetch_artifacts(self, conv_id: str) -> dict:
        """Retrieve artifacts from a completed conversation."""
        try:
            resp = self._client.get(
                f"/api/file/download-trajectory/{conv_id}", timeout=30.0
            )
            return resp.json()
        except httpx.HTTPError:
            return {"artifacts": []}

    def read_file_in_workspace(self, conv_id: str, file_path: str) -> Optional[str]:
        """Read a file from the conversation workspace via REST."""
        try:
            resp = self._client.get(
                f"/api/conversations/{conv_id}/files",
                params={"path": file_path},
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("content")
        except httpx.HTTPError:
            pass
        return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()