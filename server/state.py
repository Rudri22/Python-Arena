"""
Server runtime state (Sprint 2 + Sprint 3 backend).

This module combines:
- Sprint 2 lobby/session state (usernames, invites, matchmaking)
- Sprint 3 game runtime state integration (board/snake/pie/collision updates)
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4

from server.game_engine import (
    MatchRuntime,
    create_match_runtime,
    queue_direction,
    step_runtime,
    to_protocol_state,
)
from shared.protocol import SnakeSkin, sanitize_skin, skin_to_dict


# // Feature: Server State / Matchmaking
# // Purpose: Normalize username for case-insensitive identity checks.
# // Trigger: Called by the server state / matchmaking flow when this helper is needed.
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
    _client_skins: dict[str, SnakeSkin] = field(default_factory=dict)
    _skins_by_username: dict[str, SnakeSkin] = field(default_factory=dict)
    _client_chat_endpoints: dict[str, tuple[str, int]] = field(default_factory=dict)
    _chat_endpoint_by_username: dict[str, tuple[str, int]] = field(default_factory=dict)
    # Keyed by normalized username for case-insensitive stability.
    _wins_by_user: dict[str, int] = field(default_factory=dict)
    # Per-online-username reconnect/session version to detect leave+rejoin.
    _user_session_versions: dict[str, int] = field(default_factory=dict)
    _next_user_session_version: int = 1

    # Invitation and matchmaking registry.
    _pending_invites: dict[tuple[str, str], str] = field(default_factory=dict)
    _busy_invite_users: set[str] = field(default_factory=set)
    _quick_match_queue: list[str] = field(default_factory=list)
    _active_matches: dict[str, tuple[str, str]] = field(default_factory=dict)
    _user_to_match: dict[str, str] = field(default_factory=dict)
    _match_spectators: dict[str, set[str]] = field(default_factory=dict)
    _user_to_spectated_match: dict[str, str] = field(default_factory=dict)

    # Sprint 3 authoritative match runtime (game_id -> runtime state).
    _match_runtimes: dict[str, MatchRuntime] = field(default_factory=dict)
    # Short-lived handoff window to allow GUI->Pygame reconnect without ending match.
    _match_handoff_until: dict[str, float] = field(default_factory=dict)

    _lock: Lock = field(default_factory=Lock)

    # // Feature: Server State / Matchmaking
    # // Purpose: Register a new client connection and return generated client id.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def register_client(self, client_socket: socket.socket | None = None) -> str:
        """Register a new client connection and return generated client id."""

        with self._lock:
            client_id = f"client-{self._next_client_id}"
            self._next_client_id += 1
            self.connected_clients += 1
            self._client_usernames[client_id] = None
            self._client_sockets[client_id] = client_socket
            return client_id

    # // Feature: Server State / Matchmaking
    # // Purpose: Remove disconnected client and clean all linked lobby/match state.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def unregister_client(self, client_id: str) -> list[str]:
        """Remove disconnected client and clean all linked lobby/match state."""

        with self._lock:
            self.connected_clients = max(0, self.connected_clients - 1)
            previous_username = self._client_usernames.pop(client_id, None)
            self._client_sockets.pop(client_id, None)
            self._client_skins.pop(client_id, None)
            self._client_chat_endpoints.pop(client_id, None)

            if previous_username is not None:
                owner_id = self._username_to_client.get(_normalize_username(previous_username))
                game_id = self._find_game_id_for_user_locked(previous_username)
                keep_session = game_id is not None and self._is_handoff_active_locked(game_id)
                if owner_id == client_id:
                    self._username_to_client.pop(_normalize_username(previous_username), None)
                    if not keep_session:
                        self._user_session_versions.pop(previous_username, None)
                self._cleanup_user_lobby_state(previous_username)

            return sorted(self._username_to_client.keys())

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 5 PBI 5.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def unregister_client_with_events(self, client_id: str) -> list[dict[str, Any]]:
        """
        Sprint 5 PBI 5.4 + 5.5: unregister and return disconnect-driven events.

        Event shape:
        - {
            "type": "match_abandoned",
            "game_id": str,
            "players": (p1, p2),
            "spectators": (s1, s2, ...),
            "winner": str
          }
        """

        with self._lock:
            events: list[dict[str, Any]] = []
            self.connected_clients = max(0, self.connected_clients - 1)
            previous_username = self._client_usernames.pop(client_id, None)
            self._client_sockets.pop(client_id, None)
            self._client_skins.pop(client_id, None)
            self._client_chat_endpoints.pop(client_id, None)

            if previous_username is None:
                return events

            owner_id = self._username_to_client.get(_normalize_username(previous_username))
            game_id = self._find_game_id_for_user_locked(previous_username)
            keep_session = game_id is not None and self._is_handoff_active_locked(game_id)
            if owner_id == client_id:
                self._username_to_client.pop(_normalize_username(previous_username), None)
                if not keep_session:
                    self._user_session_versions.pop(previous_username, None)
            if game_id is not None:
                if self._is_handoff_active_locked(game_id):
                    # Intentional GUI->Pygame switch in progress; keep match alive.
                    self._cleanup_user_invites_locked(previous_username)
                    return events

                players = self._active_matches.get(game_id)
                if players is not None:
                    spectators = tuple(sorted(self._match_spectators.get(game_id, set()), key=str.casefold))
                    if len(players) == 2:
                        opponent = players[0] if players[1] == previous_username else players[1]
                    else:
                        opponent = "draw"
                    events.append(
                        {
                            "type": "match_abandoned",
                            "game_id": game_id,
                            "players": players,
                            "spectators": spectators,
                            "winner": opponent,
                            "disconnected_player": previous_username,
                        }
                    )
                self._teardown_match_locked(game_id)

            self._cleanup_user_lobby_state(previous_username)
            return events

    # // Feature: Server State / Matchmaking
    # // Purpose: Assign username if unique; returns status tuple (accepted/reason).
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def set_client_username(self, client_id: str, username: str) -> tuple[bool, str]:
        """Assign username if unique; returns status tuple (accepted/reason)."""

        with self._lock:
            if client_id not in self._client_usernames:
                return False, "unknown_client"

            normalized_username = _normalize_username(username)
            existing_owner = self._username_to_client.get(normalized_username)
            if existing_owner is not None and existing_owner != client_id:
                if self._release_stale_username_owner_locked(normalized_username, existing_owner):
                    existing_owner = self._username_to_client.get(normalized_username)

            if existing_owner is not None and existing_owner != client_id:
                if not self._can_reclaim_username_for_handoff_locked(normalized_username, existing_owner):
                    return False, "username_taken"

                # Handoff reclaim: release old owner mapping so reconnecting
                # client can continue the same match without username deadlock.
                self._username_to_client.pop(normalized_username, None)
                if existing_owner in self._client_usernames:
                    self._client_usernames[existing_owner] = None
                if existing_owner in self._client_sockets:
                    self._client_sockets[existing_owner] = None

            previous_username = self._client_usernames[client_id]
            if previous_username is not None and previous_username != username:
                self._username_to_client.pop(_normalize_username(previous_username), None)
                self._user_session_versions.pop(previous_username, None)
                self._chat_endpoint_by_username.pop(_normalize_username(previous_username), None)
                self._cleanup_user_lobby_state(previous_username)

            self._client_usernames[client_id] = username
            self._username_to_client[normalized_username] = client_id
            remembered_skin = self._skins_by_username.get(normalized_username)
            if remembered_skin is not None:
                self._client_skins[client_id] = remembered_skin
            remembered_endpoint = self._chat_endpoint_by_username.get(normalized_username)
            if remembered_endpoint is not None:
                self._client_chat_endpoints[client_id] = remembered_endpoint
            elif client_id in self._client_chat_endpoints:
                self._chat_endpoint_by_username[normalized_username] = self._client_chat_endpoints[client_id]
            self._user_session_versions[username] = self._next_user_session_version
            self._wins_by_user[normalized_username] = self._wins_by_user.get(normalized_username, 0)
            self._next_user_session_version += 1
            return True, "accepted"

    # // Feature: Server State / Matchmaking
    # // Purpose: Resolve currently assigned username for one client id.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_client_username(self, client_id: str) -> str | None:
        """Resolve currently assigned username for one client id."""

        with self._lock:
            return self._client_usernames.get(client_id)

    # // Feature: Server State / Matchmaking
    # // Purpose: Store a sanitized skin for one connected client.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def set_client_skin(self, client_id: str, raw_skin: dict | None) -> None:
        """Store a sanitized skin for one connected client."""

        skin = sanitize_skin(raw_skin)
        with self._lock:
            if client_id in self._client_usernames:
                self._client_skins[client_id] = skin
                username = self._client_usernames.get(client_id)
                if username is not None:
                    self._skins_by_username[_normalize_username(username)] = skin

    # // Feature: Server State / Matchmaking
    # // Purpose: Store a peer-chat endpoint advertised by one connected client.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def set_client_chat_endpoint(self, client_id: str, host: str | None, port: int | None) -> None:
        """Store a peer-chat endpoint advertised by one connected client."""

        # We do not open or proxy this socket from the server. We only remember
        # where the client says its P2P chat listener is, then publish that to
        # other clients in ONLINE_USERS.
        if host is None or port is None:
            return
        host_text = str(host).strip()
        try:
            port_num = int(port)
        except (TypeError, ValueError):
            return
        if not host_text or not (1 <= port_num <= 65535):
            return

        endpoint = (host_text, port_num)
        with self._lock:
            if client_id not in self._client_usernames:
                return
            self._client_chat_endpoints[client_id] = endpoint
            username = self._client_usernames.get(client_id)
            if username is not None:
                self._chat_endpoint_by_username[_normalize_username(username)] = endpoint

    # // Feature: Server State / Matchmaking
    # // Purpose: Return the skin dict for a username, or a default skin if unset.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_skin_dict_for_username(self, username: str) -> dict[str, str]:
        """Return the skin dict for a username, or a default skin if unset."""

        with self._lock:
            return self._skin_dict_for_username_locked(username)

    # // Feature: Server State / Matchmaking
    # // Purpose: Lock-free variant for callers that already hold `_lock`.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _skin_dict_for_username_locked(self, username: str) -> dict[str, str]:
        """Lock-free variant for callers that already hold `_lock`."""

        client_id = self._username_to_client.get(_normalize_username(username))
        skin = self._client_skins.get(client_id) if client_id is not None else None
        if skin is None:
            skin = self._skins_by_username.get(_normalize_username(username))
        return skin_to_dict(skin if skin is not None else SnakeSkin())

    # // Feature: Server State / Matchmaking
    # // Purpose: Return online usernames, keeping active-match players visible.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _online_usernames_locked(self) -> list[str]:
        """Return online usernames, keeping active-match players visible."""

        names_by_cf: dict[str, str] = {
            username.casefold(): username
            for username in self._client_usernames.values()
            if username is not None
        }
        for players in self._active_matches.values():
            for username in players:
                names_by_cf.setdefault(username.casefold(), username)
        return sorted(names_by_cf.values(), key=str.casefold)

    # // Feature: Server State / Matchmaking
    # // Purpose: Build authoritative skin map for both players in one active match.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _match_skins_locked(self, players: tuple[str, str]) -> dict[str, dict[str, str]]:
        """Build authoritative skin map for both players in one active match."""

        p1, p2 = players
        return {
            p1: self._skin_dict_for_username_locked(p1),
            p2: self._skin_dict_for_username_locked(p2),
        }

    # // Feature: Server State / Matchmaking
    # // Purpose: Attach authoritative skins as both top-level map and per-snake field.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _attach_skins_to_state_locked(
        self,
        state_payload: dict[str, Any],
        players: tuple[str, str],
    ) -> dict[str, Any]:
        """Attach authoritative skins as both top-level map and per-snake field."""

        skins = self._match_skins_locked(players)
        state_payload["skins"] = skins
        snakes_payload = state_payload.get("snakes")
        if isinstance(snakes_payload, list):
            for snake in snakes_payload:
                if not isinstance(snake, dict):
                    continue
                player = str(snake.get("player", "")).strip()
                if player:
                    skin = skins.get(player)
                    if skin is not None:
                        snake["skin"] = dict(skin)
        return state_payload

    # // Feature: Server State / Matchmaking
    # // Purpose: Return all online usernames sorted for deterministic payloads.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_online_users(self) -> list[str]:
        """Return all online usernames sorted for deterministic payloads."""

        with self._lock:
            return self._online_usernames_locked()

    # // Feature: Server State / Matchmaking
    # // Purpose: Return online usernames mapped to their current session version.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_online_user_sessions(self) -> dict[str, int]:
        """Return online usernames mapped to their current session version."""

        with self._lock:
            result: dict[str, int] = {}
            for username in self._online_usernames_locked():
                result[username] = self._user_session_versions.get(username, 0)
            return result

    # // Feature: Server State / Matchmaking
    # // Purpose: Return online usernames mapped to authoritative win totals.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_online_user_wins(self) -> dict[str, int]:
        """Return online usernames mapped to authoritative win totals."""

        with self._lock:
            result: dict[str, int] = {}
            for username in self._online_usernames_locked():
                result[username] = int(self._wins_by_user.get(_normalize_username(username), 0))
            return result

    # // Feature: Server State / Matchmaking
    # // Purpose: Return P2P chat listener endpoints for currently online usernames.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_chat_peers_snapshot(self) -> dict[str, dict[str, str | int]]:
        """Return P2P chat listener endpoints for currently online usernames."""

        with self._lock:
            peers: dict[str, dict[str, str | int]] = {}
            for username in self._online_usernames_locked():
                normalized = _normalize_username(username)
                client_id = self._username_to_client.get(normalized)
                endpoint = self._client_chat_endpoints.get(client_id) if client_id is not None else None
                if endpoint is None:
                    endpoint = self._chat_endpoint_by_username.get(normalized)
                if endpoint is None:
                    continue
                host, port = endpoint
                # Shape kept JSON-friendly because this rides inside the normal
                # ONLINE_USERS protocol message.
                peers[username] = {"host": host, "port": int(port)}
            return peers

    # // Feature: Server State / Matchmaking
    # // Purpose: Return active match ids with current player pairs for lobby routing.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_active_matches_snapshot(self) -> list[dict[str, Any]]:
        """Return active match ids with current player pairs for lobby routing."""

        with self._lock:
            snapshot: list[dict[str, Any]] = []
            for game_id, players in self._active_matches.items():
                runtime = self._match_runtimes.get(game_id)
                if runtime is None or runtime.status != "running":
                    continue
                snapshot.append(
                    {
                        "game_id": game_id,
                        "players": [str(players[0]), str(players[1])],
                    }
                )
            return snapshot

    # // Feature: Server State / Matchmaking
    # // Purpose: Increment one player's win total when username resolves online identity.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def record_win(self, username: str) -> bool:
        """Increment one player's win total when username resolves online identity."""

        with self._lock:
            canonical = self._resolve_username(username)
            if canonical is None:
                return False
            canonical_cf = _normalize_username(canonical)
            self._wins_by_user[canonical_cf] = int(self._wins_by_user.get(canonical_cf, 0)) + 1
            return True

    # // Feature: Server State / Matchmaking
    # // Purpose: Return currently connected sockets for broadcast operations.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_client_sockets(self) -> list[socket.socket]:
        """Return currently connected sockets for broadcast operations."""

        with self._lock:
            return [sock for sock in self._client_sockets.values() if sock is not None]

    # // Feature: Server State / Matchmaking
    # // Purpose: Find active socket for a given online username, if any.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_socket_for_username(self, username: str) -> socket.socket | None:
        """Find active socket for a given online username, if any."""

        with self._lock:
            client_id = self._username_to_client.get(_normalize_username(username))
            if client_id is None:
                return None
            return self._client_sockets.get(client_id)

    # // Feature: Server State / Matchmaking
    # // Purpose: True when fewer than two online users are available in lobby.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def has_waiting_player(self) -> bool:
        """True when fewer than two online users are available in lobby."""

        with self._lock:
            return len(self._username_to_client) < 2

    # // Feature: Server State / Matchmaking
    # // Purpose: Enforce one active match globally for basic-project compliance.
    # // Trigger: Called before state changes to enforce game and input rules.
    def _single_match_busy_locked(self) -> bool:
        """Enforce one active match globally for basic-project compliance."""

        return len(self._active_matches) > 0

    # // Feature: Server State / Matchmaking
    # // Purpose: Create pending invitation if both users are eligible.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def create_invitation(self, from_user: str, to_user: str) -> tuple[bool, str]:
        """Create pending invitation if both users are eligible."""

        with self._lock:
            from_user = self._resolve_username(from_user)
            to_user = self._resolve_username(to_user)
            if from_user is None or to_user is None:
                return False, "user_offline"
            if from_user == to_user:
                return False, "cannot_invite_self"
            if self._single_match_busy_locked():
                return False, "single_session_busy"
            if from_user in self._user_to_match or to_user in self._user_to_match:
                return False, "user_in_match"
            # If inviter moves to direct invites, remove from quick queue.
            self._remove_from_quick_queue_locked(from_user)
            invite_key = (from_user, to_user)
            if invite_key in self._pending_invites:
                return False, "user_busy"
            self._pending_invites[invite_key] = "pending"
            return True, "pending"

    # // Feature: Server State / Matchmaking
    # // Purpose: Queue one user for quick match, or pair immediately when possible.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def request_quick_match(self, username: str) -> tuple[bool, str, str | None, str | None]:
        """
        Queue one user for quick match, or pair immediately when possible.

        Returns:
        - (True, "waiting"|"already_waiting", None, None)
        - (True, "matched", opponent, game_id)
        - (False, "<reason>", None, None)
        """

        with self._lock:
            player = self._resolve_username(username)
            if player is None:
                return False, "user_offline", None, None
            if self._single_match_busy_locked():
                return False, "single_session_busy", None, None
            if player in self._user_to_match:
                return False, "user_in_match", None, None

            # Keep queue clean from stale/unavailable entries.
            cleaned: list[str] = []
            for queued in self._quick_match_queue:
                canonical = self._resolve_username(queued)
                if canonical is None:
                    continue
                if canonical in self._user_to_match:
                    continue
                if canonical not in cleaned:
                    cleaned.append(canonical)
            self._quick_match_queue = cleaned

            if player in self._quick_match_queue:
                return True, "already_waiting", None, None

            opponent: str | None = None
            while self._quick_match_queue:
                candidate = self._quick_match_queue.pop(0)
                if candidate == player:
                    continue
                if self._resolve_username(candidate) is None:
                    continue
                if candidate in self._user_to_match:
                    continue
                opponent = candidate
                break

            if opponent is None:
                self._quick_match_queue.append(player)
                return True, "waiting", None, None

            # Pair and start match immediately.
            self._cleanup_user_invites_locked(player)
            self._cleanup_user_invites_locked(opponent)
            self._remove_from_quick_queue_locked(player)
            self._remove_from_quick_queue_locked(opponent)

            game_id = f"game-{uuid4().hex[:8]}"
            self._active_matches[game_id] = (opponent, player)
            self._user_to_match[opponent] = game_id
            self._user_to_match[player] = game_id
            self._match_runtimes[game_id] = create_match_runtime(
                game_id=game_id,
                player_a=opponent,
                player_b=player,
            )
            return True, "matched", opponent, game_id

    # // Feature: Server State / Matchmaking
    # // Purpose: Remove one user from quick-match queue.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def cancel_quick_match(self, username: str) -> tuple[bool, str]:
        """Remove one user from quick-match queue."""

        with self._lock:
            player = self._resolve_username(username)
            if player is None:
                return False, "user_offline"
            if player not in self._quick_match_queue:
                return True, "not_waiting"
            self._remove_from_quick_queue_locked(player)
            return True, "cancelled"

    # // Feature: Server State / Matchmaking
    # // Purpose: Record that a player has connected via the game window socket.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def mark_player_in_game_window(self, username: str) -> None:
        """Record that a player has connected via the game window socket.

        Once both players have checked in, the pre-match countdown begins.
        If this completes the pair, the countdown is reset to its initial
        value so both players always get the full grace period.
        """
        with self._lock:
            canonical = self._resolve_username(username)
            if canonical is None:
                return
            game_id = self._user_to_match.get(canonical)
            if game_id is None:
                return
            runtime = self._match_runtimes.get(game_id)
            if runtime is None:
                return
            runtime.players_in_window.add(canonical)
            if len(runtime.players_in_window) >= 2:
                # Both players arrived — reset to full countdown so they both
                # see the complete 3-2-1 sequence from the same starting point.
                runtime.countdown_ticks = 25

    # // Feature: Server State / Matchmaking
    # // Purpose: Mark an active match for short reconnect handoff (GUI -> Pygame).
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def mark_match_handoff(self, username: str, ttl_seconds: float = 8.0) -> tuple[bool, str]:
        """Mark an active match for short reconnect handoff (GUI -> Pygame)."""

        with self._lock:
            canonical_username = self._resolve_username(username)
            if canonical_username is None:
                return False, "user_offline"

            game_id = self._user_to_match.get(canonical_username)
            if game_id is None:
                return False, "user_not_in_match"

            self._match_handoff_until[game_id] = time.monotonic() + max(1.0, ttl_seconds)
            return True, "handoff_marked"

    # // Feature: Server State / Matchmaking
    # // Purpose: Resolve invitation lifecycle action.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
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
                return True, "cancelled", None

            if action == "decline":
                self._pending_invites.pop(invite_key, None)
                return True, "declined", None

            if action == "accept":
                if self._single_match_busy_locked():
                    return False, "single_session_busy", None
                if from_user in self._user_to_match or to_user in self._user_to_match:
                    return False, "user_in_match", None

                self._pending_invites.pop(invite_key, None)
                # Once a match is accepted, clear any other invite rows involving
                # either matched player so lobby invite state stays consistent.
                self._cleanup_user_invites_locked(from_user)
                self._cleanup_user_invites_locked(to_user)
                self._remove_from_quick_queue_locked(from_user)
                self._remove_from_quick_queue_locked(to_user)

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

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 6 PBI 6.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def add_spectator(
        self,
        username: str,
        *,
        game_id: str | None = None,
        target_user: str | None = None,
    ) -> tuple[bool, str, str | None, tuple[str, str] | None]:
        """
        Sprint 6 PBI 6.2: attach one connected idle user to spectate a match.

        Match resolution strategy:
        - direct `game_id` when provided
        - otherwise resolve from `target_user` active match
        """

        with self._lock:
            spectator = self._resolve_username(username)
            if spectator is None:
                return False, "user_offline", None, None

            # Active players cannot spectate another game simultaneously.
            if spectator in self._user_to_match:
                return False, "user_in_match", None, None

            resolved_game_id = game_id
            if resolved_game_id is None:
                canonical_target = self._resolve_username(target_user or "")
                if canonical_target is not None:
                    resolved_game_id = self._user_to_match.get(canonical_target)

            if resolved_game_id is None:
                return False, "match_not_found", None, None

            players = self._active_matches.get(resolved_game_id)
            runtime = self._match_runtimes.get(resolved_game_id)
            if players is None or runtime is None:
                return False, "match_not_found", None, None
            if runtime.status == "finished":
                return False, "match_finished", None, None

            previous_game_id = self._user_to_spectated_match.get(spectator)
            if previous_game_id == resolved_game_id:
                return True, "already_spectating", resolved_game_id, players
            if previous_game_id is not None:
                self._remove_spectator_locked(spectator)

            spectators = self._match_spectators.setdefault(resolved_game_id, set())
            spectators.add(spectator)
            self._user_to_spectated_match[spectator] = resolved_game_id
            return True, "spectating", resolved_game_id, players

    # // Feature: Server State / Matchmaking
    # // Purpose: Detach one spectator session from any currently spectated match.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def remove_spectator(self, username: str) -> tuple[bool, str]:
        """Detach one spectator session from any currently spectated match."""

        with self._lock:
            spectator = self._resolve_username(username)
            if spectator is None:
                return False, "user_offline"
            removed = self._remove_spectator_locked(spectator)
            if not removed:
                return False, "not_spectating"
            return True, "removed"

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 6 PBI 6.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def resolve_live_match_for_interaction(
        self,
        username: str,
        *,
        game_id: str | None = None,
        target_user: str | None = None,
    ) -> tuple[bool, str, str | None, tuple[str, str] | None]:
        """
        Sprint 6 PBI 6.4 helper: resolve one running match for fan interactions.

        Resolution order:
        - explicit `game_id`
        - active match of `target_user`
        - sender's own active match
        - sender's current spectated match
        """

        with self._lock:
            actor = self._resolve_username(username)
            if actor is None:
                return False, "user_offline", None, None

            resolved_game_id = game_id
            if resolved_game_id is None and target_user:
                canonical_target = self._resolve_username(target_user)
                if canonical_target is not None:
                    resolved_game_id = self._user_to_match.get(canonical_target)

            if resolved_game_id is None:
                resolved_game_id = self._user_to_match.get(actor)

            if resolved_game_id is None:
                resolved_game_id = self._user_to_spectated_match.get(actor)

            if resolved_game_id is None:
                return False, "match_not_found", None, None

            players = self._active_matches.get(resolved_game_id)
            runtime = self._match_runtimes.get(resolved_game_id)
            if players is None or runtime is None:
                return False, "match_not_found", None, None
            if runtime.status != "running":
                return False, "match_finished", None, players

            return True, "ok", resolved_game_id, players

    # // Feature: Server State / Matchmaking
    # // Purpose: Return both usernames assigned to one game id.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_match_players(self, game_id: str) -> tuple[str, str] | None:
        """Return both usernames assigned to one game id."""

        with self._lock:
            return self._active_matches.get(game_id)

    # // Feature: Server State / Matchmaking
    # // Purpose: Return packaged game_state dictionary for one active match.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_match_state_dict(self, game_id: str) -> dict[str, Any] | None:
        """Return packaged game_state dictionary for one active match."""

        with self._lock:
            runtime = self._match_runtimes.get(game_id)
            if runtime is None:
                return None
            state = to_protocol_state(runtime)
            players = self._active_matches.get(game_id)
            if players is not None:
                state = self._attach_skins_to_state_locked(state, players)
            return state

    # // Feature: Server State / Matchmaking
    # // Purpose: Process one movement command and optionally advance the match tick.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
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

            # Sprint 5 PBI 5.1: movement is queued here, while simulation
            # stepping is handled by the continuous server game loop.

            # State and end-of-match broadcasts are produced by the dedicated
            # server game loop. Movement handling only needs to enqueue input.
            return True, "updated", game_id, None, None

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 5 PBI 5.
    # // Trigger: Called by the simulation/game loop to advance runtime state.
    def step_active_matches(self) -> list[dict[str, Any]]:
        """
        Sprint 5 PBI 5.1 + 5.6: advance all active matches and collect updates.

        Returns per-match update payloads ready for broadcast:
        - game_id
        - players
        - state
        - optional game_over payload
        """

        with self._lock:
            updates: list[dict[str, Any]] = []
            games_to_cleanup: list[str] = []

            for game_id, runtime in list(self._match_runtimes.items()):
                self._cleanup_expired_handoff_locked(game_id)
                # Sprint 5 PBI 5.1:
                # Run authoritative simulation continuously on server tick,
                # even when no fresh movement command arrived this frame.
                if runtime.status == "running":
                    if runtime.countdown_ticks > 0:
                        if len(runtime.players_in_window) >= 2:
                            runtime.countdown_ticks -= 1
                    else:
                        # Timer must be real-time authoritative and independent
                        # from movement/input cadence.
                        now = time.monotonic()
                        if runtime.started_at_monotonic is None:
                            runtime.started_at_monotonic = now
                        runtime.tick = max(0, int(now - runtime.started_at_monotonic))
                        step_runtime(runtime)

                players = self._active_matches.get(game_id)
                if players is None:
                    games_to_cleanup.append(game_id)
                    continue

                game_over_payload: dict[str, Any] | None = None
                if runtime.status == "finished":
                    final_scores = {
                        username: runtime.snakes[username].health
                        for username in runtime.players
                    }
                    winner = runtime.winner or "draw"
                    if winner and str(winner).casefold() not in {"draw", "none", "-"}:
                        winner_cf = _normalize_username(str(winner))
                        self._wins_by_user[winner_cf] = int(self._wins_by_user.get(winner_cf, 0)) + 1
                    game_over_payload = {
                        "game_id": game_id,
                        "winner": winner,
                        "final_scores": final_scores,
                        "reason": runtime.end_reason or "match_finished",
                    }
                    games_to_cleanup.append(game_id)

                state_payload = to_protocol_state(runtime)
                state_payload = self._attach_skins_to_state_locked(state_payload, players)

                updates.append(
                    {
                        "game_id": game_id,
                        "players": players,
                        "spectators": tuple(sorted(self._match_spectators.get(game_id, set()), key=str.casefold)),
                        "recipients": tuple(
                            [
                                *players,
                                *[
                                    user
                                    for user in sorted(self._match_spectators.get(game_id, set()), key=str.casefold)
                                    if user not in players
                                ],
                            ]
                        ),
                        "state": state_payload,
                        "game_over": game_over_payload,
                    }
                )

            # Sprint 5 PBI 5.6: clean ended sessions after publishing final state.
            for game_id in games_to_cleanup:
                self._teardown_match_locked(game_id)

            return updates

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 4 PBI 4.
    # // Trigger: Called before state changes to enforce game and input rules.
    def validate_movement_command(self, player: str, direction: str) -> tuple[bool, str, str | None]:
        """
        Sprint 4 PBI 4.11: validate movement command before simulation update.

        This method performs fast guard checks without mutating runtime:
        - player must be in an active match
        - match runtime must exist
        - match must still be running
        - player snake must be alive
        - direction must be one of allowed protocol values
        """

        with self._lock:
            game_id = self._user_to_match.get(player)
            if game_id is None:
                return False, "player_not_in_match", None

            runtime = self._match_runtimes.get(game_id)
            if runtime is None:
                return False, "match_not_found", game_id

            if runtime.status != "running":
                return False, "match_finished", game_id

            snake = runtime.snakes.get(player)
            if snake is None:
                return False, "player_not_in_match", game_id

            if not snake.alive:
                return False, "player_not_alive", game_id

            if direction not in {"up", "down", "left", "right"}:
                return False, "invalid_direction", game_id

            return True, "ok", game_id

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 4 PBI 4.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_match_player_sockets(self, game_id: str) -> list[socket.socket]:
        """
        Sprint 4 PBI 4.12 helper: resolve active-player sockets for one match.

        The caller can iterate this list to broadcast authoritative GAME_STATE
        updates only to players in the active match.
        """

        with self._lock:
            players = self._active_matches.get(game_id)
            if players is None:
                return []

            sockets: list[socket.socket] = []
            for username in players:
                client_id = self._username_to_client.get(_normalize_username(username))
                if client_id is None:
                    continue
                sock = self._client_sockets.get(client_id)
                if sock is not None:
                    sockets.append(sock)
            return sockets

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 6 PBI 6.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_match_session_sockets(self, game_id: str) -> list[socket.socket]:
        """
        Sprint 6 PBI 6.2 helper: resolve sockets for players + spectators.

        This allows gameplay state broadcasts to include spectator sessions.
        """

        with self._lock:
            participants: list[str] = []
            players = self._active_matches.get(game_id)
            if players is not None:
                participants.extend(list(players))
            participants.extend(sorted(self._match_spectators.get(game_id, set()), key=str.casefold))

            sockets: list[socket.socket] = []
            seen_client_ids: set[str] = set()
            for username in participants:
                client_id = self._username_to_client.get(_normalize_username(username))
                if client_id is None or client_id in seen_client_ids:
                    continue
                seen_client_ids.add(client_id)
                sock = self._client_sockets.get(client_id)
                if sock is not None:
                    sockets.append(sock)
            return sockets

    # // Feature: Server State / Matchmaking
    # // Purpose: Ignore client pie-spawn events; server simulation owns pie generation.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def spawn_blob_pie(
        self,
        *,
        actor: str,
        game_id: str | None,
        x: int,
        y: int,
        kind: str,
    ) -> tuple[bool, str]:
        """
        Ignore legacy client pie-spawn event.

        Basic-project server rules require the backend simulation to generate
        pies authoritatively. Client `spawn_pie` actions are accepted as
        backward-compatible no-ops.
        """

        _ = (actor, game_id, x, y, kind)
        return False, "server_authoritative_spawn"

    # // Feature: Server State / Matchmaking
    # // Purpose: Sprint 5 PBI 5.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def get_idle_users(self) -> list[str]:
        """Sprint 5 PBI 5.3: list online users who are not in active matches."""

        with self._lock:
            online_users = self._online_usernames_locked()
            return sorted([user for user in online_users if user not in self._user_to_match], key=str.casefold)

    # // Feature: Server State / Matchmaking
    # // Purpose: Internal cleanup for invitation/match state linked to a user.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _cleanup_user_lobby_state(self, username: str) -> None:
        """Internal cleanup for invitation/match state linked to a user."""

        self._remove_spectator_locked(username)
        self._remove_from_quick_queue_locked(username)
        self._cleanup_user_invites_locked(username)

        game_id = self._user_to_match.pop(username, None)
        if game_id is not None:
            self._teardown_match_locked(game_id)

    # // Feature: Server State / Matchmaking
    # // Purpose: Resolve any username casing to the canonical currently-registered value.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _resolve_username(self, username: str) -> str | None:
        """
        Resolve any username casing to the canonical currently-registered value.
        """

        client_id = self._username_to_client.get(_normalize_username(username))
        if client_id is None:
            return None
        return self._client_usernames.get(client_id)

    # // Feature: Server State / Matchmaking
    # // Purpose: Remove one match and clear all player->match links.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _teardown_match_locked(self, game_id: str) -> None:
        """
        Remove one match and clear all player->match links.

        This helper requires the caller to already hold `self._lock`.
        """

        players = self._active_matches.pop(game_id, None)
        self._match_runtimes.pop(game_id, None)
        self._match_handoff_until.pop(game_id, None)
        spectators = tuple(self._match_spectators.pop(game_id, set()))
        for spectator in spectators:
            if self._user_to_spectated_match.get(spectator) == game_id:
                self._user_to_spectated_match.pop(spectator, None)
        if players is None:
            return
        for player in players:
            self._user_to_match.pop(player, None)

    # // Feature: Server State / Matchmaking
    # // Purpose: Remove one user from spectator tracking while lock is held.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _remove_spectator_locked(self, username: str) -> bool:
        """Remove one user from spectator tracking while lock is held."""

        game_id = self._user_to_spectated_match.pop(username, None)
        if game_id is None:
            return False

        spectators = self._match_spectators.get(game_id)
        if spectators is None:
            return True

        spectators.discard(username)
        if not spectators:
            self._match_spectators.pop(game_id, None)
        return True

    # // Feature: Server State / Matchmaking
    # // Purpose: Resolve a user's active game id while holding lock.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _find_game_id_for_user_locked(self, username: str) -> str | None:
        """
        Resolve a user's active game id while holding lock.

        Uses both direct mapping and active-match scan to tolerate ordering
        races between disconnect cleanup and match lifecycle cleanup.
        """

        game_id = self._user_to_match.get(username)
        if game_id is not None:
            return game_id

        for candidate_game_id, players in self._active_matches.items():
            if username in players:
                return candidate_game_id
        return None

    # // Feature: Server State / Matchmaking
    # // Purpose: Remove pending invite state for one user while lock is held.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _cleanup_user_invites_locked(self, username: str) -> None:
        """Remove pending invite state for one user while lock is held."""

        invites_to_remove = [key for key in self._pending_invites if username in key]
        for from_user, to_user in invites_to_remove:
            self._pending_invites.pop((from_user, to_user), None)

    # // Feature: Server State / Matchmaking
    # // Purpose: Drop one username from quick-match queue while lock is held.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _remove_from_quick_queue_locked(self, username: str) -> None:
        """Drop one username from quick-match queue while lock is held."""

        self._quick_match_queue = [u for u in self._quick_match_queue if u != username]

    # // Feature: Server State / Matchmaking
    # // Purpose: Check if a match is currently in reconnect handoff grace window.
    # // Trigger: Called before state changes to enforce game and input rules.
    def _is_handoff_active_locked(self, game_id: str) -> bool:
        """Check if a match is currently in reconnect handoff grace window."""

        until = self._match_handoff_until.get(game_id)
        if until is None:
            return False
        return until > time.monotonic()

    # // Feature: Server State / Matchmaking
    # // Purpose: Drop stale handoff metadata for a match when ttl elapsed.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _cleanup_expired_handoff_locked(self, game_id: str) -> None:
        """Drop stale handoff metadata for a match when ttl elapsed."""

        until = self._match_handoff_until.get(game_id)
        if until is None:
            return
        if until <= time.monotonic():
            self._match_handoff_until.pop(game_id, None)

    # // Feature: Server State / Matchmaking
    # // Purpose: Allow username reclaim only during an active handoff window.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _can_reclaim_username_for_handoff_locked(self, normalized_username: str, owner_id: str) -> bool:
        """Allow username reclaim only during an active handoff window."""

        owner_username = self._client_usernames.get(owner_id)
        if owner_username is None:
            return False

        if _normalize_username(owner_username) != normalized_username:
            return False

        owner_game_id = self._find_game_id_for_user_locked(owner_username)
        if owner_game_id is None:
            return False

        return self._is_handoff_active_locked(owner_game_id)

    # // Feature: Server State / Matchmaking
    # // Purpose: Release username ownership when previous owner socket is already dead.
    # // Trigger: Called by the server state / matchmaking flow when this helper is needed.
    def _release_stale_username_owner_locked(self, normalized_username: str, owner_id: str) -> bool:
        """
        Release username ownership when previous owner socket is already dead.

        This prevents long-lived "username taken" loops after client-side
        transitions where old sockets were closed but mapping cleanup raced.
        """

        owner_socket = self._client_sockets.get(owner_id)
        if owner_socket is not None and owner_socket.fileno() != -1:
            return False

        self._username_to_client.pop(normalized_username, None)
        if owner_id in self._client_usernames:
            self._client_usernames[owner_id] = None
        if owner_id in self._client_sockets:
            self._client_sockets[owner_id] = None
        return True
