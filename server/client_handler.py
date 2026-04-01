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
    make_game_over_message,
    make_game_state_message,
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

        # Notify involved players about decision.
        sender_socket = server_state.get_socket_for_username(from_user)
        receiver_socket = server_state.get_socket_for_username(to_user)
        decision_message = make_invitation_message(
            from_user=from_user,
            to_user=to_user,
            action=reason,
            game_id=game_id,
        )

        if reason == "accepted":
            # UX rule: only inviter gets explicit "accepted" status message.
            # The acceptor already knows they accepted via their own action.
            if sender_socket is not None:
                try:
                    send_message(sender_socket, decision_message)
                except OSError:
                    pass
        else:
            # For decline/cancel, notify both sides for clear state sync.
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

            # Sprint 3 bootstrap: send initial authoritative game state
            # immediately after match pairing is complete.
            initial_state = server_state.get_match_state_dict(game_id)
            if initial_state is not None:
                game_state_message = make_game_state_message(game_id=game_id, state=initial_state)
                for sock in [sender_socket, receiver_socket]:
                    if sock is None:
                        continue
                    try:
                        send_message(sock, game_state_message)
                    except OSError:
                        continue
        return

    send_message(client_socket, make_error_message("Unsupported invitation action."))


def _handle_movement_message(
    client_socket: socket.socket,
    payload: dict,
    server_state: ServerState,
    sender_client_id: str,
) -> None:
    """
    Sprint 3 backend movement integration.

    - Validates command ownership and direction value.
    - Applies movement through authoritative server state.
    - Broadcasts GAME_STATE and optional GAME_OVER.
    """

    player = payload.get("player", "").strip()
    direction = payload.get("direction", "").strip().lower()
    sender_username = server_state.get_client_username(sender_client_id)

    if sender_username is None:
        send_message(client_socket, make_error_message("Username must be set before movement."))
        return

    if player != sender_username:
        send_message(client_socket, make_error_message("Movement player mismatch."))
        return

    success, reason, game_id, state_dict, game_over_payload = server_state.process_player_movement(
        player=player,
        direction=direction,
    )
    if not success:
        reason_to_message = {
            "invalid_direction": "Invalid movement direction.",
            "player_not_in_match": "You are not in an active match.",
            "match_not_found": "Match state could not be found.",
            "player_not_alive": "Your snake is no longer active.",
            "match_finished": "Match has already ended.",
        }
        send_message(client_socket, make_error_message(reason_to_message.get(reason, "Movement rejected.")))
        return

    if game_id is None or state_dict is None:
        send_message(client_socket, make_error_message("Internal state update failed."))
        return

    players = server_state.get_match_players(game_id)
    if players is None:
        send_message(client_socket, make_error_message("Unable to resolve match players."))
        return

    sockets = [server_state.get_socket_for_username(players[0]), server_state.get_socket_for_username(players[1])]

    # PBI 3.12: broadcast packaged authoritative game state each update.
    game_state_message = make_game_state_message(game_id=game_id, state=state_dict)
    for sock in sockets:
        if sock is None:
            continue
        try:
            send_message(sock, game_state_message)
        except OSError:
            continue

    # PBI 3.10/3.11: send explicit end-of-game summary when match is finished.
    if game_over_payload is not None:
        game_over_message = make_game_over_message(
            game_id=game_over_payload["game_id"],
            winner=game_over_payload["winner"],
            final_scores=game_over_payload.get("final_scores"),
            reason=game_over_payload.get("reason"),
        )
        for sock in sockets:
            if sock is None:
                continue
            try:
                send_message(sock, game_over_message)
            except OSError:
                continue


def handle_incoming_message(
    client_socket: socket.socket,
    message: dict,
    server_state: ServerState,
    client_id: str,
) -> None:
    """Process one incoming message and send server responses."""

    message_type = message["type"]
    payload = message["payload"]
    current_username = server_state.get_client_username(client_id)

    # Guardrail: user must successfully register a username before any
    # other lobby actions (chat, invites, etc.) are allowed.
    if message_type != MessageType.USERNAME.value and current_username is None:
        send_message(
            client_socket,
            make_error_message("Choose an available username before continuing."),
        )
        return

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

    if message_type == MessageType.MOVEMENT.value:
        _handle_movement_message(
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
