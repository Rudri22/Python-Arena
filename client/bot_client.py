"""
Simple invite-driven bot client for Python-Arena.

Purpose:
- Connect as a normal user (for example: bot1)
- Auto-accept incoming game invitations
- Send movement commands periodically during active matches
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


DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000
DEFAULT_USERNAME = "bot1"
DEFAULT_MOVE_INTERVAL_MS = 140

VALID_DIRECTIONS = ("up", "down", "left", "right")
OPPOSITE = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


def parse_args() -> argparse.Namespace:
    """Parse CLI args for bot startup."""

    parser = argparse.ArgumentParser(description="Python-Arena invite bot")
    parser.add_argument("--server-ip", default=DEFAULT_SERVER_IP, help="Server IP")
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT, help="Server port")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Bot username")
    parser.add_argument(
        "--move-interval-ms",
        type=int,
        default=DEFAULT_MOVE_INTERVAL_MS,
        help="Movement send interval in milliseconds",
    )
    return parser.parse_args()


def choose_direction(previous: str) -> str:
    """Pick a random direction while avoiding immediate reverse."""

    options = [d for d in VALID_DIRECTIONS if d != OPPOSITE.get(previous, "")]
    if not options:
        return previous
    return random.choice(options)


def run_bot(server_ip: str, server_port: int, username: str, move_interval_ms: int) -> None:
    """Connect bot, auto-accept invites, and play active matches."""

    connection = ClientConnection(server_ip=server_ip, server_port=server_port)
    active_game_id: str | None = None
    last_direction = "right"
    last_move_ms = 0

    try:
        # Initial server connect ACK.
        _ = connection.receive_message()
        connection.send_message(make_username_message(username))
        print(f"[BOT] Connected as '{username}' to {server_ip}:{server_port}")

        while True:
            message = connection.receive_message()
            msg_type = str(message.get("type", ""))
            payload = message.get("payload", {})

            if msg_type == MessageType.ERROR.value:
                print(f"[BOT][ERROR] {payload.get('message', 'unknown error')}")
                continue

            if msg_type == MessageType.INVITATION.value:
                action = str(payload.get("action", "")).lower()
                from_user = str(payload.get("from_user", ""))
                to_user = str(payload.get("to_user", ""))

                if action == "send" and to_user.casefold() == username.casefold():
                    # Auto-accept invitation to start playing immediately.
                    connection.send_message(
                        make_invitation_message(
                            from_user=from_user,
                            to_user=username,
                            action="accept",
                        )
                    )
                    print(f"[BOT] Accepted invitation from {from_user}")
                    continue

                if action == "match_started":
                    active_game_id = str(payload.get("game_id", "")) or active_game_id
                    print(f"[BOT] Match started: {active_game_id}")
                    continue

            if msg_type == MessageType.GAME_STATE.value:
                active_game_id = str(payload.get("game_id", "")) or active_game_id

                now_ms = int(time.time() * 1000)
                if active_game_id is not None and now_ms - last_move_ms >= move_interval_ms:
                    last_direction = choose_direction(last_direction)
                    connection.send_message(
                        make_movement_message(player=username, direction=last_direction)
                    )
                    last_move_ms = now_ms
                continue

            if msg_type == MessageType.GAME_OVER.value:
                winner = payload.get("winner", "unknown")
                print(f"[BOT] Game over. Winner={winner}")
                active_game_id = None
                continue

    finally:
        connection.close()


def main() -> None:
    """Bot CLI entrypoint."""

    args = parse_args()
    run_bot(
        server_ip=args.server_ip,
        server_port=args.server_port,
        username=args.username,
        move_interval_ms=max(50, int(args.move_interval_ms)),
    )


if __name__ == "__main__":
    main()
