"""
PBI 1.7 + PBI 1.8 - Frontend client entry point.

PBI 1.7: Provide the client startup entry point.
PBI 1.8: Connect client to server using explicit IP and port.
"""

from __future__ import annotations

import argparse
import ipaddress

from client.network import ClientConnection
from client.ui import show_incoming, show_system
from shared.protocol import make_chat_message, make_username_message


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
        default=DEFAULT_USERNAME,
        help=f"Display username sent to server (default: {DEFAULT_USERNAME})",
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

    try:
        # First server message should be connect acknowledgement.
        server_connect = connection.receive_message()
        show_incoming(server_connect)

        # Send username so backend can map this socket to a player identity.
        connection.send_message(make_username_message(username))

        # Read server acknowledgement to confirm receive path is working.
        username_ack = connection.receive_message()
        show_incoming(username_ack)

        show_system("Type a chat message and press Enter. Type '/quit' to exit.")

        while True:
            text = input("You> ").strip()

            if not text:
                # Ignore empty input to keep the protocol stream clean.
                continue

            if text.lower() == "/quit":
                show_system("Exiting client...")
                break

            # Send user text as protocol chat message.
            connection.send_message(make_chat_message(sender=username, message=text))

            # Wait for one server response and print it.
            response = connection.receive_message()
            show_incoming(response)

    finally:
        # Ensure socket closes cleanly even if an error occurs.
        connection.close()


def main() -> None:
    """CLI entry point function for the client app."""

    args = parse_args()

    try:
        run_client(
            server_ip=args.server_ip,
            server_port=args.server_port,
            username=args.username,
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
