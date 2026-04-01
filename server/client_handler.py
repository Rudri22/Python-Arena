"""
PBI 1.6 + PBI 2.10 + PBI 2.11 - Client communication handler.

This module handles basic receive/parse/send communication for one client.
"""

from __future__ import annotations

import socket

from server.state import ServerState
from shared.protocol import (
    MessageType,
    ProtocolError,
    make_chat_message,
    make_connect_message,
    make_error_message,
    make_invitation_message,
    make_online_users_message,
)
from shared.utils import encode_message_for_socket, split_socket_buffer


def send_message(client_socket: socket.socket, message: dict) -> None:
    """Send one protocol message as newline-delimited JSON bytes."""

    client_socket.sendall(encode_message_for_socket(message))


def broadcast_message(server_state: ServerState, message: dict) -> None:
    """
    Send one message to all currently connected clients.

    This powers PBI 2.10 online players updates.
    """

    for target_socket in server_state.get_client_sockets_snapshot():
        try:
            send_message(target_socket, message)
        except OSError:
            # If one socket is already closed, skip it and keep broadcasting.
            continue


def handle_incoming_message(
    client_socket: socket.socket,
    message: dict,
    client_id: str,
    server_state: ServerState,
) -> None:
    """Process one incoming message and send a simple server response."""

    message_type = message["type"]
    payload = message["payload"]

    if message_type == MessageType.USERNAME.value:
        # PBI 2.10: track username and broadcast online players list.
        online_users = server_state.set_username(client_id, payload["username"])
        broadcast_message(server_state, make_online_users_message(online_users))
        return

    if message_type == MessageType.INVITATION.value:
        # PBI 2.11: let one player select another player and send invitation.
        sender_username = server_state.get_username_for_client(client_id)
        target_username = payload["to_user"]
        action = payload["action"]

        if sender_username is None:
            send_message(client_socket, make_error_message("Set username before sending invitations."))
            return

        if action != "send":
            send_message(client_socket, make_error_message("Only invitation action 'send' is supported now."))
            return

        if target_username == sender_username:
            send_message(client_socket, make_error_message("You cannot invite yourself."))
            return

        target_socket = server_state.get_socket_by_username(target_username)
        if target_socket is None:
            send_message(
                client_socket,
                make_error_message(f"Player '{target_username}' is not online."),
            )
            return

        # Forward invitation to selected target player.
        invite_message = make_invitation_message(
            from_user=sender_username,
            to_user=target_username,
            action="send",
        )
        send_message(target_socket, invite_message)

        # Confirm to sender that invitation was delivered.
        send_message(
            client_socket,
            make_chat_message(
                sender="SERVER",
                message=f"Invitation sent to {target_username}.",
            ),
        )
        return

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

    client_id = server_state.register_client(client_socket)
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
                    handle_incoming_message(client_socket, message, client_id, server_state)
                except ValueError as error:
                    # Handles server-state validation errors such as duplicate username.
                    send_message(client_socket, make_error_message("Invalid request.", str(error)))
                except ProtocolError as error:
                    send_message(client_socket, make_error_message("Invalid message payload.", str(error)))

    # On disconnect, remove the player and broadcast updated online list.
    online_users = server_state.unregister_client(client_id)
    broadcast_message(server_state, make_online_users_message(online_users))
    print(f"[CLIENT] Active clients: {server_state.connected_clients}")
