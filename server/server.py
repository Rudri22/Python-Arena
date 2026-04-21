"""
PBI 1.4 + PBI 1.5 + PBI 1.6 - Backend server startup and communication loop.

PBI 1.4: Start server with a configurable port argument.
PBI 1.5: Accept incoming client connections.
PBI 1.6: Route accepted sockets to a handler that does basic send/receive.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import socket
import threading
import time
from contextlib import closing

from server.client_handler import broadcast_online_users, handle_client_connection
from server.state import ServerState
from shared.protocol import make_game_over_message, make_game_state_message
from shared.utils import encode_message_for_socket


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 5000
BACKLOG = 20
GAME_TICK_SECONDS = 0.12


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

    # Sprint 5 PBI 5.1: continuous authoritative game loop thread.
    game_loop_thread = threading.Thread(
        target=_run_game_loop,
        args=(server_state,),
        daemon=True,
    )
    game_loop_thread.start()

    with closing(create_server_socket(host, port)) as server_socket:
        print(f"[SERVER] Listening on {host}:{port}")

        try:
            while True:
                client_socket, client_address = server_socket.accept()
                try:
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
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


def _send_socket_message(sock: socket.socket, message: dict) -> None:
    """Server-level helper for sending protocol messages safely."""

    sock.sendall(encode_message_for_socket(message))


def _send_socket_bytes(sock: socket.socket, payload: bytes) -> None:
    """Server-level helper for sending already-encoded payload bytes."""

    sock.sendall(payload)


def _run_game_loop(server_state: ServerState) -> None:
    """
    Sprint 5 PBI 5.1 + 5.6 continuous match loop.

    The loop advances active matches on a fixed tick and broadcasts:
    - GAME_STATE updates to active players and spectators
    - GAME_OVER when a match finishes (players + spectators)
    """

    send_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=24,
        thread_name_prefix="arena-send",
    )
    # One in-flight GAME_STATE send per socket. If a client is slow, we drop
    # intermediate snapshots for that client instead of stalling the loop.
    state_send_futures_by_fileno: dict[int, concurrent.futures.Future[None]] = {}
    try:
        while True:
            updates = server_state.step_active_matches()
            for update in updates:
                game_id = str(update["game_id"])
                state = dict(update["state"])
                game_state_message = make_game_state_message(game_id=game_id, state=state)
                game_state_payload = encode_message_for_socket(game_state_message)
                # Sprint 5/6 reliability note:
                # Use recipients captured in this tick update so final broadcasts
                # still work even after session cleanup removed match mapping.
                # Recipients include active players and spectators.
                recipients = tuple(update.get("recipients", update.get("players", ())))
                sockets: list[socket.socket] = []
                for username in recipients:
                    sock = server_state.get_socket_for_username(str(username))
                    if sock is not None:
                        sockets.append(sock)

                for sock in sockets:
                    fileno = sock.fileno()
                    if fileno < 0:
                        continue
                    prev = state_send_futures_by_fileno.get(fileno)
                    if prev is not None and not prev.done():
                        continue
                    state_send_futures_by_fileno[fileno] = send_executor.submit(
                        _send_socket_bytes,
                        sock,
                        game_state_payload,
                    )

                game_over_payload = update.get("game_over")
                if game_over_payload is not None:
                    game_over_message = make_game_over_message(
                        game_id=game_id,
                        winner=str(game_over_payload.get("winner", "draw")),
                        final_scores=game_over_payload.get("final_scores"),
                        reason=str(game_over_payload.get("reason", "match_finished")),
                    )
                    game_over_encoded = encode_message_for_socket(game_over_message)
                    for sock in sockets:
                        send_executor.submit(_send_socket_bytes, sock, game_over_encoded)
                    # Refresh lobby metadata (idle/active split + win totals) after
                    # match teardown so leaderboard reflects new winner score.
                    broadcast_online_users(server_state)

            # Trim completed futures to keep the map small over long uptime.
            done_fds = [fd for fd, fut in state_send_futures_by_fileno.items() if fut.done()]
            for fd in done_fds:
                state_send_futures_by_fileno.pop(fd, None)

            time.sleep(GAME_TICK_SECONDS)
    finally:
        send_executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    """CLI entry point for running the backend server."""

    args = parse_args()
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
