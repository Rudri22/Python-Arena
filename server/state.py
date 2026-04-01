"""
PBI 1.6 - Shared backend runtime state.

This module stores lightweight server-wide data that each client handler
thread can safely read/write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass(slots=True)
class ServerState:
    """
    Thread-safe connection registry for the backend server.

    For PBI 1.6 we only track connected client count and generate an
    internal client id. This keeps the communication layer simple while
    preparing for richer lobby/game state in later PBIs.
    """

    connected_clients: int = 0
    _next_client_id: int = 1
    _lock: Lock = field(default_factory=Lock)

    def register_client(self) -> str:
        """Register a new connection and return a generated client id."""

        with self._lock:
            client_id = f"client-{self._next_client_id}"
            self._next_client_id += 1
            self.connected_clients += 1
            return client_id

    def unregister_client(self) -> None:
        """Decrease connected count when a client disconnects."""

        with self._lock:
            self.connected_clients = max(0, self.connected_clients - 1)
