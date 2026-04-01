"""
PBI 1.6 + PBI 2.10 + PBI 2.11 - Shared backend runtime state.

This module stores lightweight server-wide data that each client handler
thread can safely read/write.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from threading import Lock


@dataclass(slots=True)
class ServerState:
    """
    Thread-safe connection registry for the backend server.

    PBI 1.6 baseline tracked connection count and internal ids.
    PBI 2.10 extends state with usernames + sockets for online list updates.
    PBI 2.11 adds lookup helpers to route invitations to selected players.
    """

    connected_clients: int = 0
    _next_client_id: int = 1
    _client_sockets: dict[str, socket.socket] = field(default_factory=dict)
    _client_usernames: dict[str, str] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def register_client(self, client_socket: socket.socket) -> str:
        """Register a new connection and return a generated client id."""

        with self._lock:
            client_id = f"client-{self._next_client_id}"
            self._next_client_id += 1
            self.connected_clients += 1
            self._client_sockets[client_id] = client_socket
            return client_id

    def unregister_client(self, client_id: str) -> list[str]:
        """Decrease connected count when a client disconnects."""

        with self._lock:
            self.connected_clients = max(0, self.connected_clients - 1)
            self._client_sockets.pop(client_id, None)
            self._client_usernames.pop(client_id, None)
            return sorted(self._client_usernames.values())

    def set_username(self, client_id: str, username: str) -> list[str]:
        """
        Assign/update a player's username and return current online list.

        Returning the snapshot list simplifies handler logic for broadcasting.
        """

        with self._lock:
            # Enforce unique usernames so "select opponent" resolves cleanly.
            for existing_client_id, existing_username in self._client_usernames.items():
                if existing_client_id != client_id and existing_username == username:
                    raise ValueError(f"Username '{username}' is already in use.")

            self._client_usernames[client_id] = username
            return sorted(self._client_usernames.values())

    def get_client_sockets_snapshot(self) -> list[socket.socket]:
        """Return a copy of connected sockets for safe iteration/broadcast."""

        with self._lock:
            return list(self._client_sockets.values())

    def get_socket_by_username(self, username: str) -> socket.socket | None:
        """Find the connected socket for a username, if currently online."""

        with self._lock:
            for client_id, stored_username in self._client_usernames.items():
                if stored_username == username:
                    return self._client_sockets.get(client_id)
            return None

    def get_username_for_client(self, client_id: str) -> str | None:
        """Resolve the registered username for a connected client id."""

        with self._lock:
            return self._client_usernames.get(client_id)
