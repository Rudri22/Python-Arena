"""
Simple invite-driven bot client for Python-Arena.

Purpose:
- Connect as a normal user (for example: bot1)
- Join the quick-match queue (same as the Pygame lobby)
- Auto-accept incoming direct invitations as fallback
- Send movement commands during active matches, avoiding walls and body
"""

from __future__ import annotations

import argparse
import random
import time

from client.network import ClientConnection
from shared.protocol import (
    MessageType,
    make_invitation_message,
    make_movement_message,
    make_username_message,
)


DEFAULT_SERVER_IP   = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000
DEFAULT_USERNAME    = "bot1"
DEFAULT_MOVE_INTERVAL_MS = 140

BOARD_COLS = 20
BOARD_ROWS = 20

VALID_DIRECTIONS = ("up", "down", "left", "right")
DIRECTION_DELTA: dict[str, tuple[int, int]] = {
    "up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python-Arena bot")
    parser.add_argument("--server-ip",   default=DEFAULT_SERVER_IP)
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT)
    parser.add_argument("--username",    default=DEFAULT_USERNAME)
    parser.add_argument("--move-interval-ms", type=int, default=DEFAULT_MOVE_INTERVAL_MS)
    return parser.parse_args()


def _occupied_cells(state: dict) -> set[tuple[int, int]]:
    """Return all grid cells that are dangerous to enter."""
    occupied: set[tuple[int, int]] = set()
    for snake in state.get("snakes", []):
        body = snake.get("body", [])
        for i, seg in enumerate(body):
            # Exclude the very last tail segment (it will have moved by the time we arrive)
            if i == len(body) - 1:
                continue
            occupied.add((int(seg.get("x", 0)), int(seg.get("y", 0))))
    for obs in state.get("obstacles", []):
        pos = obs.get("position", {})
        occupied.add((int(pos.get("x", 0)), int(pos.get("y", 0))))
    return occupied


def choose_direction(
    state: dict | None,
    username: str,
    current_dir: str,
) -> str:
    """Pick the best safe direction, avoiding walls and bodies."""
    if state is None:
        # No state yet — pure random (no reverse)
        options = [d for d in VALID_DIRECTIONS if d != OPPOSITE.get(current_dir, "")]
        return random.choice(options) if options else current_dir

    snakes = state.get("snakes", [])
    my_snake = next(
        (s for s in snakes if s.get("player", "").casefold() == username.casefold()),
        None,
    )
    if my_snake is None or not my_snake.get("body"):
        options = [d for d in VALID_DIRECTIONS if d != OPPOSITE.get(current_dir, "")]
        return random.choice(options) if options else current_dir

    head = my_snake["body"][0]
    hx, hy = int(head.get("x", 0)), int(head.get("y", 0))
    occupied = _occupied_cells(state)

    # Check pies (food) to bias movement
    pies: set[tuple[int, int]] = set()
    for p in state.get("pies", []):
        pos = p.get("position", {})
        pies.add((int(pos.get("x", 0)), int(pos.get("y", 0))))

    def is_safe(d: str) -> bool:
        dx, dy = DIRECTION_DELTA[d]
        nx, ny = hx + dx, hy + dy
        return 0 <= nx < BOARD_COLS and 0 <= ny < BOARD_ROWS and (nx, ny) not in occupied

    # Prefer: not reversing, safe, toward food
    candidates = [d for d in VALID_DIRECTIONS if d != OPPOSITE.get(current_dir, "") and is_safe(d)]

    if not candidates:
        # All forward directions are blocked — allow reversing as last resort
        candidates = [d for d in VALID_DIRECTIONS if is_safe(d)]

    if not candidates:
        return current_dir  # truly cornered

    # Among safe candidates, prefer one that moves toward nearest pie
    if pies:
        def pie_score(d: str) -> float:
            dx, dy = DIRECTION_DELTA[d]
            nx, ny = hx + dx, hy + dy
            return -min(abs(nx - px) + abs(ny - py) for px, py in pies)
        candidates.sort(key=pie_score, reverse=True)
        # 70% chance to follow food, 30% random among safe
        if random.random() < 0.70:
            return candidates[0]

    # Prefer continuing current direction if safe (snake looks smoother)
    if current_dir in candidates:
        return current_dir

    return random.choice(candidates)


def _send_quick_match(connection: ClientConnection, username: str) -> None:
    connection.send_message(
        make_invitation_message(from_user=username, to_user="", action="quick_match")
    )


def run_bot(server_ip: str, server_port: int, username: str, move_interval_ms: int) -> None:
    connection = ClientConnection(server_ip=server_ip, server_port=server_port)
    active_game_id: str | None = None
    last_direction  = "right"
    last_move_ms    = 0
    last_state: dict | None = None

    try:
        # Consume connect ACK then register username
        _ = connection.receive_message()
        connection.send_message(make_username_message(username))
        print(f"[BOT] Connected as '{username}' → {server_ip}:{server_port}")

        # Join quick-match queue immediately (same flow as the Pygame lobby)
        _send_quick_match(connection, username)
        print("[BOT] Joined quick-match queue")

        while True:
            message  = connection.receive_message()
            msg_type = str(message.get("type", ""))
            payload  = message.get("payload", {})

            # ── Error ────────────────────────────────────────────────────────
            if msg_type == MessageType.ERROR.value:
                print(f"[BOT][ERROR] {payload.get('message', 'unknown error')}")
                continue

            # ── Invitation / match flow ───────────────────────────────────────
            if msg_type == MessageType.INVITATION.value:
                action    = str(payload.get("action", "")).lower()
                from_user = str(payload.get("from_user", ""))
                to_user   = str(payload.get("to_user", ""))

                if action == "waiting":
                    print("[BOT] Waiting in quick-match queue…")
                    continue

                if action == "quick_cancelled":
                    continue

                if action == "send" and to_user.casefold() == username.casefold():
                    # Direct invitation — accept immediately (fallback path)
                    connection.send_message(
                        make_invitation_message(from_user=from_user, to_user=username, action="accept")
                    )
                    print(f"[BOT] Accepted direct invite from {from_user}")
                    continue

                if action == "match_started":
                    active_game_id = str(payload.get("game_id", "")) or active_game_id
                    last_state     = None
                    print(f"[BOT] Match started: {active_game_id}")
                    continue

                continue  # ignore declined/cancelled/spectate notices

            # ── Game state → send movement ────────────────────────────────────
            if msg_type == MessageType.GAME_STATE.value:
                active_game_id = str(payload.get("game_id", "")) or active_game_id
                last_state     = payload.get("state", {})

                now_ms = int(time.time() * 1000)
                if active_game_id is not None and now_ms - last_move_ms >= move_interval_ms:
                    last_direction = choose_direction(last_state, username, last_direction)
                    connection.send_message(
                        make_movement_message(player=username, direction=last_direction)
                    )
                    last_move_ms = now_ms
                continue

            # ── Game over → re-queue ──────────────────────────────────────────
            if msg_type == MessageType.GAME_OVER.value:
                winner = payload.get("winner", "unknown")
                print(f"[BOT] Game over. Winner={winner}")
                active_game_id = None
                last_state     = None

                # Re-join the quick-match queue for the next game
                _send_quick_match(connection, username)
                print("[BOT] Re-joined quick-match queue")
                continue

            # ONLINE_USERS and CHAT messages are silently ignored

    finally:
        connection.close()


def main() -> None:
    args = parse_args()
    run_bot(
        server_ip=args.server_ip,
        server_port=args.server_port,
        username=args.username,
        move_interval_ms=max(50, int(args.move_interval_ms)),
    )


if __name__ == "__main__":
    main()
