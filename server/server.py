"""
PBI 1.4 + PBI 1.5 + PBI 1.6 - Backend server startup and communication loop.

PBI 1.4: Start server with a configurable port argument.
PBI 1.5: Accept incoming client connections.
PBI 1.6: Route accepted sockets to a handler that does basic send/receive.
"""

from __future__ import annotations

import argparse
import socket
import threading
from contextlib import closing

from server.client_handler import handle_client_connection
from server.state import ServerState


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5000
BACKLOG = 20


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for server startup."""

    parser = argparse.ArgumentParser(description="Python-Arena backend server")

    # PBI 1.4 requirement: support a port argument at startup.
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind the server on (default: {DEFAULT_PORT})",
    )

    # Host is optional but useful when testing locally or on LAN.
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host/IP to bind (default: {DEFAULT_HOST})",
    )

    return parser.parse_args()


def validate_port(port: int) -> None:
    """Fail early if the provided port is outside the valid TCP range."""

    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")


def create_server_socket(host: str, port: int) -> socket.socket:
    """Create, bind, and listen on the server socket."""

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Allows quick restart of the server without waiting for TIME_WAIT cleanup.
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_socket.bind((host, port))
    server_socket.listen(BACKLOG)
    return server_socket


def run_server(host: str, port: int) -> None:
    """Start the accept loop and keep the backend process alive."""

    validate_port(port)

    # Shared state object passed to each client thread.
    server_state = ServerState()

    with closing(create_server_socket(host, port)) as server_socket:
        print(f"[SERVER] Listening on {host}:{port}")

        try:
            while True:
                client_socket, client_address = server_socket.accept()
                print(f"[SERVER] Connection accepted from {client_address}")

                # PBI 1.5 + PBI 1.6: each client runs in its own handler thread
                # so communication can happen concurrently.
                client_thread = threading.Thread(
                    target=handle_client_connection,
                    args=(client_socket, client_address, server_state),
                    daemon=True,
                )
                client_thread.start()
        except KeyboardInterrupt:
            # Graceful shutdown path when you stop the process with Ctrl+C.
            print("\n[SERVER] Shutdown requested. Stopping server...")


def main() -> None:
    """CLI entry point for running the backend server."""

    args = parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
