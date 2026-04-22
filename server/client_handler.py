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
    make_emote_message,
    make_error_message,
    make_game_over_message,
    make_game_state_message,
    make_invitation_message,
    make_online_users_message,
)
from shared.utils import encode_message_for_socket, split_socket_buffer

# Backend feature map for prelobby/lobby ownership.
# These IDs mirror the frontend list in `client/prelobby_pygame.py`.
BACKEND_FEATURES: dict[str, str] = {
    "PRE_01_MUSIC_TOGGLE": "No backend handling (client-local audio only).",
    "PRE_02_STATUS_BADGE": "No backend handling (client-local typing/ready state).",
    "PRE_03_DEPLOY_NAME_INPUT": "Validated on username submission in handle_incoming_message -> MessageType.USERNAME.",
    "PRE_04_SUGGESTED_NAMES": "No backend handling (client-local suggestion chips).",
    "PRE_05_DEPLOY_BUTTON": "Indirect: deploy proceeds to lobby where username is submitted to server.",
    "PRE_06_DEPLOY_TRANSITION": "No backend handling (client-local transition animation).",
    "LOB_01_USERNAME_SUBMISSION": "handle_incoming_message(USERNAME): uniqueness, skin/chat endpoint capture, lobby broadcast.",
    "LOB_02_ONLINE_AND_LEADERBOARD_SYNC": "broadcast_online_users + ServerState snapshots (users, idle, matches, wins, chat peers).",
}


# // Feature: Server Message Routing
# // Purpose: Send one protocol message as newline-delimited JSON bytes.
# // Trigger: Triggered when this system needs to open, close, or send network/audio data.
def send_message(client_socket: socket.socket, message: dict) -> None:
    """Send one protocol message as newline-delimited JSON bytes."""

    client_socket.sendall(encode_message_for_socket(message))


# Feature: LOB_02_ONLINE_AND_LEADERBOARD_SYNC (authoritative online/idle/match/wins/chat-peers snapshot).
# // Feature: Server Message Routing
# // Purpose: PBI 2.
# // Trigger: Called by the server message routing flow when this helper is needed.
def broadcast_online_users(server_state: ServerState) -> None:
    """
    PBI 2.4: broadcast updated online users list to all connected clients.

    We include `waiting` metadata so clients can render waiting-state hints.
    """

    users = server_state.get_online_users()
    waiting = len(users) < 2
    message = make_online_users_message(users)
    message["payload"]["waiting"] = waiting
    # Sprint 5 PBI 5.3: expose lobby-vs-active split for UI clarity.
    message["payload"]["idle_users"] = server_state.get_idle_users()
    message["payload"]["active_players"] = len(users) - len(message["payload"]["idle_users"])
    message["payload"]["user_sessions"] = server_state.get_online_user_sessions()
    message["payload"]["wins"] = server_state.get_online_user_wins()
    message["payload"]["active_matches"] = server_state.get_active_matches_snapshot()
    message["payload"]["chat_peers"] = server_state.get_chat_peers_snapshot()

    for sock in server_state.get_client_sockets():
        try:
            send_message(sock, message)
        except OSError:
            # Ignore socket send failures here; disconnect cleanup path handles it.
            continue


# Feature: LOB_02_ONLINE_AND_LEADERBOARD_SYNC (waiting-state hint tied to roster size).
# // Feature: Server Message Routing
# // Purpose: PBI 2.
# // Trigger: Triggered when this system needs to open, close, or send network/audio data.
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


# Feature: LOB_02_ONLINE_AND_LEADERBOARD_SYNC (match assignment triggers roster refresh).
# // Feature: Server Message Routing
# // Purpose: PBI 2.
# // Trigger: Triggered by user input events (keyboard/mouse) or UI interactions.
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

    if action == "quick_match":
        if from_user.casefold() != sender_username.casefold():
            send_message(client_socket, make_error_message("Quick Match sender mismatch."))
            return

        success, reason, opponent, game_id = server_state.request_quick_match(from_user)
        if not success:
            reason_to_error = {
                "user_offline": "Quick Match failed: user offline.",
                "user_in_match": "You are already in a match.",
                "single_session_busy": "A match is already running. Wait for it to finish.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Quick Match failed.")))
            return

        if reason in {"waiting", "already_waiting"}:
            send_message(
                client_socket,
                make_invitation_message(
                    from_user="SERVER",
                    to_user=sender_username,
                    action="waiting",
                ),
            )
            return

        if reason == "matched" and game_id is not None and opponent is not None:
            p1_socket = server_state.get_socket_for_username(opponent)
            p2_socket = server_state.get_socket_for_username(sender_username)
            match_skins = {
                opponent: server_state.get_skin_dict_for_username(opponent),
                sender_username: server_state.get_skin_dict_for_username(sender_username),
            }
            start_message = make_invitation_message(
                from_user=opponent,
                to_user=sender_username,
                action="match_started",
                game_id=game_id,
                skins=match_skins,
            )
            for sock in [p1_socket, p2_socket]:
                if sock is None:
                    continue
                try:
                    send_message(sock, start_message)
                except OSError:
                    continue

            initial_state = server_state.get_match_state_dict(game_id)
            if initial_state is not None:
                game_state_message = make_game_state_message(game_id=game_id, state=initial_state)
                for sock in [p1_socket, p2_socket]:
                    if sock is None:
                        continue
                    try:
                        send_message(sock, game_state_message)
                    except OSError:
                        continue
            # Match assignment changes idle/active lobby state for everyone.
            broadcast_online_users(server_state)
            return

    if action == "quick_cancel":
        if from_user.casefold() != sender_username.casefold():
            send_message(client_socket, make_error_message("Quick Match cancel sender mismatch."))
            return
        success, reason = server_state.cancel_quick_match(from_user)
        if not success:
            reason_to_error = {
                "user_offline": "Quick Match cancel failed: user offline.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Quick Match cancel failed.")))
            return
        send_message(
            client_socket,
            make_invitation_message(
                from_user="SERVER",
                to_user=sender_username,
                action="quick_cancelled",
            ),
        )
        return

    if action == "spectate":
        # Spectator join:
        # - from_user must match connected sender
        # - target can be resolved by `game_id` or `to_user` active match
        if from_user.casefold() != sender_username.casefold():
            send_message(client_socket, make_error_message("Spectator sender mismatch."))
            return

        requested_game_id = str(payload.get("game_id", "")).strip() or None
        success, reason, resolved_game_id, players = server_state.add_spectator(
            username=sender_username,
            game_id=requested_game_id,
            target_user=to_user,
        )
        if not success or resolved_game_id is None:
            reason_to_error = {
                "user_offline": "Spectator join failed: user is offline.",
                "user_in_match": "You cannot spectate while in an active match.",
                "match_not_found": "No active match found to spectate.",
                "match_finished": "Cannot spectate a finished match.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Spectator join failed.")))
            return

        match_skins: dict[str, dict] = {}
        if players is not None:
            for p in players:
                match_skins[p] = server_state.get_skin_dict_for_username(p)
        spectator_notice = make_invitation_message(
            from_user="SERVER",
            to_user=sender_username,
            action="spectate_joined",
            game_id=resolved_game_id,
            skins=match_skins,
        )
        send_message(client_socket, spectator_notice)

        initial_state = server_state.get_match_state_dict(resolved_game_id)
        if initial_state is not None:
            send_message(
                client_socket,
                make_game_state_message(game_id=resolved_game_id, state=initial_state),
            )

        if players is not None:
            send_message(
                client_socket,
                make_chat_message(
                    sender="SERVER",
                    message=f"Now spectating {players[0]} vs {players[1]} ({resolved_game_id}).",
                ),
            )
        return

    if action == "spectate_leave":
        if from_user.casefold() != sender_username.casefold():
            send_message(client_socket, make_error_message("Spectator sender mismatch."))
            return

        success, reason = server_state.remove_spectator(sender_username)
        if not success:
            reason_to_error = {
                "user_offline": "Spectator leave failed: user is offline.",
                "not_spectating": "You are not currently spectating any match.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Spectator leave failed.")))
            return

        send_message(
            client_socket,
            make_invitation_message(
                from_user="SERVER",
                to_user=sender_username,
                action="spectate_left",
            ),
        )
        return

    if action == "spawn_pie":
        req_game_id = str(payload.get("game_id", "")).strip() or None
        try:
            x = int(payload.get("x"))
            y = int(payload.get("y"))
        except (TypeError, ValueError):
            return
        kind = str(payload.get("kind", "green")).strip().lower()
        server_state.spawn_blob_pie(
            actor=sender_username,
            game_id=req_game_id,
            x=x,
            y=y,
            kind=kind,
        )
        return

    if action == "send":
        # For "send", connected sender must match `from_user`.
        if from_user.casefold() != sender_username.casefold():
            send_message(client_socket, make_error_message("Invitation sender mismatch."))
            return

        success, reason = server_state.create_invitation(from_user=from_user, to_user=to_user)
        if not success:
            reason_to_error = {
                "cannot_invite_self": "You cannot invite yourself.",
                "user_offline": "Selected player is offline.",
                "user_in_match": "Selected player is already in a match.",
                "user_busy": "Selected player is busy with another invite.",
                "single_session_busy": "A match is already running. Wait for it to finish.",
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
        # - accept/decline are sent by invitee (`to_user`)
        # - cancel is sent by inviter (`from_user`)
        if action == "cancel":
            if from_user.casefold() != sender_username.casefold():
                send_message(client_socket, make_error_message("Invitation canceller mismatch."))
                return
        elif to_user.casefold() != sender_username.casefold():
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
                "single_session_busy": "A match is already running. Wait for it to finish.",
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
            match_skins = {
                from_user: server_state.get_skin_dict_for_username(from_user),
                to_user: server_state.get_skin_dict_for_username(to_user),
            }
            start_message = make_invitation_message(
                from_user=from_user,
                to_user=to_user,
                action="match_started",
                game_id=game_id,
                skins=match_skins,
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
            # Match assignment changes idle/active lobby state for everyone.
            broadcast_online_users(server_state)
        return

    if action == "handoff":
        # GUI -> Pygame transition marker. Keep active match alive briefly
        # while clients reconnect using the same usernames.
        if sender_username.casefold() != from_user.casefold():
            send_message(client_socket, make_error_message("Invitation sender mismatch."))
            return

        success, reason = server_state.mark_match_handoff(from_user)
        if not success:
            reason_to_error = {
                "user_offline": "Handoff failed: user offline.",
                "user_not_in_match": "Handoff failed: no active match.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Handoff failed.")))
            return

        send_message(
            client_socket,
            make_chat_message(sender="SERVER", message="Match handoff marked. Reconnect to continue."),
        )
        return

    send_message(client_socket, make_error_message("Unsupported invitation action."))


# // Feature: Server Message Routing
# // Purpose: Sprint 3 backend movement integration.
# // Trigger: Triggered by user input events (keyboard/mouse) or UI interactions.
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

    # Sprint 4 PBI 4.11: perform explicit movement validation before update.
    is_valid, validation_reason, validated_game_id = server_state.validate_movement_command(
        player=player,
        direction=direction,
    )
    if not is_valid:
        reason_to_message = {
            "invalid_direction": "Invalid movement direction.",
            "player_not_in_match": "You are not in an active match.",
            "match_not_found": "Match state could not be found.",
            "player_not_alive": "Your snake is no longer active.",
            "match_finished": "Match has already ended.",
        }
        send_message(
            client_socket,
            make_error_message(reason_to_message.get(validation_reason, "Movement rejected.")),
        )
        return

    success, reason, game_id, _state_dict, _game_over_payload = server_state.process_player_movement(
        player=player,
        direction=direction,
    )

    # Server-side movement trace for multi-match debugging.
    # Includes game_id so concurrent matches are easy to differentiate.
    trace_game_id = game_id or validated_game_id or "unknown"
    print(
        f"[MOVE][game={trace_game_id}] player={player} direction={direction} "
        f"result={'ok' if success else 'rejected'} reason={reason}"
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

    if game_id is None:
        send_message(client_socket, make_error_message("Internal state update failed."))
        return
    # Authoritative game-state and game-over broadcasts are emitted by the
    # continuous server loop (`server/server.py::_run_game_loop`). Sending
    # duplicates here can build client-side backlogs and cause visible lag.


# Feature: LOB_02_ONLINE_AND_LEADERBOARD_SYNC (lobby/match social signals rely on active roster routing).
# // Feature: Server Message Routing
# // Purpose: Sprint 6 PBI 6.
# // Trigger: Triggered by user input events (keyboard/mouse) or UI interactions.
def _handle_chat_message(
    client_socket: socket.socket,
    payload: dict,
    server_state: ServerState,
    sender_client_id: str,
) -> None:
    """
    Sprint 6 PBI 6.1 backend chat routing.

    Supports:
    - lobby-wide chat broadcast
    - direct message delivery using optional `recipient`
    """

    sender_username = server_state.get_client_username(sender_client_id)
    if sender_username is None:
        send_message(client_socket, make_error_message("Username must be set before chat."))
        return

    claimed_sender = str(payload.get("sender", "")).strip()
    if claimed_sender and claimed_sender.casefold() != sender_username.casefold():
        send_message(client_socket, make_error_message("Chat sender mismatch."))
        return

    text = str(payload.get("message", "")).strip()
    if not text:
        send_message(client_socket, make_error_message("Chat message cannot be empty."))
        return

    recipient_raw = payload.get("recipient")
    recipient = str(recipient_raw).strip() if recipient_raw is not None else ""
    message_kind = str(payload.get("kind", "")).strip().lower()
    is_cheer = message_kind == "cheer" or text.lower().startswith("/cheer")

    if is_cheer:
        cheer_text = text
        if cheer_text.lower().startswith("/cheer"):
            cheer_text = cheer_text[6:].strip(" :-")
        if not cheer_text:
            cheer_text = "Let's go!"

        requested_game_id = str(payload.get("game_id", "")).strip() or None
        target_user = str(payload.get("target_user", "")).strip() or None
        if target_user is None and recipient:
            # If client reused private-chat target while cheering, treat that
            # target as a match lookup hint instead of a direct DM recipient.
            target_user = recipient

        success, reason, resolved_game_id, players = server_state.resolve_live_match_for_interaction(
            sender_username,
            game_id=requested_game_id,
            target_user=target_user,
        )
        if not success or resolved_game_id is None:
            reason_to_error = {
                "user_offline": "Cheer failed: user offline.",
                "match_not_found": "Cheer failed: no active match to cheer for.",
                "match_finished": "Cheer failed: match already finished.",
            }
            send_message(client_socket, make_error_message(reason_to_error.get(reason, "Cheer failed.")))
            return

        cheer_message = make_chat_message(
            sender=sender_username,
            message=f"[CHEER] {cheer_text}",
        )
        cheer_message["payload"]["kind"] = "cheer"
        cheer_message["payload"]["game_id"] = resolved_game_id
        if players is not None:
            cheer_message["payload"]["players"] = list(players)

        sockets = server_state.get_match_session_sockets(resolved_game_id)
        if not sockets:
            send_message(client_socket, make_error_message("Cheer failed: match recipients unavailable."))
            return

        for sock in sockets:
            try:
                send_message(sock, cheer_message)
            except OSError:
                continue
        return

    send_message(
        client_socket,
        make_error_message("Server chat relay disabled. Use direct peer chat transport."),
    )


_VALID_EMOTES: frozenset[str] = frozenset({"heheheha", "grrr", "cry"})


# Feature: LOB_02_ONLINE_AND_LEADERBOARD_SYNC (emote routed to current live match participants/spectators).
# // Feature: Server Message Routing
# // Purpose: Handles emote message and applies the related game action.
# // Trigger: Triggered by user input events (keyboard/mouse) or UI interactions.
def _handle_emote_message(
    client_socket: socket.socket,
    payload: dict,
    server_state: ServerState,
    sender_client_id: str,
) -> None:
    sender_username = server_state.get_client_username(sender_client_id)
    if sender_username is None:
        return

    claimed_sender = str(payload.get("sender", "")).strip()
    if claimed_sender and claimed_sender.casefold() != sender_username.casefold():
        return

    emote = str(payload.get("emote", "")).strip()
    if emote not in _VALID_EMOTES:
        return

    game_id = str(payload.get("game_id", "")).strip() or None
    success, _reason, resolved_game_id, _players = server_state.resolve_live_match_for_interaction(
        sender_username,
        game_id=game_id,
    )
    if not success or resolved_game_id is None:
        return

    msg = make_emote_message(sender=sender_username, emote=emote, game_id=resolved_game_id)
    sender_socket = server_state.get_socket_for_username(sender_username)
    delivered_to_sender = False
    for sock in server_state.get_match_session_sockets(resolved_game_id):
        if sender_socket is not None and sock is sender_socket:
            delivered_to_sender = True
        try:
            send_message(sock, msg)
        except OSError:
            continue
    # Keep sender feedback reliable even if participant mapping is temporarily stale.
    if sender_socket is not None and not delivered_to_sender:
        try:
            send_message(sender_socket, msg)
        except OSError:
            pass


# Features: LOB_01_USERNAME_SUBMISSION, LOB_02_ONLINE_AND_LEADERBOARD_SYNC.
# // Feature: Server Message Routing
# // Purpose: Process one incoming message and send server responses.
# // Trigger: Called by the server message routing flow when this helper is needed.
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

        # Cosmetic skin chosen in the lobby travels with the username payload.
        server_state.set_client_skin(client_id, payload.get("skin"))
        chat_host = str(payload.get("chat_host", "")).strip()
        try:
            chat_port = int(payload.get("chat_port", 0))
        except (TypeError, ValueError):
            chat_port = 0
        if not chat_host:
            try:
                chat_host = str(client_socket.getpeername()[0]).strip()
            except OSError:
                chat_host = ""
        if chat_host and 1 <= chat_port <= 65535:
            server_state.set_client_chat_endpoint(client_id, chat_host, chat_port)

        # If the player is reconnecting into an active match (game window), mark
        # them as ready so the pre-match countdown can begin once both arrive.
        server_state.mark_player_in_game_window(username)

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
        _handle_chat_message(
            client_socket=client_socket,
            payload=payload,
            server_state=server_state,
            sender_client_id=client_id,
        )
        return

    if message_type == MessageType.EMOTE.value:
        _handle_emote_message(
            client_socket=client_socket,
            payload=payload,
            server_state=server_state,
            sender_client_id=client_id,
        )
        return

    # Default acknowledgement for unhandled message types.
    ack = make_chat_message(sender="SERVER", message=f"Received message type '{message_type}'.")
    send_message(client_socket, ack)


# // Feature: Server Message Routing
# // Purpose: Keep one client connection open until disconnect.
# // Trigger: Called by the server message routing flow when this helper is needed.
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
            try:
                data = client_socket.recv(4096)
            except (ConnectionResetError, ConnectionAbortedError, OSError) as error:
                # Treat transport-level socket resets as normal disconnects.
                # Common on Windows when clients close quickly (WinError 10054).
                print(f"[CLIENT] Disconnected (socket error): {client_address} ({client_id}) - {error}")
                break
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
                except Exception as error:
                    # Sprint 5 PBI 5.7: never let one malformed action crash the handler loop.
                    send_message(client_socket, make_error_message("Server action failed.", str(error)))

    disconnect_events = server_state.unregister_client_with_events(client_id)

    # Sprint 5 PBI 5.5: notify remaining player when match ends by disconnect.
    for event in disconnect_events:
        if event.get("type") != "match_abandoned":
            continue
        players = event.get("players", ())
        spectators = event.get("spectators", ())
        winner = str(event.get("winner", "draw"))
        disconnected_player = str(event.get("disconnected_player", "unknown"))
        game_id = str(event.get("game_id", "unknown"))
        final_scores = {str(players[0]): 0, str(players[1]): 0} if len(players) == 2 else None
        game_over_message = make_game_over_message(
            game_id=game_id,
            winner=winner,
            final_scores=final_scores,
            reason=f"disconnect:{disconnected_player}",
        )
        recipients = [*players, *spectators]
        for user in recipients:
            if user == disconnected_player:
                continue
            sock = server_state.get_socket_for_username(str(user))
            if sock is None:
                continue
            try:
                send_message(sock, game_over_message)
            except OSError:
                continue

    # PBI 2.4: ensure lobby update goes out when a user disconnects.
    broadcast_online_users(server_state)

    print(f"[CLIENT] Active clients: {server_state.connected_clients}")
