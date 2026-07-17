"""REST client for OpenHands Agent Server."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

console = Console()


class OpenHandsError(Exception):
    """Raised when OpenHands Agent Server requests fail."""
    pass


class OpenHandsClient:
    """Manage the OpenHands Agent Server lifecycle and task submission."""

    def __init__(
        self,
        base_url: str = "http://localhost:3005",
        compose_file: str | None = None,
        timeout: int = 600,
        workspace_mount: str | None = None,
    ):
        self.base_url = base_url
        self.compose_file = compose_file
        self.timeout = timeout
        self.workspace_mount = workspace_mount
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

    def wait_for_ready(self, retries: int = 30, interval: float = 2.0) -> None:
        """Poll the health endpoint until the server is accepting connections."""
        for i in range(retries):
            try:
                resp = self._client.get("/health", timeout=5.0)
                if resp.status_code == 200:
                    return
            except httpx.ConnectError:
                if i < retries - 1:
                    time.sleep(interval)
            except httpx.HTTPError:
                return  # Server responding but not 200 — might be normal
        raise OpenHandsError(
            f"OpenHands server did not become ready after {retries} attempts"
        )

    # --- Conversations ---

    def create_conversation(
        self, goal: str, workspace: str, n_retries: int = 3
    ) -> str:
        """Create a new conversation (task) and return the conversation ID."""
        payload = {
            "workspace": {"working_dir": workspace, "kind": "LocalWorkspace"},
            "initial_message": {"content": [{"text": goal}]},
            "agent": {
                "llm": {"model": "Qwen3.6-27B", "base_url": "http://172.25.0.1:8080"},
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
                    f"Failed to connect to OpenHands server after {n_retries} attempts"
                )
        raise OpenHandsError("Unexpected code path reached")

    def poll_conversation(self, conv_id: str) -> dict:
        """Long-poll a conversation until it completes or times out."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                resp = self._client.get(
                    f"/api/conversations/{conv_id}", timeout=15.0
                )
                data = resp.json()
                status = data.get("status", "")

                if status in ("completed", "finished", "done"):
                    return data

                # Keep polling if still running
                time.sleep(3)
            except httpx.HTTPError:
                # Server may be shutting down; check for final state
                time.sleep(2)

        raise OpenHandsError(
            f"Conversation {conv_id} timed out after {self.timeout}s"
        )

    def fetch_artifacts(self, conv_id: str) -> dict:
        """Retrieve artifacts (reports, scripts) from a completed conversation."""
        try:
            resp = self._client.get(
                f"/api/file/download-trajectory/{conv_id}", timeout=30.0
            )
            return resp.json()
        except httpx.HTTPError:
            return {"artifacts": []}

    def close(self) -> None:
        """Release HTTP client resources."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()