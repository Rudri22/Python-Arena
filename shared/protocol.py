"""
Shared message protocol for Python-Arena.\n\nPBI 1.2: Define game state data structure.

Sprint 1 focuses on agreeing on one message shape that both the client
and the server can use. If we keep that contract in one file, later PBIs
become much easier because everybody speaks the same "language".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Every network message must declare one of these protocol types."""

    CONNECT = "connect"
    USERNAME = "username_submission"
    ONLINE_USERS = "online_users"
    INVITATION = "invitation"
    MOVEMENT = "movement"
    GAME_STATE = "game_state"
    GAME_OVER = "game_over"
    CHAT = "chat"
    EMOTE = "emote"
    ERROR = "error"


class ProtocolError(ValueError):
    """Raised when an incoming message does not match the protocol."""


# These dataclasses define the shared game-state structure for PBI 1.2.
# They let both the server and client agree on the exact shape of snakes,
# positions, pies, obstacles, timer values, and winner information.


@dataclass(slots=True)
class Position:
    """A single coordinate on the arena grid."""

    x: int
    y: int


@dataclass(slots=True)
class SnakeState:
    """
    Represents one snake/player in the current match.

    `body` stores all grid cells occupied by the snake, usually from head
    to tail. `health` is included because the game design mentions it as a
    first-class part of the game state.
    """

    player: str
    body: list[Position]
    direction: str
    health: int
    alive: bool = True


@dataclass(slots=True)
class PieState:
    """Represents one pie collectible on the game board."""

    pie_id: str
    position: Position
    points: int = 1


@dataclass(slots=True)
class ObstacleState:
    """Represents a blocked cell such as a wall or hazard."""

    obstacle_id: str
    position: Position
    kind: str = "wall"


@dataclass(slots=True)
class TimerState:
    """
    Stores time-related values for the match.

    `remaining_seconds` is useful for countdown matches.
    `elapsed_seconds` is useful for survival or stopwatch-style matches.
    """

    total_seconds: int
    remaining_seconds: int
    elapsed_seconds: int = 0


@dataclass(slots=True)
class SnakeSkin:
    """Per-player cosmetic appearance chosen in the lobby."""

    color: str = "none"
    pattern: str = "solid"
    hat: str = "none"
    tail: str = "none"
    eyes: str = "normal"


SKIN_COLORS: dict[str, tuple[int, int, int]] = {
    "none":   (76, 246, 132),
    "venom":  (76, 246, 132),
    "moss":   (72, 140, 72),
    "rot":    (158, 196, 60),
    "toxin":  (190, 255, 90),
    "ember":  (244, 78, 62),
    "forge":  (230, 130, 40),
    "ash":    (110, 110, 115),
    "stone":  (160, 160, 160),
    "iron":   (80, 90, 110),
    "bone":   (226, 218, 196),
    "dirt":   (150, 110, 70),
    "dusk":   (95, 70, 140),
    "ocean":  (72, 166, 238),
    "coral":  (250, 120, 116),
    "mint":   (122, 246, 196),
    "onyx":   (68, 74, 92),
}

# Texture selection is intentionally disabled in the lobby UI.
# Keep `pattern` in the protocol for wire/backward compatibility, but only
# permit the neutral solid style.
PATTERNS: tuple[str, ...] = ("solid",)
HATS: tuple[str, ...] = (
    "none",
    "helmet",
    "crown",
    "wizard",
    "jester",
    "bandana",
    "beanie",
    "horns",
)
TAILS: tuple[str, ...] = ("none", "rattle", "spike", "flame", "fin", "club", "leaf")
EYE_STYLES: tuple[str, ...] = ("normal", "fierce", "glow", "visor", "sleepy", "star", "cyber")


# // Feature: Protocol Serialization
# // Purpose: Coerce a raw dict into a valid SnakeSkin, falling back to defaults.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def sanitize_skin(raw: dict | None) -> SnakeSkin:
    """Coerce a raw dict into a valid SnakeSkin, falling back to defaults."""

    defaults = SnakeSkin()
    if not isinstance(raw, dict):
        return defaults

    color = raw.get("color", defaults.color)
    pattern = raw.get("pattern", defaults.pattern)
    hat = raw.get("hat", defaults.hat)
    tail = raw.get("tail", defaults.tail)
    eyes = raw.get("eyes", defaults.eyes)

    return SnakeSkin(
        color=color if color in SKIN_COLORS else defaults.color,
        pattern=pattern if pattern in PATTERNS else defaults.pattern,
        hat=hat if hat in HATS else defaults.hat,
        tail=tail if tail in TAILS else defaults.tail,
        eyes=eyes if eyes in EYE_STYLES else defaults.eyes,
    )


# // Feature: Protocol Serialization
# // Purpose: Convert a SnakeSkin to a plain JSON-ready dict.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def skin_to_dict(skin: SnakeSkin) -> dict[str, str]:
    """Convert a SnakeSkin to a plain JSON-ready dict."""

    return asdict(skin)


# // Feature: Protocol Serialization
# // Purpose: Parse a dict (possibly from the wire) into a validated SnakeSkin.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def skin_from_dict(data: dict | None) -> SnakeSkin:
    """Parse a dict (possibly from the wire) into a validated SnakeSkin."""

    return sanitize_skin(data)


@dataclass(slots=True)
class GameState:
    """
    Full shared state of a running or finished game.

    This structure covers the items listed in PBI 1.2:
    snakes, positions, pies, obstacles, health, timer, and winner.
    """

    game_id: str
    snakes: list[SnakeState]
    pies: list[PieState]
    obstacles: list[ObstacleState]
    timer: TimerState
    winner: str | None = None
    status: str = "waiting"


# Each message type has a minimum set of payload fields it must contain.
# This keeps validation simple while still giving us flexibility to add more
# optional fields later without breaking the protocol.
REQUIRED_FIELDS: dict[MessageType, set[str]] = {
    MessageType.CONNECT: {"client_id"},
    MessageType.USERNAME: {"username"},
    MessageType.ONLINE_USERS: {"users"},
    MessageType.INVITATION: {"from_user", "to_user", "action"},
    MessageType.MOVEMENT: {"player", "direction"},
    MessageType.GAME_STATE: {"game_id", "state"},
    MessageType.GAME_OVER: {"game_id", "winner"},
    MessageType.CHAT: {"sender", "message"},
    MessageType.EMOTE: {"sender", "emote"},
    MessageType.ERROR: {"message"},
}


# // Feature: Protocol Serialization
# // Purpose: Build a protocol message using the shared shape used across the project.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def build_message(message_type: MessageType | str, **payload: Any) -> dict[str, Any]:
    """
    Build a protocol message using the shared shape used across the project.

    All messages follow the same top-level structure:
    {
        "type": "<message-type>",
        "payload": { ... message-specific data ... }
    }
    """

    normalized_type = MessageType(message_type)
    message = {"type": normalized_type.value, "payload": payload}
    validate_message(message)
    return message


# // Feature: Protocol Serialization
# // Purpose: Validate one decoded message dictionary.
# // Trigger: Called before state changes to enforce game and input rules.
def validate_message(message: dict[str, Any]) -> dict[str, Any]:
    """
    Validate one decoded message dictionary.

    We return the same message back so this helper can be used inline while
    receiving data from the socket code.
    """

    if not isinstance(message, dict):
        raise ProtocolError("Message must be a dictionary.")

    if "type" not in message:
        raise ProtocolError("Message is missing the 'type' field.")

    if "payload" not in message:
        raise ProtocolError("Message is missing the 'payload' field.")

    if not isinstance(message["payload"], dict):
        raise ProtocolError("Message 'payload' must be a dictionary.")

    try:
        message_type = MessageType(message["type"])
    except ValueError as error:
        raise ProtocolError(f"Unknown message type: {message['type']}") from error

    missing_fields = REQUIRED_FIELDS[message_type] - set(message["payload"])
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ProtocolError(
            f"Message '{message_type.value}' is missing required payload fields: {missing}"
        )

    return message


# // Feature: Protocol Serialization
# // Purpose: Convert a validated message to JSON text.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def serialize_message(message: dict[str, Any]) -> str:
    """
    Convert a validated message to JSON text.

    We do not add a newline here; transport helpers can decide whether they
    want line-delimited framing or another framing strategy.
    """

    validate_message(message)
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False)


# // Feature: Protocol Serialization
# // Purpose: Parse raw JSON text received from the network into a validated message.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def parse_message(raw_message: str) -> dict[str, Any]:
    """
    Parse raw JSON text received from the network into a validated message.
    """

    try:
        decoded = json.loads(raw_message)
    except json.JSONDecodeError as error:
        raise ProtocolError("Incoming data is not valid JSON.") from error

    return validate_message(decoded)


# The builder helpers below make calling code much cleaner. Instead of
# manually remembering field names all over the project, the client/server
# can call small descriptive functions from this shared file.


# // Feature: Protocol Serialization
# // Purpose: Convert a position to plain dictionary form for JSON transport.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def position_to_dict(position: Position | dict[str, int]) -> dict[str, int]:
    """
    Convert a position to plain dictionary form for JSON transport.

    We keep this separate because positions appear in movement updates,
    snake bodies, pies, and obstacles.
    """

    if isinstance(position, Position):
        return asdict(position)
    return position


# // Feature: Protocol Serialization
# // Purpose: Convert a typed `GameState` object into a JSON-ready dictionary.
# // Trigger: Called by the protocol serialization flow when this helper is needed.
def state_to_dict(state: GameState) -> dict[str, Any]:
    """
    Convert a typed `GameState` object into a JSON-ready dictionary.

    `asdict` recursively converts nested dataclasses, which means every
    snake body segment, pie position, obstacle position, and timer block
    becomes plain data the network layer can serialize.
    """

    return asdict(state)


# // Feature: Protocol Serialization
# // Purpose: Create one board position.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_position(x: int, y: int) -> Position:
    """Create one board position."""

    return Position(x=x, y=y)


# // Feature: Protocol Serialization
# // Purpose: Create one snake entry for the shared game state.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_snake_state(
    player: str,
    body: list[Position],
    direction: str,
    health: int,
    alive: bool = True,
) -> SnakeState:
    """Create one snake entry for the shared game state."""

    return SnakeState(
        player=player,
        body=body,
        direction=direction,
        health=health,
        alive=alive,
    )


# // Feature: Protocol Serialization
# // Purpose: Create one pie entry for the shared game state.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_pie_state(pie_id: str, position: Position, points: int = 1) -> PieState:
    """Create one pie entry for the shared game state."""

    return PieState(pie_id=pie_id, position=position, points=points)


# // Feature: Protocol Serialization
# // Purpose: Create one obstacle entry for the shared game state.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_obstacle_state(
    obstacle_id: str,
    position: Position,
    kind: str = "wall",
) -> ObstacleState:
    """Create one obstacle entry for the shared game state."""

    return ObstacleState(obstacle_id=obstacle_id, position=position, kind=kind)


# // Feature: Protocol Serialization
# // Purpose: Create the timer block used inside the game state.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_timer_state(
    total_seconds: int,
    remaining_seconds: int,
    elapsed_seconds: int = 0,
) -> TimerState:
    """Create the timer block used inside the game state."""

    return TimerState(
        total_seconds=total_seconds,
        remaining_seconds=remaining_seconds,
        elapsed_seconds=elapsed_seconds,
    )


# // Feature: Protocol Serialization
# // Purpose: Build the full game state object used by the server and protocol.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_game_state(
    game_id: str,
    snakes: list[SnakeState],
    pies: list[PieState],
    obstacles: list[ObstacleState],
    timer: TimerState,
    winner: str | None = None,
    status: str = "waiting",
) -> GameState:
    """
    Build the full game state object used by the server and protocol.

    This is the central structure for PBI 1.2 because it groups every
    gameplay element in one shared object.
    """

    return GameState(
        game_id=game_id,
        snakes=snakes,
        pies=pies,
        obstacles=obstacles,
        timer=timer,
        winner=winner,
        status=status,
    )


# // Feature: Protocol Serialization
# // Purpose: Sent when a client first connects to the server.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_connect_message(
    client_id: str,
    client_version: str = "1.0",
) -> dict[str, Any]:
    """Sent when a client first connects to the server."""

    return build_message(
        MessageType.CONNECT,
        client_id=client_id,
        client_version=client_version,
    )


# // Feature: Protocol Serialization
# // Purpose: Sent by the client after connection so the server can register a name.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_username_message(
    username: str,
    skin: dict[str, str] | SnakeSkin | None = None,
    chat_host: str | None = None,
    chat_port: int | None = None,
) -> dict[str, Any]:
    """Sent by the client after connection so the server can register a name."""

    payload: dict[str, Any] = {"username": username}
    if skin is not None:
        payload["skin"] = skin_to_dict(skin) if isinstance(skin, SnakeSkin) else dict(skin)
    if chat_host is not None:
        payload["chat_host"] = str(chat_host)
    if chat_port is not None:
        payload["chat_port"] = int(chat_port)
    return build_message(MessageType.USERNAME, **payload)


# // Feature: Protocol Serialization
# // Purpose: Broadcast by the server whenever the lobby user list changes.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_online_users_message(users: list[str]) -> dict[str, Any]:
    """Broadcast by the server whenever the lobby user list changes."""

    return build_message(MessageType.ONLINE_USERS, users=users)


# // Feature: Protocol Serialization
# // Purpose: Notify one user that they are currently in lobby waiting state.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_waiting_message(
    username: str,
    reason: str = "no_opponent_available",
) -> dict[str, Any]:
    """
    Notify one user that they are currently in lobby waiting state.

    We keep this under `INVITATION` with action=`waiting` to remain
    backward-compatible with the existing protocol message types while still
    giving frontend code a clear, explicit lobby state signal.
    """

    return build_message(
        MessageType.INVITATION,
        from_user="SERVER",
        to_user=username,
        action="waiting",
        reason=reason,
    )


# // Feature: Protocol Serialization
# // Purpose: Invitation messages cover invite lifecycle events.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_invitation_message(
    from_user: str,
    to_user: str,
    action: str,
    game_id: str | None = None,
    skins: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Invitation messages cover invite lifecycle events.

    Example actions:
    - "send"
    - "accept"
    - "decline"
    - "cancel"
    """

    payload: dict[str, Any] = {
        "from_user": from_user,
        "to_user": to_user,
        "action": action,
    }
    if game_id is not None:
        payload["game_id"] = game_id
    if skins is not None:
        payload["skins"] = skins

    return build_message(MessageType.INVITATION, **payload)


# // Feature: Protocol Serialization
# // Purpose: Build standardized invitation lifecycle responses for Sprint 2 lobby flow.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_invitation_status_message(
    from_user: str,
    to_user: str,
    action: str,
    status: str,
    message: str | None = None,
    game_id: str | None = None,
) -> dict[str, Any]:
    """
    Build standardized invitation lifecycle responses for Sprint 2 lobby flow.

    Typical combinations:
    - action="send", status="accepted_for_delivery"
    - action="send", status="rejected_busy"
    - action="accept", status="accepted"
    - action="decline", status="accepted"
    - action="cancel", status="accepted"
    """

    payload: dict[str, Any] = {
        "from_user": from_user,
        "to_user": to_user,
        "action": action,
        "status": status,
    }
    if message is not None:
        payload["message"] = message
    if game_id is not None:
        payload["game_id"] = game_id

    return build_message(MessageType.INVITATION, **payload)


# // Feature: Protocol Serialization
# // Purpose: Build a match-start notification once an invitation is accepted.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_match_start_message(
    game_id: str,
    players: list[str],
    skins: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Build a match-start notification once an invitation is accepted.

    We encode this as an invitation action to keep compatibility with the
    current protocol enum while still carrying explicit game startup data.
    """

    if len(players) != 2:
        raise ProtocolError("Match start must include exactly two players.")

    payload: dict[str, Any] = {
        "from_user": "SERVER",
        "to_user": "both_players",
        "action": "match_started",
        "game_id": game_id,
        "players": players,
    }
    if skins is not None:
        payload["skins"] = skins

    return build_message(MessageType.INVITATION, **payload)


# // Feature: Protocol Serialization
# // Purpose: Sent during gameplay when a player moves.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_movement_message(
    player: str,
    direction: str,
    position: dict[str, int] | Position | None = None,
) -> dict[str, Any]:
    """
    Sent during gameplay when a player moves.

    `direction` can be values such as "up", "down", "left", or "right".
    `position` is optional because some game loops prefer to send only input
    commands while the authoritative server calculates the real position.
    """

    payload: dict[str, Any] = {"player": player, "direction": direction}
    if position is not None:
        payload["position"] = position_to_dict(position)

    return build_message(MessageType.MOVEMENT, **payload)


# // Feature: Protocol Serialization
# // Purpose: Sent by the server to share the latest authoritative game state.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_game_state_message(
    game_id: str,
    state: dict[str, Any] | GameState,
) -> dict[str, Any]:
    """
    Sent by the server to share the latest authoritative game state.

    This helper accepts either a plain dictionary or the typed `GameState`
    dataclass so the rest of the project can stay flexible.
    """

    serialized_state = state_to_dict(state) if isinstance(state, GameState) else state
    return build_message(MessageType.GAME_STATE, game_id=game_id, state=serialized_state)


# // Feature: Protocol Serialization
# // Purpose: Sent once the match ends so both clients can show the result screen.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_game_over_message(
    game_id: str,
    winner: str,
    final_scores: dict[str, int] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Sent once the match ends so both clients can show the result screen."""

    payload: dict[str, Any] = {"game_id": game_id, "winner": winner}
    if final_scores is not None:
        payload["final_scores"] = final_scores
    if reason is not None:
        payload["reason"] = reason

    return build_message(MessageType.GAME_OVER, **payload)


# // Feature: Protocol Serialization
# // Purpose: Chat can support lobby-wide messages or direct messages.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_chat_message(
    sender: str,
    message: str,
    recipient: str | None = None,
) -> dict[str, Any]:
    """
    Chat can support lobby-wide messages or direct messages.

    If `recipient` is None, treat it as a public chat message.
    """

    payload: dict[str, Any] = {"sender": sender, "message": message}
    if recipient is not None:
        payload["recipient"] = recipient

    return build_message(MessageType.CHAT, **payload)


# // Feature: Protocol Serialization
# // Purpose: Utility helper for reporting protocol or server-side problems.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_error_message(message: str, details: str | None = None) -> dict[str, Any]:
    """Utility helper for reporting protocol or server-side problems."""

    payload: dict[str, Any] = {"message": message}
    if details is not None:
        payload["details"] = details

    return build_message(MessageType.ERROR, **payload)


# // Feature: Protocol Serialization
# // Purpose: Sent by a player to broadcast an in-game emote to both players and spectators.
# // Trigger: Called when constructing protocol payloads or runtime data structures.
def make_emote_message(
    sender: str,
    emote: str,
    game_id: str | None = None,
) -> dict[str, Any]:
    """Sent by a player to broadcast an in-game emote to both players and spectators."""

    payload: dict[str, Any] = {"sender": sender, "emote": emote}
    if game_id is not None:
        payload["game_id"] = game_id

    return build_message(MessageType.EMOTE, **payload)

