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
INITIAL_HEALTH = 100
HEALTH_DECAY_PER_TICK = 1
HEALTH_GAIN_PER_PIE = 15
MAX_MATCH_TICKS = 300

# Static obstacle layout (fixed coordinates as requested).
STATIC_OBSTACLES: tuple[tuple[int, int], ...] = (
    (9, 7),
    (10, 7),
    (9, 12),
    (10, 12),
    (5, 10),
    (14, 10),
)

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
    obstacles: set[tuple[int, int]]
    pies: set[tuple[int, int]]
    tick: int = 0
    max_ticks: int = MAX_MATCH_TICKS
    status: str = "running"
    winner: str | None = None
    end_reason: str | None = None
    width: int = BOARD_WIDTH
    height: int = BOARD_HEIGHT
    pie_counter: int = 1
    lockstep_moves: dict[str, bool] = field(default_factory=dict)


def create_match_runtime(game_id: str, player_a: str, player_b: str) -> MatchRuntime:
    """
    Build initial match state.

    Snakes start from opposite sides and move toward each other.
    """

    snake_a = SnakeRuntime(
        player=player_a,
        body=[(3, 10), (2, 10), (1, 10)],
        direction="right",
        next_direction="right",
    )
    snake_b = SnakeRuntime(
        player=player_b,
        body=[(16, 10), (17, 10), (18, 10)],
        direction="left",
        next_direction="left",
    )

    runtime = MatchRuntime(
        game_id=game_id,
        players=(player_a, player_b),
        snakes={player_a: snake_a, player_b: snake_b},
        obstacles=set(STATIC_OBSTACLES),
        pies=set(),
        lockstep_moves={player_a: False, player_b: False},
    )
    _spawn_next_pie(runtime)
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


def step_runtime(runtime: MatchRuntime) -> None:
    """
    Advance one simulation tick.

    This function applies movement, health updates, collisions, pie handling,
    and winner/end-condition evaluation.
    """

    if runtime.status != "running":
        return

    runtime.tick += 1

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

    # Apply movement + health + immediate collision checks.
    ate_pie = False
    for player, snake in runtime.snakes.items():
        if not snake.alive:
            continue

        next_head = planned_heads[player]
        snake.health -= HEALTH_DECAY_PER_TICK
        if snake.health <= 0:
            snake.health = 0
            snake.alive = False
            continue

        x, y = next_head
        if x < 0 or y < 0 or x >= runtime.width or y >= runtime.height:
            snake.alive = False
            continue
        if next_head in runtime.obstacles:
            snake.alive = False
            continue
        if next_head in all_body_cells:
            snake.alive = False
            continue

        grows = next_head in runtime.pies
        snake.body.insert(0, next_head)
        if not grows:
            snake.body.pop()
        else:
            ate_pie = True
            runtime.pies.discard(next_head)
            snake.health = min(INITIAL_HEALTH, snake.health + HEALTH_GAIN_PER_PIE)

    # Head-to-head collision after simultaneous move.
    alive_heads: dict[tuple[int, int], list[str]] = {}
    for player, snake in runtime.snakes.items():
        if snake.alive:
            alive_heads.setdefault(snake.body[0], []).append(player)
    for players in alive_heads.values():
        if len(players) > 1:
            for player in players:
                runtime.snakes[player].alive = False

    if ate_pie:
        _spawn_next_pie(runtime)

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
            points=1,
        )
        for x, y in sorted(runtime.pies)
    ]

    obstacles = [
        make_obstacle_state(
            obstacle_id=f"obstacle-{idx}",
            position=make_position(x, y),
            kind="wall",
        )
        for idx, (x, y) in enumerate(sorted(runtime.obstacles), start=1)
    ]

    timer = make_timer_state(
        total_seconds=runtime.max_ticks,
        remaining_seconds=max(0, runtime.max_ticks - runtime.tick),
        elapsed_seconds=runtime.tick,
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
    return state_to_dict(state)


def _spawn_next_pie(runtime: MatchRuntime) -> None:
    """Spawn one pie on the first free deterministic board cell."""

    occupied = set(runtime.obstacles)
    for snake in runtime.snakes.values():
        occupied.update(snake.body)

    for y in range(runtime.height):
        for x in range(runtime.width):
            if (x, y) not in occupied:
                runtime.pies = {(x, y)}
                runtime.pie_counter += 1
                return

    runtime.pies.clear()


def _update_match_status(runtime: MatchRuntime) -> None:
    """Evaluate end conditions and compute winner when match is over."""

    alive_players = [p for p in runtime.players if runtime.snakes[p].alive]

    if len(alive_players) == 1:
        runtime.status = "finished"
        runtime.winner = alive_players[0]
        runtime.end_reason = "elimination"
        return

    if len(alive_players) == 0:
        runtime.status = "finished"
        runtime.winner = _winner_by_health(runtime)
        runtime.end_reason = "mutual_elimination"
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
