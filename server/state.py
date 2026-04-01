"""
PBI 1.6 + Sprint 2 lobby state support.

This module stores thread-safe backend runtime state used by client handler
threads for lobby operations, invitations, and match pairing.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4


@dataclass(slots=True)
class ServerState:
    """
    Thread-safe server registry and lobby coordination state.

    Sprint 2 additions cover:
    - PBI 2.3: online users tracking
    - PBI 2.5: waiting-state checks
    - PBI 2.6: invitation lifecycle state
    - PBI 2.7: match creation after accepted invitation
    """

    connected_clients: int = 0
    _next_client_id: int = 1

    # Per-connection username registry. A client is inserted with `None` at
    # connect time, then updated once it submits a username.
    _client_usernames: dict[str, str | None] = field(default_factory=dict)

    # Reverse lookup used to enforce uniqueness in O(1): username -> client_id.
    _username_to_client: dict[str, str] = field(default_factory=dict)

    # Connected sockets registry for broadcasts and direct sends.
    _client_sockets: dict[str, socket.socket] = field(default_factory=dict)

    # Pending invite registry: (from_user, to_user) -> "pending".
    _pending_invites: dict[tuple[str, str], str] = field(default_factory=dict)

    # Users involved in a pending invite (either inviter or invitee).
    _busy_invite_users: set[str] = field(default_factory=set)

    # Active match mappings once an invite is accepted.
    _active_matches: dict[str, tuple[str, str]] = field(default_factory=dict)
    _user_to_match: dict[str, str] = field(default_factory=dict)

    _lock: Lock = field(default_factory=Lock)

    def register_client(self, client_socket: socket.socket | None = None) -> str:
        """Register a new connection and return its generated client id."""

        with self._lock:
            client_id = f"client-{self._next_client_id}"
            self._next_client_id += 1
            self.connected_clients += 1

            # Reserve a slot so username assignment can verify known session.
            self._client_usernames[client_id] = None
            self._client_sockets[client_id] = client_socket
            return client_id

    def unregister_client(self, client_id: str) -> None:
        """Remove disconnected client and clean associated lobby state."""

        with self._lock:
            self.connected_clients = max(0, self.connected_clients - 1)

            previous_username = self._client_usernames.pop(client_id, None)
            self._client_sockets.pop(client_id, None)

            if previous_username is not None:
                # Release username only if still owned by this client.
                owner_id = self._username_to_client.get(previous_username)
                if owner_id == client_id:
                    self._username_to_client.pop(previous_username, None)

                # Cleanup pending invites and active matches for disconnected user.
                self._cleanup_user_lobby_state(previous_username)

    def set_client_username(self, client_id: str, username: str) -> tuple[bool, str]:
        """
        Save a client username if valid and unique.

        Returns:
        - (True, "accepted") on success
        - (False, "unknown_client") when client id is not registered
        - (False, "username_taken") when another client already owns username
        """

        with self._lock:
            if client_id not in self._client_usernames:
                return False, "unknown_client"

            # Reject only when another client already owns that username.
            existing_owner = self._username_to_client.get(username)
            if existing_owner is not None and existing_owner != client_id:
                return False, "username_taken"

            previous_username = self._client_usernames[client_id]
            if previous_username is not None and previous_username != username:
                # If username changed, free old name ownership and lobby links.
                self._username_to_client.pop(previous_username, None)
                self._cleanup_user_lobby_state(previous_username)

            self._client_usernames[client_id] = username
            self._username_to_client[username] = client_id
            return True, "accepted"

    def get_client_username(self, client_id: str) -> str | None:
        """Read the current username mapped to one client id."""

        with self._lock:
            return self._client_usernames.get(client_id)

    def get_online_users(self) -> list[str]:
        """
        Return all online usernames sorted for deterministic broadcasts.

        PBI 2.3 source of truth for lobby user list.
        """

        with self._lock:
            return sorted(self._username_to_client.keys())

    def get_client_sockets(self) -> list[socket.socket]:
        """Return all currently connected sockets for broadcast operations."""

        with self._lock:
            return list(self._client_sockets.values())

    def get_socket_for_username(self, username: str) -> socket.socket | None:
        """Resolve one lobby username to its active socket, if available."""

        with self._lock:
            client_id = self._username_to_client.get(username)
            if client_id is None:
                return None
            return self._client_sockets.get(client_id)

    def has_waiting_player(self) -> bool:
        """
        PBI 2.5 helper.

        True when fewer than two online users are available to form a match.
        """

        with self._lock:
            return len(self._username_to_client) < 2

    def create_invitation(self, from_user: str, to_user: str) -> tuple[bool, str]:
        """
        PBI 2.6: register a pending invitation between two online users.

        Reject when users are offline, in match, busy in another invite,
        or when user tries to invite themselves.
        """

        with self._lock:
            if from_user == to_user:
                return False, "cannot_invite_self"

            if from_user not in self._username_to_client or to_user not in self._username_to_client:
                return False, "user_offline"

            if from_user in self._user_to_match or to_user in self._user_to_match:
                return False, "user_in_match"

            if from_user in self._busy_invite_users or to_user in self._busy_invite_users:
                return False, "user_busy"

            invite_key = (from_user, to_user)
            self._pending_invites[invite_key] = "pending"
            self._busy_invite_users.add(from_user)
            self._busy_invite_users.add(to_user)
            return True, "pending"

    def respond_to_invitation(
        self,
        from_user: str,
        to_user: str,
        action: str,
    ) -> tuple[bool, str, str | None]:
        """
        PBI 2.6 + 2.7: resolve pending invitation action.

        Returns tuple: (success, result_code, game_id)
        """

        with self._lock:
            invite_key = (from_user, to_user)
            if invite_key not in self._pending_invites:
                return False, "invite_not_found", None

            if action == "cancel":
                self._pending_invites.pop(invite_key, None)
                self._busy_invite_users.discard(from_user)
                self._busy_invite_users.discard(to_user)
                return True, "cancelled", None

            if action == "decline":
                self._pending_invites.pop(invite_key, None)
                self._busy_invite_users.discard(from_user)
                self._busy_invite_users.discard(to_user)
                return True, "declined", None

            if action == "accept":
                if from_user in self._user_to_match or to_user in self._user_to_match:
                    return False, "user_in_match", None

                self._pending_invites.pop(invite_key, None)
                self._busy_invite_users.discard(from_user)
                self._busy_invite_users.discard(to_user)

                # Create a match id once both players are paired.
                game_id = f"game-{uuid4().hex[:8]}"
                self._active_matches[game_id] = (from_user, to_user)
                self._user_to_match[from_user] = game_id
                self._user_to_match[to_user] = game_id
                return True, "accepted", game_id

            return False, "invalid_action", None

    def _cleanup_user_lobby_state(self, username: str) -> None:
        """Internal helper to remove invite/match links for one username."""

        # Remove pending invites involving this username.
        invites_to_remove = [key for key in self._pending_invites if username in key]
        for from_user, to_user in invites_to_remove:
            self._pending_invites.pop((from_user, to_user), None)
            self._busy_invite_users.discard(from_user)
            self._busy_invite_users.discard(to_user)

        # Remove active match references involving this username.
        game_id = self._user_to_match.pop(username, None)
        if game_id is not None:
            players = self._active_matches.pop(game_id, None)
            if players is not None:
                for player in players:
                    if player != username:
                        self._user_to_match.pop(player, None)
