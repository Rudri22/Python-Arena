"""
PBI 1.7 + PBI 1.8 + PBI 2.8 + PBI 2.9 + PBI 2.10 + PBI 2.11 + PBI 2.12 - Frontend client entry point.

PBI 1.7: Provide the client startup entry point.
PBI 1.8: Connect client to server using explicit IP and port.
PBI 2.8: Create username input screen.
PBI 2.9: Display username validation result.
PBI 2.10: Display online players list.
PBI 2.11: Let user select another player to play against.
PBI 2.12: Display waiting screen if no opponent is available.
"""

from __future__ import annotations

import argparse
import ipaddress

from client.network import ClientConnection
from client.ui import (
    prompt_username,
    prompt_opponent_selection,
    show_invitation_notice,
    show_invitation_update,
    show_online_players,
    show_incoming,
    show_system,
    show_waiting_for_opponent_screen,
    show_username_validation_result,
    validate_username,
)
from shared.protocol import (
    MessageType,
    make_chat_message,
    make_invitation_message,
    make_username_message,
)


DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000
DEFAULT_USERNAME = "Player"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for starting the client app."""

    parser = argparse.ArgumentParser(description="Python-Arena client")

    # PBI 1.8: expose explicit server IP/port arguments.
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

    # Backward-compatible aliases from earlier PBI naming.
    parser.add_argument("--host", dest="server_ip", help=argparse.SUPPRESS)
    parser.add_argument("--port", dest="server_port", type=int, help=argparse.SUPPRESS)

    parser.add_argument(
        "--username",
        default=None,
        help="Display username sent to server (if omitted, prompt screen is shown)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch Tkinter GUI client instead of terminal mode.",
    )
    parser.add_argument(
        "--pygame",
        action="store_true",
        help="Launch Sprint 4 Pygame gameplay window.",
    )

    return parser.parse_args()


def validate_server_address(server_ip: str, server_port: int) -> None:
    """
    Validate IP and port before trying to open the socket.

    This gives immediate, friendly errors for bad CLI input.
    """

    # Require a literal IP for this PBI.
    ipaddress.ip_address(server_ip)

    if not 1 <= server_port <= 65535:
        raise ValueError("Server port must be between 1 and 65535.")


def run_client(server_ip: str, server_port: int, username: str) -> None:
    """
    Connect to backend and run a basic interactive chat-style loop.

    Scope for these PBIs:
    - Start from one entry point
    - Connect to server by IP and port
    - Perform basic send/receive over shared protocol
    """

    validate_server_address(server_ip, server_port)

    connection = ClientConnection(server_ip=server_ip, server_port=server_port)
    show_system(f"Connected to server at {server_ip}:{server_port}")
    online_users: list[str] = []

    try:
        # First server message should be connect acknowledgement.
        server_connect = connection.receive_message()
        show_incoming(server_connect)

        # Username handshake loop:
        # If username is duplicate/taken, server returns error and we force
        # user to choose another name before entering lobby/chat.
        while True:
            connection.send_message(make_username_message(username))
            username_response = connection.receive_message()

            if username_response["type"] == MessageType.ERROR.value:
                show_incoming(username_response)
                show_system("Please choose another username.")
                username = prompt_username(default_username=DEFAULT_USERNAME)
                continue

            if username_response["type"] == MessageType.ONLINE_USERS.value:
                online_users = username_response["payload"]["users"]
                show_online_players(online_users)
                if all(user == username for user in online_users):
                    # PBI 2.12: show waiting state when you're currently alone.
                    show_waiting_for_opponent_screen(current_username=username)
                break

            # Keep compatibility with alternative backend variants that send an
            # explicit username-accepted message before online users.
            show_incoming(username_response)

        show_system("Type a chat message and press Enter.")
        show_system("Commands: /players, /play, /quit")

        while True:
            text = input("You> ").strip()

            if not text:
                # Ignore empty input to keep the protocol stream clean.
                continue

            if text.lower() == "/quit":
                show_system("Exiting client...")
                break

            if text.lower() == "/players":
                # PBI 2.10 helper command: refresh local view of online users.
                show_online_players(online_users)
                continue

            if text.lower() == "/play":
                # PBI 2.11: let user select another online player as opponent.
                if all(user == username for user in online_users):
                    # PBI 2.12: explicit waiting screen when no opponents exist.
                    show_waiting_for_opponent_screen(current_username=username)
                    continue

                opponent = prompt_opponent_selection(online_users, current_username=username)
                if opponent is None:
                    continue

                connection.send_message(
                    make_invitation_message(
                        from_user=username,
                        to_user=opponent,
                        action="send",
                    )
                )
                response = connection.receive_message()
            else:
                # Send user text as protocol chat message.
                connection.send_message(make_chat_message(sender=username, message=text))
                response = connection.receive_message()

            # Wait for one server response and print it.
            if response["type"] == MessageType.ONLINE_USERS.value:
                online_users = response["payload"]["users"]
                show_online_players(online_users)
            elif response["type"] == MessageType.INVITATION.value:
                invitation_payload = response["payload"]
                invitation_action = invitation_payload.get("action", "")

                # Show specific invitation lifecycle updates so sender can
                # clearly see whether invite was accepted or declined.
                if invitation_action == "send":
                    show_invitation_notice(invitation_payload["from_user"])
                else:
                    show_invitation_update(
                        from_user=invitation_payload.get("from_user", "unknown"),
                        to_user=invitation_payload.get("to_user", "unknown"),
                        action=invitation_action,
                        game_id=invitation_payload.get("game_id"),
                    )
            else:
                show_incoming(response)

    finally:
        # Ensure socket closes cleanly even if an error occurs.
        connection.close()


def main() -> None:
    """CLI entry point function for the client app."""

    args = parse_args()

    if args.gui and args.pygame:
        show_system("Choose only one UI mode: --gui or --pygame.")
        raise SystemExit(1)

    if args.pygame:
        # Import lazily so terminal mode does not require pygame at import-time.
        from client.game_window import main as pygame_main

        pygame_username = args.username or DEFAULT_USERNAME
        is_valid, result_message = validate_username(pygame_username)
        show_username_validation_result(is_valid, result_message)
        if not is_valid:
            raise SystemExit(1)

        try:
            validate_server_address(args.server_ip, args.server_port)
        except ValueError as error:
            show_system(f"Invalid client arguments: {error}")
            raise SystemExit(1) from error

        pygame_main(
            server_ip=args.server_ip,
            server_port=args.server_port,
            username=pygame_username,
        )
        return

    if args.gui:
        # Import lazily so terminal mode does not require GUI modules at import-time.
        from client.gui import main as gui_main

        gui_main()
        return

    if args.username:
        # PBI 2.9: display validation result even when username comes from CLI.
        is_valid, result_message = validate_username(args.username)
        show_username_validation_result(is_valid, result_message)
        if not is_valid:
            raise SystemExit(1)
        username = args.username
    else:
        username = prompt_username(default_username=DEFAULT_USERNAME)

    try:
        run_client(
            server_ip=args.server_ip,
            server_port=args.server_port,
            username=username,
        )
    except ValueError as error:
        show_system(f"Invalid client arguments: {error}")
        raise SystemExit(1) from error
    except (ConnectionError, TimeoutError, OSError) as error:
        # Network failures are shown as clean user-facing messages.
        show_system(f"Connection failed: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
