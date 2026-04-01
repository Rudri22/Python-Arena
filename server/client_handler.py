"""
PBI 1.6 - Client communication handler.

This module handles basic receive/parse/send communication for one client.
"""

from __future__ import annotations

import socket

from server.state import ServerState
from shared.protocol import MessageType, ProtocolError, make_chat_message, make_connect_message, make_error_message
from shared.utils import encode_message_for_socket, split_socket_buffer


def send_message(client_socket: socket.socket, message: dict) -> None:
    """Send one protocol message as newline-delimited JSON bytes."""

    client_socket.sendall(encode_message_for_socket(message))


def handle_incoming_message(client_socket: socket.socket, message: dict) -> None:
    """Process one incoming message and send a simple server response."""

    message_type = message["type"]
    payload = message["payload"]

    if message_type == MessageType.CHAT.value:
        # For chat messages we demonstrate full round-trip communication.
        response = make_chat_message(sender="SERVER", message=f"Echo: {payload['message']}")
        send_message(client_socket, response)
        return

    # For other types we acknowledge receipt to confirm basic send/receive.
    ack = make_chat_message(
        sender="SERVER",
        message=f"Received message type '{message_type}'.",
    )
    send_message(client_socket, ack)


def handle_client_connection(
    client_socket: socket.socket,
    client_address: tuple[str, int],
    server_state: ServerState,
) -> None:
    """
    Keep one client connection open until the client disconnects.

    PBI 1.6 scope:
    - receive raw bytes
    - parse framed protocol messages
    - send back basic responses
    """

    client_id = server_state.register_client()
    print(f"[CLIENT] Handler started for {client_address} as {client_id}")

    # Text buffer keeps partial lines between recv calls.
    text_buffer = ""

    # `with` guarantees the socket closes even if an exception occurs.
    with client_socket:
        # Send a simple connect acknowledgement when handler starts.
        send_message(client_socket, make_connect_message(client_id=client_id, client_version="server-1.0"))

        while True:
            data = client_socket.recv(4096)
            if not data:
                print(f"[CLIENT] Disconnected: {client_address} ({client_id})")
                break

            # Decode incoming bytes and append to framing buffer.
            text_buffer += data.decode("utf-8", errors="replace")

            try:
                messages, text_buffer = split_socket_buffer(text_buffer)
            except ProtocolError as error:
                # If a malformed message arrives, inform client and reset buffer
                # so the handler can continue processing future messages.
                send_message(client_socket, make_error_message("Malformed message.", str(error)))
                text_buffer = ""
                continue

            for message in messages:
                try:
                    handle_incoming_message(client_socket, message)
                except ProtocolError as error:
                    send_message(client_socket, make_error_message("Invalid message payload.", str(error)))

    server_state.unregister_client()
    print(f"[CLIENT] Active clients: {server_state.connected_clients}")
