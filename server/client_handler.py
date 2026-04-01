"""
PBI 1.6 + Sprint 2 lobby handling.

This module handles receive/parse/send communication for one client and now
supports Sprint 2 backend lobby behavior (online users, waiting, invitations,
and match start notifications).
"""

from __future__ import annotations

import socket

from server.state import ServerState
from shared.protocol import (
    MessageType,
    ProtocolError,
    build_message,
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


def broadcast_online_users(server_state: ServerState) -> None:
    """
    PBI 2.4: broadcast updated online users list to all connected clients.

    We include `waiting` metadata so clients can render waiting-state hints.
    """

    users = server_state.get_online_users()
    waiting = len(users) < 2
    message = make_online_users_message(users)
    message["payload"]["waiting"] = waiting

    for sock in server_state.get_client_sockets():
        try:
            send_message(sock, message)
        except OSError:
            # Ignore socket send failures here; disconnect cleanup path handles it.
            continue


def _send_waiting_status_if_needed(
    client_socket: socket.socket,
    server_state: ServerState,
) -> None:
    """PBI 2.5 helper: notify user when they are the only available player."""

    if server_state.has_waiting_player():
        waiting_message = build_message(
            MessageType.CHAT,
            sender="SERVER",
            message="Waiting for an opponent to join the lobby.",
            status="waiting",
        )
        send_message(client_socket, waiting_message)


def _handle_invitation_message(
    client_socket: socket.socket,
    payload: dict,
    server_state: ServerState,
    sender_client_id: str,
) -> None:
    """
    PBI 2.6 + 2.7 invitation dispatcher.

    Supported actions:
    - send
    - accept
    - decline
    - cancel
    """

    from_user = payload["from_user"].strip()
    to_user = payload["to_user"].strip()
    action = payload["action"].strip().lower()

    sender_username = server_state.get_client_username(sender_client_id)
    if sender_username is None:
        send_message(client_socket, make_error_message("Username must be set before invitations."))
        return

    if action == "send":
        # For "send", connected sender must match `from_user`.
        if from_user != sender_username:
            send_message(client_socket, make_error_message("Invitation sender mismatch."))
            return

        success, reason = server_state.create_invitation(from_user=from_user, to_user=to_user)
        if not success:
            reason_to_error = {
                "cannot_invite_self": "You cannot invite yourself.",
                "user_offline": "Selected player is offline.",
                "user_in_match": "Selected player is already in a match.",
                "user_busy": "Selected player is busy with another invite.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Invitation failed.")))
            return

        # Keep sender UX consistent with current frontend by using chat confirmation.
        send_message(
            client_socket,
            make_chat_message(sender="SERVER", message=f"Invitation sent to {to_user}."),
        )

        # Notify invitee using invitation protocol message.
        target_socket = server_state.get_socket_for_username(to_user)
        if target_socket is not None:
            send_message(
                target_socket,
                make_invitation_message(from_user=from_user, to_user=to_user, action="send"),
            )
        return

    if action in {"accept", "decline", "cancel"}:
        # For response actions, connected sender should be the invited user (`to_user`).
        if to_user != sender_username:
            send_message(client_socket, make_error_message("Invitation responder mismatch."))
            return

        success, reason, game_id = server_state.respond_to_invitation(
            from_user=from_user,
            to_user=to_user,
            action=action,
        )
        if not success:
            reason_to_error = {
                "invite_not_found": "No pending invitation found for this action.",
                "user_in_match": "One of the players is already in a match.",
                "invalid_action": "Unsupported invitation action.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Invitation action failed.")))
            return

        # Notify both involved players about decision.
        sender_socket = server_state.get_socket_for_username(from_user)
        receiver_socket = server_state.get_socket_for_username(to_user)
        decision_message = make_invitation_message(
            from_user=from_user,
            to_user=to_user,
            action=reason,
            game_id=game_id,
        )

        for sock in [sender_socket, receiver_socket]:
            if sock is None:
                continue
            try:
                send_message(sock, decision_message)
            except OSError:
                continue

        # PBI 2.7: match starts when invite is accepted.
        if reason == "accepted" and game_id is not None:
            start_message = make_invitation_message(
                from_user=from_user,
                to_user=to_user,
                action="match_started",
                game_id=game_id,
            )
            for sock in [sender_socket, receiver_socket]:
                if sock is None:
                    continue
                try:
                    send_message(sock, start_message)
                except OSError:
                    continue
        return

    send_message(client_socket, make_error_message("Unsupported invitation action."))


def handle_incoming_message(
    client_socket: socket.socket,
    message: dict,
    server_state: ServerState,
    client_id: str,
) -> None:
    """Process one incoming message and send server responses."""

    message_type = message["type"]
    payload = message["payload"]

    if message_type == MessageType.USERNAME.value:
        # PBI 2.1 + 2.2: receive username and enforce uniqueness.
        username = payload["username"].strip()
        if not username:
            send_message(client_socket, make_error_message("Username cannot be empty."))
            return

        accepted, reason = server_state.set_client_username(client_id, username)
        if not accepted:
            error_message = "Unknown client session."
            if reason == "username_taken":
                error_message = "Username already taken."
            send_message(client_socket, make_error_message(error_message))
            return

        # PBI 2.4 + 2.5: push lobby state updates after successful username set.
        broadcast_online_users(server_state)
        _send_waiting_status_if_needed(client_socket, server_state)
        return

    if message_type == MessageType.INVITATION.value:
        _handle_invitation_message(
            client_socket=client_socket,
            payload=payload,
            server_state=server_state,
            sender_client_id=client_id,
        )
        return

    if message_type == MessageType.CHAT.value:
        # Keep chat echo path for baseline communication checks.
        response = make_chat_message(sender="SERVER", message=f"Echo: {payload['message']}")
        send_message(client_socket, response)
        return

    # Default acknowledgement for unhandled message types.
    ack = make_chat_message(sender="SERVER", message=f"Received message type '{message_type}'.")
    send_message(client_socket, ack)


def handle_client_connection(
    client_socket: socket.socket,
    client_address: tuple[str, int],
    server_state: ServerState,
) -> None:
    """
    Keep one client connection open until disconnect.

    Scope:
    - receive raw bytes
    - parse framed messages
    - route to handlers
    """

    client_id = server_state.register_client(client_socket)
    print(f"[CLIENT] Handler started for {client_address} as {client_id}")

    text_buffer = ""

    with client_socket:
        # Initial connect acknowledgement when handler starts.
        send_message(client_socket, make_connect_message(client_id=client_id, client_version="server-1.0"))

        while True:
            data = client_socket.recv(4096)
            if not data:
                print(f"[CLIENT] Disconnected: {client_address} ({client_id})")
                break

            text_buffer += data.decode("utf-8", errors="replace")

            try:
                messages, text_buffer = split_socket_buffer(text_buffer)
            except ProtocolError as error:
                send_message(client_socket, make_error_message("Malformed message.", str(error)))
                text_buffer = ""
                continue

            for message in messages:
                try:
                    handle_incoming_message(
                        client_socket=client_socket,
                        message=message,
                        server_state=server_state,
                        client_id=client_id,
                    )
                except ProtocolError as error:
                    send_message(client_socket, make_error_message("Invalid message payload.", str(error)))

    server_state.unregister_client(client_id)

    # PBI 2.4: ensure lobby update goes out when a user disconnects.
    broadcast_online_users(server_state)

    print(f"[CLIENT] Active clients: {server_state.connected_clients}")
