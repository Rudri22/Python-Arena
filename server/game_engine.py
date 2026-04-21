"""
Sprint 3 backend game engine.

This module owns the authoritative match simulation used by the server:
- board model
- snake runtime state
- pie and obstacle generation
- movement and collision logic
- end-of-match and winner calculation
- game-state packaging for broadcast
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from shared.protocol import (
    GameState,
    Position,
    make_game_state,
    make_obstacle_state,
    make_pie_state,
    make_position,
    make_snake_state,
    make_timer_state,
    state_to_dict,
)

# Core deterministic match settings for Sprint 3.
BOARD_WIDTH = 20
BOARD_HEIGHT = 20
INITIAL_SNAKE_LENGTH = 3
# Temporary tuning for local testing: keep players alive much longer.
INITIAL_HEALTH = 1_000
DEFAULT_PIE_HEALTH_GAIN = 30
DEFAULT_PIE_DAMAGE = 40
WALL_COLLISION_DAMAGE = 20
OTHER_SNAKE_COLLISION_DAMAGE = 20
SELF_COLLISION_DAMAGE = 20
HEAD_TO_HEAD_DAMAGE = 25
MAX_MATCH_TICKS = 300

# Visual island layout — must mirror client/game_window.py metrics.
# The board's top (rows 0-6) is the castle-extension / serpent gap above the island.
# Rows 7-18 (cols 1-18) are the playable island surface.
ISLAND_ROW_MIN = 7   # ceil(SIDE_EXTENSION_ROWS * TILE_SIZE / GAME_CELL_H) = ceil(6.15)
ISLAND_COL_MIN = 1
ISLAND_COL_MAX = 18  # exclusive upper bound is 19 (BOARD_WIDTH - 1)
ISLAND_ROW_MAX = 18  # exclusive upper bound is 19 (BOARD_HEIGHT - 1)
# Centre-top gap (the serpent's domain above the island): block snake entry.
GAP_COL_START = 5
GAP_COL_END   = 15
GAP_ROW_END   = 7

# Static obstacle layout (fixed coordinates as requested).
STATIC_OBSTACLES: tuple[tuple[int, int], ...] = (
    (9, 7),
    (10, 7),
    (9, 12),
    (10, 12),
    (5, 10),
    (14, 10),
)

PIE_TYPES: tuple[dict[str, object], ...] = (
    {"kind": "green", "effect": "self_heal", "amount": DEFAULT_PIE_HEALTH_GAIN, "points": 30},
    {"kind": "yellow", "effect": "opponent_damage", "amount": DEFAULT_PIE_DAMAGE, "points": -40},
    {"kind": "red", "effect": "self_damage", "amount": DEFAULT_PIE_DAMAGE, "points": -40},
)
# Interval bounds (in real-time seconds) between throw announcements.
THROW_MIN_INTERVAL = 5
THROW_MAX_INTERVAL = 8

_DIRECTIONS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

_OPPOSITE_DIRECTION: dict[str, str] = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


@dataclass(slots=True)
class SnakeRuntime:
    """Authoritative snake runtime state for one player."""

    player: str
    body: list[tuple[int, int]]
    direction: str
    next_direction: str
    health: int = INITIAL_HEALTH
    alive: bool = True


@dataclass(slots=True)
class MatchRuntime:
    """Authoritative server-side runtime state for one active match."""

    game_id: str
    players: tuple[str, str]
    snakes: dict[str, SnakeRuntime]
    obstacles: dict[tuple[int, int], dict[str, object]]
    pies: dict[tuple[int, int], dict[str, object]]
    tick: int = 0
    max_ticks: int = MAX_MATCH_TICKS
    status: str = "running"
    winner: str | None = None
    end_reason: str | None = None
    width: int = BOARD_WIDTH
    height: int = BOARD_HEIGHT
    pie_counter: int = 1
    lockstep_moves: dict[str, bool] = field(default_factory=dict)
    countdown_ticks: int = 25  # ~3 s at 0.12 s/tick; frozen until this reaches 0
    # Countdown only ticks once both players have connected via game window.
    players_in_window: set[str] = field(default_factory=set)
    # Authoritative wall-clock start (set when countdown completes).
    started_at_monotonic: float | None = None
    # Tick at which to announce the next throw target.
    next_throw_tick: int = 3
    # Active throw target broadcast to clients so they can aim blobs here.
    # Format: {"x": int, "y": int, "kind": str, "id": int}
    throw_target: dict | None = field(default=None)


def create_match_runtime(game_id: str, player_a: str, player_b: str) -> MatchRuntime:
    """
    Build initial match state.

    Snakes start from opposite sides and move toward each other.
    """

    snake_a = SnakeRuntime(
        player=player_a,
        body=[(3, 9), (2, 9), (1, 9)],
        direction="right",
        next_direction="right",
    )
    snake_b = SnakeRuntime(
        player=player_b,
        body=[(16, 11), (17, 11), (18, 11)],
        direction="left",
        next_direction="left",
    )

    runtime = MatchRuntime(
        game_id=game_id,
        players=(player_a, player_b),
        snakes={player_a: snake_a, player_b: snake_b},
        obstacles={
            pos: {"kind": "wall", "damage": WALL_COLLISION_DAMAGE}
            for pos in STATIC_OBSTACLES
        },
        pies={},
        lockstep_moves={player_a: False, player_b: False},
    )
    return runtime


def queue_direction(runtime: MatchRuntime, player: str, direction: str) -> tuple[bool, str]:
    """Queue one player's direction change for the next simulation step."""

    snake = runtime.snakes.get(player)
    if snake is None:
        return False, "unknown_player"
    if not snake.alive:
        return False, "player_dead"
    if runtime.status != "running":
        return False, "match_finished"
    if direction not in _DIRECTIONS:
        return False, "invalid_direction"
    if _OPPOSITE_DIRECTION[snake.direction] == direction:
        return False, "reverse_not_allowed"

    snake.next_direction = direction
    runtime.lockstep_moves[player] = True
    return True, "queued"


def should_step(runtime: MatchRuntime) -> bool:
    """Return True when all alive players provided movement input."""

    for player, snake in runtime.snakes.items():
        if snake.alive and not runtime.lockstep_moves.get(player, False):
            return False
    return runtime.status == "running"


def _target_length_for_health(health: int) -> int:
    """
    Compute snake body length as a health-proportional value.

    Baseline behavior:
    - `INITIAL_HEALTH` -> `INITIAL_SNAKE_LENGTH`
    - below baseline health shrinks length
    - above baseline health grows length
    """

    MIN_SNAKE_LENGTH = 3
    if health <= 0:
        return MIN_SNAKE_LENGTH
    scaled_length = (INITIAL_SNAKE_LENGTH * float(health)) / float(INITIAL_HEALTH)
    return max(MIN_SNAKE_LENGTH, int(round(scaled_length)))


def _sync_snake_length_to_health(snake: SnakeRuntime) -> None:
    """Resize body (extend/shrink) so visual length stays proportional to health."""

    target_length = _target_length_for_health(snake.health)
    current_length = len(snake.body)
    if current_length > target_length:
        del snake.body[target_length:]
        return
    if current_length < target_length and snake.body:
        snake.body.extend([snake.body[-1]] * (target_length - current_length))


def step_runtime(runtime: MatchRuntime) -> None:
    """
    Advance one simulation tick.

    This function applies movement, health updates, collisions, pie handling,
    and winner/end-condition evaluation.
    """

    if runtime.status != "running":
        return

    # Apply queued directions before moving heads.
    for player, snake in runtime.snakes.items():
        if snake.alive:
            snake.direction = snake.next_direction
        runtime.lockstep_moves[player] = False

    planned_heads: dict[str, tuple[int, int]] = {}
    for player, snake in runtime.snakes.items():
        if not snake.alive:
            continue
        dx, dy = _DIRECTIONS[snake.direction]
        hx, hy = snake.body[0]
        planned_heads[player] = (hx + dx, hy + dy)

    # Body occupancy before movement for collision checks.
    all_body_cells: set[tuple[int, int]] = set()
    for snake in runtime.snakes.values():
        all_body_cells.update(snake.body)

    # Collect all collision penalties first so lockstep outcomes are fair.
    collision_penalties: dict[str, int] = {player: 0 for player in runtime.players}
    for player, snake in runtime.snakes.items():
        if not snake.alive:
            continue

        next_head = planned_heads[player]
        x, y = next_head
        out_of_board = x < 0 or y < 0 or x >= runtime.width or y >= runtime.height
        # Block the centre-top gap (serpent zone) — wings at rows 0-6 cols 0-4, 15-19 stay valid.
        in_gap = GAP_COL_START <= x < GAP_COL_END and y < GAP_ROW_END
        if out_of_board or in_gap:
            collision_penalties[player] += WALL_COLLISION_DAMAGE
        if next_head in runtime.obstacles:
            obstacle_damage = int(runtime.obstacles[next_head].get("damage", WALL_COLLISION_DAMAGE))
            collision_penalties[player] += obstacle_damage

        # Explicitly handle "head collides with another snake" damage.
        other_snake_cells: set[tuple[int, int]] = set()
        own_body_cells: set[tuple[int, int]] = set()
        for other_player, other_snake in runtime.snakes.items():
            if other_player == player:
                own_body_cells.update(other_snake.body)
            else:
                other_snake_cells.update(other_snake.body)

        if next_head in other_snake_cells:
            collision_penalties[player] += OTHER_SNAKE_COLLISION_DAMAGE
        # Keep self-collision behavior for standard snake rules.
        if next_head in own_body_cells:
            collision_penalties[player] += SELF_COLLISION_DAMAGE

    # Head-to-head: both snakes choose the same next cell this tick.
    next_heads_to_players: dict[tuple[int, int], list[str]] = {}
    for player, next_head in planned_heads.items():
        next_heads_to_players.setdefault(next_head, []).append(player)
    for players in next_heads_to_players.values():
        if len(players) > 1:
            for player in players:
                collision_penalties[player] += HEAD_TO_HEAD_DAMAGE

    # Apply penalties or movement.
    ate_pie = False
    for player, snake in runtime.snakes.items():
        if not snake.alive:
            continue

        penalty = collision_penalties.get(player, 0)
        if penalty > 0:
            snake.health = max(0, snake.health - penalty)
            snake.alive = snake.health > 0
            continue

        next_head = planned_heads[player]
        pie_info = runtime.pies.get(next_head)
        grows = pie_info is not None

        snake.body.insert(0, next_head)
        if not grows:
            snake.body.pop()
        else:
            ate_pie = True
            runtime.pies.pop(next_head, None)
            effect = str(pie_info.get("effect", "self_heal"))
            amount = int(pie_info.get("amount", DEFAULT_PIE_HEALTH_GAIN))
            if effect == "self_heal":
                snake.health += amount
            elif effect == "self_damage":
                snake.health = max(0, snake.health - amount)
            elif effect == "opponent_damage":
                opponent = next((name for name in runtime.players if name != player), None)
                if opponent is not None and opponent in runtime.snakes:
                    runtime.snakes[opponent].health = max(0, runtime.snakes[opponent].health - amount)

        snake.alive = snake.health > 0

    # Re-evaluate alive flags because opponent-damage pies can kill the other snake.
    for snake in runtime.snakes.values():
        snake.alive = snake.health > 0
        _sync_snake_length_to_health(snake)

    # Announce a new throw target periodically; clients aim their blobs here and
    # send spawn_pie when the blob lands — the server then places the pie.
    if runtime.tick >= runtime.next_throw_tick:
        cell_and_pie = _pick_random_spawn(runtime)
        if cell_and_pie is not None:
            (cx, cy), pie_template = cell_and_pie
            runtime.throw_target = {
                "x": cx, "y": cy,
                "kind": str(pie_template["kind"]),
                "id": runtime.pie_counter,
            }
        runtime.next_throw_tick = runtime.tick + random.randint(
            THROW_MIN_INTERVAL, THROW_MAX_INTERVAL
        )

    _update_match_status(runtime)


def to_protocol_state(runtime: MatchRuntime) -> dict:
    """Convert runtime match state into protocol `game_state` dictionary."""

    snakes = []
    for player in runtime.players:
        snake = runtime.snakes[player]
        body_positions = [Position(x=x, y=y) for x, y in snake.body]
        snakes.append(
            make_snake_state(
                player=snake.player,
                body=body_positions,
                direction=snake.direction,
                health=snake.health,
                alive=snake.alive,
            )
        )

    pies = [
        make_pie_state(
            pie_id=f"pie-{runtime.pie_counter}",
            position=make_position(x, y),
            points=int(metadata.get("points", 1)),
        )
        for (x, y), metadata in sorted(runtime.pies.items())
    ]

    obstacles = [
        make_obstacle_state(
            obstacle_id=f"obstacle-{idx}",
            position=make_position(x, y),
            kind=str(metadata.get("kind", "wall")),
        )
        for idx, ((x, y), metadata) in enumerate(sorted(runtime.obstacles.items()), start=1)
    ]

    # Sprint 5 timing UX:
    # Expose elapsed time starting from 1 as soon as a match is running so
    # clients do not appear "stuck at 0" right after match start.
    elapsed_for_payload = runtime.tick
    if runtime.status == "running":
        elapsed_for_payload = max(1, runtime.tick)

    timer = make_timer_state(
        total_seconds=runtime.max_ticks,
        remaining_seconds=max(0, runtime.max_ticks - elapsed_for_payload),
        elapsed_seconds=elapsed_for_payload,
    )

    state: GameState = make_game_state(
        game_id=runtime.game_id,
        snakes=snakes,
        pies=pies,
        obstacles=obstacles,
        timer=timer,
        winner=runtime.winner,
        status=runtime.status,
    )
    state_dict = state_to_dict(state)
    # Include pie metadata so clients can render green/yellow/red variants.
    state_dict["pies"] = [
        {
            "pie_id": f"pie-{runtime.pie_counter}-{idx}",
            "position": {"x": x, "y": y},
            "points": int(metadata.get("points", 1)),
            "kind": str(metadata.get("kind", "green")),
            "effect": str(metadata.get("effect", "self_heal")),
            "amount": int(metadata.get("amount", DEFAULT_PIE_HEALTH_GAIN)),
        }
        for idx, ((x, y), metadata) in enumerate(sorted(runtime.pies.items()), start=1)
    ]
    # Sprint 5 compatibility helper:
    # Provide numeric state for clients that expect 1=running, 0=not-running.
    state_dict["status_code"] = 1 if runtime.status == "running" else 0
    state_dict["countdown"] = runtime.countdown_ticks
    # Broadcast active throw target so clients can aim blobs and send spawn_pie.
    if runtime.throw_target is not None:
        state_dict["throw_target"] = runtime.throw_target
    return state_dict


def _pick_random_spawn(
    runtime: MatchRuntime,
) -> tuple[tuple[int, int], dict] | None:
    """Return a (cell, pie_template) for the next throw, or None if the board is full."""

    occupied: set[tuple[int, int]] = set(runtime.obstacles)
    for snake in runtime.snakes.values():
        occupied.update(snake.body)
    occupied.update(runtime.pies)

    valid = [
        (x, y)
        for y in range(ISLAND_ROW_MIN, ISLAND_ROW_MAX + 1)
        for x in range(ISLAND_COL_MIN, ISLAND_COL_MAX + 1)
        if (x, y) not in occupied
    ]
    if not valid:
        return None

    return random.choice(valid), dict(random.choice(PIE_TYPES))


def _update_match_status(runtime: MatchRuntime) -> None:
    """Evaluate end conditions and compute winner when match is over."""

    zero_health_players = [p for p in runtime.players if runtime.snakes[p].health <= 0]
    if zero_health_players:
        runtime.status = "finished"
        runtime.winner = _winner_by_health(runtime)
        runtime.end_reason = "health_depleted"
        return

    if runtime.tick >= runtime.max_ticks:
        runtime.status = "finished"
        runtime.winner = _winner_by_health(runtime)
        runtime.end_reason = "timer_expired"
        return


def _winner_by_health(runtime: MatchRuntime) -> str | None:
    """Break ties by health; returns None for a draw."""

    a, b = runtime.players
    health_a = runtime.snakes[a].health
    health_b = runtime.snakes[b].health
    if health_a > health_b:
        return a
    if health_b > health_a:
        return b
    return None
