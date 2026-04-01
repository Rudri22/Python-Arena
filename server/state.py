"""
Server runtime state (Sprint 2 + Sprint 3 backend).

This module combines:
- Sprint 2 lobby/session state (usernames, invites, matchmaking)
- Sprint 3 game runtime state integration (board/snake/pie/collision updates)
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4

from server.game_engine import (
    MatchRuntime,
    create_match_runtime,
    queue_direction,
    should_step,
    step_runtime,
    to_protocol_state,
)


def _normalize_username(username: str) -> str:
    """Normalize username for case-insensitive identity checks."""

    return username.casefold()


@dataclass(slots=True)
class ServerState:
    """
    Thread-safe server registry and gameplay coordination state.

    Lobby responsibilities (Sprint 2):
    - connected clients/sockets
    - unique usernames
    - online users list
    - invitation lifecycle + matchmaking

    Game responsibilities (Sprint 3):
    - active match runtime storage
    - movement input processing
    - game-state packaging + game-over metadata
    """

    connected_clients: int = 0
    _next_client_id: int = 1

    # Client/session registry.
    _client_usernames: dict[str, str | None] = field(default_factory=dict)
    # Maps normalized username -> client_id (case-insensitive uniqueness).
    _username_to_client: dict[str, str] = field(default_factory=dict)
    _client_sockets: dict[str, socket.socket | None] = field(default_factory=dict)

    # Invitation and matchmaking registry.
    _pending_invites: dict[tuple[str, str], str] = field(default_factory=dict)
    _busy_invite_users: set[str] = field(default_factory=set)
    _active_matches: dict[str, tuple[str, str]] = field(default_factory=dict)
    _user_to_match: dict[str, str] = field(default_factory=dict)

    # Sprint 3 authoritative match runtime (game_id -> runtime state).
    _match_runtimes: dict[str, MatchRuntime] = field(default_factory=dict)

    _lock: Lock = field(default_factory=Lock)

    def register_client(self, client_socket: socket.socket | None = None) -> str:
        """Register a new client connection and return generated client id."""

        with self._lock:
            client_id = f"client-{self._next_client_id}"
            self._next_client_id += 1
            self.connected_clients += 1
            self._client_usernames[client_id] = None
            self._client_sockets[client_id] = client_socket
            return client_id

    def unregister_client(self, client_id: str) -> list[str]:
        """Remove disconnected client and clean all linked lobby/match state."""

        with self._lock:
            self.connected_clients = max(0, self.connected_clients - 1)
            previous_username = self._client_usernames.pop(client_id, None)
            self._client_sockets.pop(client_id, None)

            if previous_username is not None:
                owner_id = self._username_to_client.get(_normalize_username(previous_username))
                if owner_id == client_id:
                    self._username_to_client.pop(_normalize_username(previous_username), None)
                self._cleanup_user_lobby_state(previous_username)

            return sorted(self._username_to_client.keys())

    def set_client_username(self, client_id: str, username: str) -> tuple[bool, str]:
        """Assign username if unique; returns status tuple (accepted/reason)."""

        with self._lock:
            if client_id not in self._client_usernames:
                return False, "unknown_client"

            normalized_username = _normalize_username(username)
            existing_owner = self._username_to_client.get(normalized_username)
            if existing_owner is not None and existing_owner != client_id:
                return False, "username_taken"

            previous_username = self._client_usernames[client_id]
            if previous_username is not None and previous_username != username:
                self._username_to_client.pop(_normalize_username(previous_username), None)
                self._cleanup_user_lobby_state(previous_username)

            self._client_usernames[client_id] = username
            self._username_to_client[normalized_username] = client_id
            return True, "accepted"

    def get_client_username(self, client_id: str) -> str | None:
        """Resolve currently assigned username for one client id."""

        with self._lock:
            return self._client_usernames.get(client_id)

    def get_online_users(self) -> list[str]:
        """Return all online usernames sorted for deterministic payloads."""

        with self._lock:
            return sorted(
                [username for username in self._client_usernames.values() if username is not None],
                key=str.casefold,
            )

    def get_client_sockets(self) -> list[socket.socket]:
        """Return currently connected sockets for broadcast operations."""

        with self._lock:
            return [sock for sock in self._client_sockets.values() if sock is not None]

    def get_socket_for_username(self, username: str) -> socket.socket | None:
        """Find active socket for a given online username, if any."""

        with self._lock:
            client_id = self._username_to_client.get(_normalize_username(username))
            if client_id is None:
                return None
            return self._client_sockets.get(client_id)

    def has_waiting_player(self) -> bool:
        """True when fewer than two online users are available in lobby."""

        with self._lock:
            return len(self._username_to_client) < 2

    def create_invitation(self, from_user: str, to_user: str) -> tuple[bool, str]:
        """Create pending invitation if both users are eligible."""

        with self._lock:
            from_user = self._resolve_username(from_user)
            to_user = self._resolve_username(to_user)
            if from_user is None or to_user is None:
                return False, "user_offline"
            if from_user == to_user:
                return False, "cannot_invite_self"
            if from_user in self._user_to_match or to_user in self._user_to_match:
                return False, "user_in_match"
            if from_user in self._busy_invite_users or to_user in self._busy_invite_users:
                return False, "user_busy"

            invite_key = (from_user, to_user)
            self._pending_invites[invite_key] = "pending"
            self._busy_invite_users.add(from_user)
            self._busy_invite_users.add(to_user)
            return True, "pending"

    def respond_to_invitation(self, from_user: str, to_user: str, action: str) -> tuple[bool, str, str | None]:
        """
        Resolve invitation lifecycle action.

        On accept, creates game_id, active-match mapping, and initial Sprint 3 runtime.
        """

        with self._lock:
            from_user = self._resolve_username(from_user)
            to_user = self._resolve_username(to_user)
            if from_user is None or to_user is None:
                return False, "invite_not_found", None
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

                game_id = f"game-{uuid4().hex[:8]}"
                self._active_matches[game_id] = (from_user, to_user)
                self._user_to_match[from_user] = game_id
                self._user_to_match[to_user] = game_id

                # Sprint 3 bootstrap: create authoritative initial match runtime.
                self._match_runtimes[game_id] = create_match_runtime(
                    game_id=game_id,
                    player_a=from_user,
                    player_b=to_user,
                )
                return True, "accepted", game_id

            return False, "invalid_action", None

    def get_match_players(self, game_id: str) -> tuple[str, str] | None:
        """Return both usernames assigned to one game id."""

        with self._lock:
            return self._active_matches.get(game_id)

    def get_match_state_dict(self, game_id: str) -> dict[str, Any] | None:
        """Return packaged game_state dictionary for one active match."""

        with self._lock:
            runtime = self._match_runtimes.get(game_id)
            if runtime is None:
                return None
            return to_protocol_state(runtime)

    def process_player_movement(
        self,
        player: str,
        direction: str,
    ) -> tuple[bool, str, str | None, dict[str, Any] | None, dict[str, Any] | None]:
        """
        Process one movement command and optionally advance the match tick.

        Returns:
        - success flag
        - reason/result code
        - game_id
        - packaged game state dict
        - optional game-over summary payload
        """

        with self._lock:
            game_id = self._user_to_match.get(player)
            if game_id is None:
                return False, "player_not_in_match", None, None, None

            runtime = self._match_runtimes.get(game_id)
            if runtime is None:
                return False, "match_not_found", game_id, None, None

            queued, reason = queue_direction(runtime, player=player, direction=direction)
            if not queued:
                reason_map = {
                    "invalid_direction": "invalid_direction",
                    "player_dead": "player_not_alive",
                    "match_finished": "match_finished",
                    "unknown_player": "player_not_in_match",
                    "reverse_not_allowed": "invalid_direction",
                }
                return False, reason_map.get(reason, "movement_rejected"), game_id, None, None

            # Sprint 3 movement update logic: progress tick once both players submitted input.
            if should_step(runtime):
                step_runtime(runtime)

            state_dict = to_protocol_state(runtime)
            game_over_payload: dict[str, Any] | None = None
            if runtime.status == "finished":
                final_scores = {
                    username: runtime.snakes[username].health
                    for username in runtime.players
                }
                game_over_payload = {
                    "game_id": game_id,
                    "winner": runtime.winner or "draw",
                    "final_scores": final_scores,
                    "reason": runtime.end_reason,
                }

            return True, "updated", game_id, state_dict, game_over_payload

    def _cleanup_user_lobby_state(self, username: str) -> None:
        """Internal cleanup for invitation/match state linked to a user."""

        invites_to_remove = [key for key in self._pending_invites if username in key]
        for from_user, to_user in invites_to_remove:
            self._pending_invites.pop((from_user, to_user), None)
            self._busy_invite_users.discard(from_user)
            self._busy_invite_users.discard(to_user)

        game_id = self._user_to_match.pop(username, None)
        if game_id is not None:
            players = self._active_matches.pop(game_id, None)
            self._match_runtimes.pop(game_id, None)
            if players is not None:
                for player in players:
                    if player != username:
                        self._user_to_match.pop(player, None)

    def _resolve_username(self, username: str) -> str | None:
        """
        Resolve any username casing to the canonical currently-registered value.
        """

        client_id = self._username_to_client.get(_normalize_username(username))
        if client_id is None:
            return None
        return self._client_usernames.get(client_id)
