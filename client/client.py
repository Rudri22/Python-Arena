"""Client launcher for the active Python-Arena pygame flow."""

from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path

# Support direct execution (`python client/client.py`) and module execution (`python -m client.client`).
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.network import ClientConnection
from shared.protocol import MessageType, make_username_message

DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000
DEFAULT_USERNAME = "Player"


# // Feature: Client Launcher
# // Purpose: Implements the 'show system' step of the client launcher system.
# // Trigger: Called by the client launcher flow when this helper is needed.
def show_system(message: str) -> None:
    print(f"[SYSTEM] {message}")


# // Feature: Client Launcher
# // Purpose: Implements the 'show username validation result' step of the client launcher system.
# // Trigger: Called by the client launcher flow when this helper is needed.
def show_username_validation_result(is_valid: bool, message: str) -> None:
    prefix = "[USERNAME][OK]" if is_valid else "[USERNAME][INVALID]"
    print(f"{prefix} {message}")


# // Feature: Client Launcher
# // Purpose: Implements the 'validate username' step of the client launcher system.
# // Trigger: Called before state changes to enforce game and input rules.
def validate_username(username: str) -> tuple[bool, str]:
    if not (2 <= len(username) <= 16):
        return False, "Username must be between 2 and 16 characters."
    if not all(ch.isalnum() or ch in {"_", "-"} for ch in username):
        return False, "Username can only contain letters, numbers, '_' or '-'."
    return True, f"Username '{username}' is valid."


# // Feature: Client Launcher
# // Purpose: Implements the 'parse args' step of the client launcher system.
# // Trigger: Called at application startup from the CLI entry flow.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python-Arena client")
    parser.add_argument(
        "--server-ip",
        dest="server_ip",
        default=DEFAULT_SERVER_IP,
        help=f"Server IPv4/IPv6 address (default: {DEFAULT_SERVER_IP})",
    )
    parser.add_argument(
        "--server-port",
        dest="server_port",
        type=int,
        default=DEFAULT_SERVER_PORT,
        help=f"Server TCP port (default: {DEFAULT_SERVER_PORT})",
    )
    parser.add_argument("--host", dest="server_ip", help=argparse.SUPPRESS)
    parser.add_argument("--port", dest="server_port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--username", default=None, help="Display username sent to server.")
    parser.add_argument("--prelobby", action="store_true", help="Launch pygame pre-lobby (default mode).")
    parser.add_argument("--pygame", action="store_true", help="Launch gameplay window directly.")
    parser.add_argument("--spectator", action="store_true", help="Launch gameplay as spectator.")
    return parser.parse_args()


# // Feature: Client Launcher
# // Purpose: Implements the 'validate server address' step of the client launcher system.
# // Trigger: Called before state changes to enforce game and input rules.
def validate_server_address(server_ip: str, server_port: int) -> None:
    ipaddress.ip_address(server_ip)
    if not 1 <= server_port <= 65535:
        raise ValueError("Server port must be between 1 and 65535.")


# // Feature: Client Launcher
# // Purpose: Implements the 'check username available' step of the client launcher system.
# // Trigger: Called by the client launcher flow when this helper is needed.
def check_username_available(server_ip: str, server_port: int, username: str) -> tuple[bool, str]:
    connection = ClientConnection(server_ip=server_ip, server_port=server_port)
    try:
        connection.receive_message()  # connect ack
        connection.send_message(
            make_username_message(
                username,
                chat_host=connection.chat_host,
                chat_port=connection.chat_port,
            )
        )
        response = connection.receive_message()
        if response.get("type") == MessageType.ERROR.value:
            message = str(response.get("payload", {}).get("message", "Username unavailable."))
            return False, message
        return True, "Username accepted."
    finally:
        connection.close()


# // Feature: Client Launcher
# // Purpose: Coordinates the top-level execution flow for this part of the game.
# // Trigger: Called at application startup from the CLI entry flow.
def main() -> None:
    args = parse_args()

    if args.spectator and args.prelobby:
        show_system("Choose one mode: --prelobby or --spectator.")
        raise SystemExit(1)
    if args.pygame and args.prelobby:
        show_system("Choose one mode: --prelobby or --pygame.")
        raise SystemExit(1)
    if args.spectator:
        args.pygame = True
    if not args.prelobby and not args.pygame:
        args.prelobby = True

    try:
        validate_server_address(args.server_ip, args.server_port)
    except ValueError as error:
        show_system(f"Invalid client arguments: {error}")
        raise SystemExit(1) from error

    if args.prelobby:
        from client.prelobby_pygame import run_prelobby_to_lobby

        # // Feature: Client Launcher
        # // Purpose: Implements the 'validate for prelobby' step of the client launcher system.
        # // Trigger: Called by the client launcher flow when this helper is needed.
        def _validate_for_prelobby(candidate: str) -> tuple[bool, str]:
            is_valid, result_message = validate_username(candidate)
            if not is_valid:
                return False, result_message
            try:
                is_available, _availability_message = check_username_available(
                    args.server_ip,
                    args.server_port,
                    candidate,
                )
            except (ConnectionError, TimeoutError, OSError):
                return False, "Server unavailable. Check connection and try again."
            if not is_available:
                return False, "Username taken. Please choose another one."
            return True, "Username accepted."

        run_prelobby_to_lobby(
            server_ip=args.server_ip,
            server_port=args.server_port,
            username_validator=_validate_for_prelobby,
        )
        return

    from client.game_window import main as pygame_main

    pygame_username = args.username or DEFAULT_USERNAME
    is_valid, result_message = validate_username(pygame_username)
    show_username_validation_result(is_valid, result_message)
    if not is_valid:
        raise SystemExit(1)

    pygame_main(
        server_ip=args.server_ip,
        server_port=args.server_port,
        username=pygame_username,
        spectator_mode=bool(args.spectator),
    )


if __name__ == "__main__":
    main()
