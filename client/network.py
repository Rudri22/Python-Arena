"""
PBI 1.7 + PBI 1.8 - Frontend/client network helpers.

This module centralizes socket communication so `client.py` stays focused
on application flow (parse args, connect, send messages, and read replies).
"""

from __future__ import annotations

import socket
from typing import Any

from shared.utils import encode_message_for_socket, split_socket_buffer


class ClientConnection:
    """
    Small wrapper around a TCP socket for protocol-based communication.

    We keep a text buffer so we can reconstruct full newline-delimited
    JSON messages even when `recv` returns partial chunks.
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        self.server_ip = server_ip
        self.server_port = server_port

        # PBI 1.8: connection is established from explicit IP + port.
        self.socket = socket.create_connection(
            (server_ip, server_port),
            timeout=connect_timeout_seconds,
        )

        # Restore blocking mode after connect so receive loop behaves normally.
        self.socket.settimeout(None)
        self._buffer = ""

    def send_message(self, message: dict[str, Any]) -> None:
        """Send one shared-protocol message to the server."""

        self.socket.sendall(encode_message_for_socket(message))

    def receive_message(self) -> dict[str, Any]:
        """
        Receive and return the next complete protocol message.

        This blocks until at least one full line-delimited JSON message is
        available from the socket stream.
        """

        while True:
            messages, remainder = split_socket_buffer(self._buffer)
            if messages:
                self._buffer = remainder
                return messages[0]

            data = self.socket.recv(4096)
            if not data:
                raise ConnectionError("Server closed the connection.")

            self._buffer += data.decode("utf-8", errors="replace")

    def close(self) -> None:
        """Close the underlying socket."""

        self.socket.close()
