"""
Sprint 4 PBI 4.1..4.10 - Pygame rendering, controls, messaging, and result screen.

This module provides the first gameplay window foundation for the frontend.
Current scope:
- opening a Pygame window
- running a render loop
- handling close/quit input cleanly
- rendering the board grid
- rendering both snakes
- rendering pies
- rendering obstacles
- rendering health scores
- keyboard controls
- sending movement commands to server
- receiving and displaying updated game state
- showing end-of-game result screen
"""

from __future__ import annotations

import queue
import threading
from collections import deque

import pygame

from client.network import ClientConnection
from shared.protocol import (
    MessageType,
    make_chat_message,
    make_invitation_message,
    make_movement_message,
    make_username_message,
)

WINDOW_WIDTH = 1220
WINDOW_HEIGHT = 780
WINDOW_TITLE = "Python-Arena - Sprint 4"
TARGET_FPS = 60
MOVE_INTERVAL_MS = 120
STATE_INTERPOLATION_MS = 120
BOARD_COLS = 28
BOARD_ROWS = 24
CELL_SIZE = 24
GRID_LINE_WIDTH = 1
BASE_SNAKE_LENGTH = 3
HEALTH_PER_GROWTH_SEGMENT = 10
PIE_HEALTH_GAIN = 10
OBSTACLE_COLLISION_DAMAGE = 12
SNAKE_COLLISION_DAMAGE = 16
TARGET_PIE_COUNT = 4
LOCAL_INITIAL_HEALTH = 1000
BG_COLOR = (16, 20, 28)
PANEL_COLOR = (28, 35, 48)
TEXT_COLOR = (235, 241, 248)
SUBTEXT_COLOR = (146, 166, 188)
ACCENT_COLOR = (88, 196, 255)
BOARD_BG_COLOR = (12, 16, 24)
GRID_COLOR = (53, 67, 87)
SNAKE_A_BODY_COLOR = (82, 214, 127)
SNAKE_A_HEAD_COLOR = (36, 176, 85)
SNAKE_B_BODY_COLOR = (255, 187, 84)
SNAKE_B_HEAD_COLOR = (238, 143, 31)
PIE_COLOR = (236, 83, 99)
PIE_HIGHLIGHT_COLOR = (255, 158, 170)
OBSTACLE_COLOR = (121, 134, 153)
OBSTACLE_EDGE_COLOR = (175, 189, 210)
ERROR_PANEL_COLOR = (72, 30, 30)
ERROR_TEXT_COLOR = (255, 210, 210)
_DIRECTIONS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
_OPPOSITE_DIRECTION: dict[str, str] = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


class PygameArenaWindow:
    """Minimal Pygame window shell used for Sprint 4 gameplay UI work."""

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        username: str,
        preferred_opponent: str | None = None,
        spectator_mode: bool = False,
        return_to_tk_lobby: bool = True,
        keep_window_open_on_return: bool = False,
    ) -> None:
        pygame.init()
        pygame.display.set_caption(WINDOW_TITLE)
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.clock = pygame.time.Clock()
        self.running = True
        self.server_ip = server_ip
        self.server_port = server_port
        self.username = username
        self.return_to_tk_lobby = return_to_tk_lobby
        self.keep_window_open_on_return = keep_window_open_on_return
        # Sprint 6 PBI 6.8: spectator mode client-side runtime flag.
        self.spectator_mode = spectator_mode
        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.incoming_queue: queue.Queue[dict] = queue.Queue()
        self.username_confirmed = False
        self.online_users: list[str] = []
        self.preferred_opponent = preferred_opponent
        self.pending_invite_to: str | None = None
        self.pending_invite_sent_ms = 0
        self.pending_spectate_to: str | None = None
        self.pending_spectate_sent_ms = 0
        self.last_server_message = "Connecting..."
        self.last_input_direction = "-"
        self.last_sent_direction = "-"
        self.attempted_movement_commands = 0
        self.sent_movement_commands = 0
        self.accepted_game_states = 0
        self.rejected_movement_commands = 0
        self.active_game_id: str | None = None
        self.has_authoritative_state = False
        self.match_status = "waiting"
        self.match_winner = "-"
        self.timer_remaining = 0
        self.timer_elapsed = 0
        self.connection_healthy = True
        self.connection_notice = ""
        self.player_a_name = "Snake A"
        self.player_b_name = "Snake B"
        self.last_game_state_ms = 0
        self.state_interp_start_ms = 0
        self.render_snake_a: list[tuple[float, float]] = []
        self.render_snake_b: list[tuple[float, float]] = []
        self.interp_from_snake_a: list[tuple[float, float]] = []
        self.interp_from_snake_b: list[tuple[float, float]] = []
        self.interp_to_snake_a: list[tuple[float, float]] = []
        self.interp_to_snake_b: list[tuple[float, float]] = []
        self.show_result_screen = False
        self.result_winner = "-"
        self.result_reason = "-"
        self.result_scores: dict[str, int] = {}
        self.return_to_lobby_requested = False
        # Sprint 6 PBI 6.6 + 6.7: in-game chat runtime state.
        self.chat_messages: deque[str] = deque(maxlen=8)
        self.chat_input = ""
        self.chat_typing = False
        # Sprint 6 PBI 6.10: small creative "cheer" visual pulse.
        self.cheer_pulse_until_ms = 0
        self.board_pixel_width = BOARD_COLS * CELL_SIZE
        self.board_pixel_height = BOARD_ROWS * CELL_SIZE
        self.board_origin_x = 100
        self.board_origin_y = 120
        self.font_title = pygame.font.SysFont("consolas", 36, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 22)
        self.font_hint = pygame.font.SysFont("consolas", 18)
        self.font_score = pygame.font.SysFont("consolas", 24, bold=True)
        self.font_score_label = pygame.font.SysFont("consolas", 18)
        self.font_result_title = pygame.font.SysFont("consolas", 32, bold=True)
        self.font_result_body = pygame.font.SysFont("consolas", 22)

        # PBI 4.3 baseline snake rendering (authoritative state integration follows in next PBIs).
        self.snake_a: list[tuple[int, int]] = [(3, 9), (2, 9), (1, 9)]
        self.snake_b: list[tuple[int, int]] = [(16, 11), (17, 11), (18, 11)]
        self.render_snake_a = [(float(x), float(y)) for x, y in self.snake_a]
        self.render_snake_b = [(float(x), float(y)) for x, y in self.snake_b]
        self.snake_a_direction = "right"
        self.snake_b_direction = "left"
        self.snake_a_health = LOCAL_INITIAL_HEALTH
        self.snake_b_health = LOCAL_INITIAL_HEALTH
        # PBI 4.4 baseline pie rendering.
        self.pies: list[tuple[int, int]] = [(7, 5), (12, 14)]
        # PBI 4.5 baseline obstacle rendering.
        self.obstacles: list[tuple[int, int]] = []
        self.last_move_ms = pygame.time.get_ticks()
        self._reset_local_round_layout()
        self._connect_to_server()

    def run(self) -> bool:
        """Start the main window loop until user exits."""

        while self.running:
            self._handle_events()
            self._drain_incoming_queue()
            self._update_movement()
            self._draw_frame()
            pygame.display.flip()
            self.clock.tick(TARGET_FPS)

        self._disconnect_from_server()
        if self.return_to_lobby_requested and self.return_to_tk_lobby:
            pygame.quit()
            self._launch_lobby()
        elif self.return_to_lobby_requested and self.keep_window_open_on_return:
            # Keep current pygame window alive so caller can switch scene
            # without closing/reopening another native window.
            pass
        else:
            pygame.quit()
        return self.return_to_lobby_requested

    def _launch_lobby(self) -> None:
        """Return player to Tkinter lobby with same connection settings."""

        from client.gui import ArenaGuiApp

        app = ArenaGuiApp()
        app.server_ip_var.set(self.server_ip)
        app.server_port_var.set(str(self.server_port))
        app.username_var.set(self.username)
        app._retry_same_username = True
        # Give backend a short moment to release old socket/session cleanly.
        app.root.after(450, app._connect)
        app.run()

    def _handle_events(self) -> None:
        """Process window close and keyboard exit events."""

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if self._handle_chat_input_event(event):
                    continue
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_SPACE:
                    self.show_result_screen = False
                elif event.key == pygame.K_r and self.show_result_screen:
                    self.return_to_lobby_requested = True
                    self.running = False
                elif event.key == pygame.K_c:
                    self._send_cheer()
                elif event.key == pygame.K_q and self.spectator_mode:
                    self._leave_spectator_mode()
                elif event.key == pygame.K_t:
                    self.chat_typing = True
                self._handle_direction_input(event.key)

    def _handle_chat_input_event(self, event: pygame.event.Event) -> bool:
        """Sprint 6 PBI 6.6: handle in-game chat compose input."""

        if event.key == pygame.K_RETURN:
            if self.chat_typing:
                self._submit_chat_message()
            else:
                self.chat_typing = True
            return True

        if event.key == pygame.K_ESCAPE and self.chat_typing:
            self.chat_typing = False
            self.chat_input = ""
            return True

        if not self.chat_typing:
            return False

        if event.key == pygame.K_BACKSPACE:
            self.chat_input = self.chat_input[:-1]
            return True

        # Keep typing payload simple and printable for lobby/game chat.
        if event.unicode and event.unicode.isprintable():
            self.chat_input += event.unicode
            if len(self.chat_input) > 120:
                self.chat_input = self.chat_input[:120]
            return True

        return False

    def _submit_chat_message(self) -> None:
        """Send chat message to server."""

        text = self.chat_input.strip()
        self.chat_typing = False
        self.chat_input = ""
        if not text:
            return
        if self.connection is None:
            return
        try:
            self.connection.send_message(make_chat_message(sender=self.username, message=text))
        except OSError:
            self.chat_messages.append("[CHAT] Failed to send message.")

    def _send_cheer(self) -> None:
        """Sprint 6 PBI 6.10: trigger a lightweight cheer interaction."""

        self.cheer_pulse_until_ms = pygame.time.get_ticks() + 650
        if self.connection is None:
            return
        try:
            cheer_message = make_chat_message(sender=self.username, message="/cheer CHEER!")
            cheer_message["payload"]["kind"] = "cheer"
            if self.active_game_id is not None:
                cheer_message["payload"]["game_id"] = self.active_game_id
            self.connection.send_message(cheer_message)
        except OSError:
            self.chat_messages.append("[CHAT] Cheer send failed.")

    def _leave_spectator_mode(self) -> None:
        """Allow spectator to leave immediately and return to lobby."""

        if not self.spectator_mode:
            return

        if self.connection is not None:
            try:
                self.connection.send_message(
                    make_invitation_message(
                        from_user=self.username,
                        to_user=self.pending_spectate_to or self.username,
                        action="spectate_leave",
                    )
                )
            except OSError:
                # Even if leave message fails, return user to lobby immediately.
                pass

        self.return_to_lobby_requested = True
        self.running = False

    def _handle_direction_input(self, key: int) -> None:
        """Handle local player movement direction input."""

        direction_map = {
            pygame.K_UP: "up",
            pygame.K_DOWN: "down",
            pygame.K_LEFT: "left",
            pygame.K_RIGHT: "right",
            pygame.K_w: "up",
            pygame.K_s: "down",
            pygame.K_a: "left",
            pygame.K_d: "right",
        }

        if key in direction_map:
            requested = direction_map[key]
            if _OPPOSITE_DIRECTION[self.snake_a_direction] != requested:
                self.snake_a_direction = requested

    def _connect_to_server(self) -> None:
        """Connect to backend and submit username for movement routing."""

        try:
            self.connection = ClientConnection(server_ip=self.server_ip, server_port=self.server_port)
        except Exception as error:
            self._mark_connection_issue(f"Unable to connect: {error}")
            self.connection = None
            return

        self.last_server_message = f"Connected to {self.server_ip}:{self.server_port}"
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receiver_thread.start()
        self.connection.send_message(make_username_message(self.username))

    def _disconnect_from_server(self) -> None:
        """Close active socket connection cleanly."""

        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None

    def _receiver_loop(self) -> None:
        """Background socket loop for receiving server messages."""

        while self.running and self.connection is not None:
            try:
                message = self.connection.receive_message()
            except Exception as error:
                self.incoming_queue.put(
                    {
                        "type": "socket_error",
                        "payload": {"message": f"Connection closed: {error}"},
                    }
                )
                break
            self.incoming_queue.put(message)

    def _drain_incoming_queue(self) -> None:
        """Process all pending server messages in the main thread."""

        while True:
            try:
                message = self.incoming_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_server_message(message)

    def _handle_server_message(self, message: dict) -> None:
        """Apply relevant backend updates to local UI state."""

        msg_type = message.get("type")
        payload = message.get("payload", {})

        if msg_type == MessageType.CONNECT.value:
            self.last_server_message = f"Session: {payload.get('client_id', 'unknown')}"
            return

        if msg_type == MessageType.ONLINE_USERS.value:
            users = payload.get("users", [])
            self.online_users = list(users)
            self.username_confirmed = any(user.casefold() == self.username.casefold() for user in users)
            self.last_server_message = f"Online users: {len(users)}"
            if self.spectator_mode:
                self._maybe_request_spectate()
            else:
                self._maybe_send_auto_invite()
            return

        if msg_type == MessageType.INVITATION.value:
            action = str(payload.get("action", "")).lower()
            from_user = str(payload.get("from_user", ""))
            to_user = str(payload.get("to_user", ""))
            if action == "send" and to_user.casefold() == self.username.casefold():
                # Spectators never join active matches.
                if self.spectator_mode:
                    if self.connection is not None:
                        self.connection.send_message(
                            make_invitation_message(
                                from_user=from_user,
                                to_user=self.username,
                                action="decline",
                            )
                        )
                    self.last_server_message = f"Declined invite (spectator mode) from {from_user}"
                    return

                # Auto-accept incoming invites so movement commands can enter live matches quickly.
                if self.connection is not None:
                    self.connection.send_message(
                        make_invitation_message(
                            from_user=from_user,
                            to_user=self.username,
                            action="accept",
                        )
                    )
                self.last_server_message = f"Auto-accepted invite from {from_user}"
                return

            if action == "match_started":
                self.active_game_id = payload.get("game_id")
                self.pending_invite_to = None
                self._reset_local_round_layout()
                self.last_server_message = f"Match started ({self.active_game_id})"
                return

            if action == "spectate_joined":
                self.active_game_id = payload.get("game_id", self.active_game_id)
                self.pending_spectate_to = None
                self.pending_spectate_sent_ms = 0
                self.last_server_message = f"Spectating match ({self.active_game_id})"
                return

            if action == "spectate_left":
                self.active_game_id = None
                self.pending_spectate_to = None
                self.pending_spectate_sent_ms = 0
                self.last_server_message = "Stopped spectating."
                return

            if action in {"declined", "cancelled"}:
                self.pending_invite_to = None
                self.last_server_message = f"Invite {action}"
            return

        if msg_type == MessageType.GAME_STATE.value:
            self.active_game_id = payload.get("game_id", self.active_game_id)
            state = payload.get("state", {})
            self._sync_from_game_state(state)
            self.has_authoritative_state = True
            self.last_game_state_ms = pygame.time.get_ticks()
            self.accepted_game_states += 1
            self.last_server_message = "Game state updated"
            return

        if msg_type == MessageType.CHAT.value:
            # Sprint 6 PBI 6.7: display incoming chat stream in gameplay view.
            sender = str(payload.get("sender", "SERVER"))
            text = str(payload.get("message", ""))
            scope = str(payload.get("scope", "")).lower()
            message_game_id = str(payload.get("game_id", ""))

            # Keep lobby chat out of in-match UI.
            if scope == "lobby":
                return

            # Keep chat isolated to the active/spectated match.
            if scope == "match" and self.active_game_id is not None:
                if message_game_id and message_game_id != self.active_game_id:
                    return

            self.chat_messages.append(f"{sender}: {text}")
            return

        if msg_type == MessageType.GAME_OVER.value:
            winner = payload.get("winner", "unknown")
            self.last_server_message = f"Game over. Winner: {winner}"
            self.show_result_screen = True
            self.result_winner = str(winner)
            self.result_reason = str(payload.get("reason", "-"))
            self.result_scores = dict(payload.get("final_scores", {}))
            return

        if msg_type == MessageType.ERROR.value:
            self.last_server_message = payload.get("message", "Server error")
            message_text = str(payload.get("message", "")).lower()
            if "movement" in message_text or "match" in message_text or "active match" in message_text:
                self.rejected_movement_commands += 1
            if (
                "invitation" in message_text
                or "busy" in message_text
                or "offline" in message_text
            ):
                # Retry invite flow if server rejected previous attempt.
                self.pending_invite_to = None
                self.pending_invite_sent_ms = 0
                self.pending_spectate_to = None
                self.pending_spectate_sent_ms = 0
                if self.spectator_mode:
                    self._maybe_request_spectate()
                else:
                    self._maybe_send_auto_invite()
            if (
                "connection" in message_text
                or "disconnect" in message_text
                or "closed" in message_text
            ):
                self._mark_connection_issue(str(payload.get("message", "Connection issue.")))
            return

        if msg_type == "socket_error":
            self._mark_connection_issue(str(payload.get("message", "Lost connection to server.")))
            return

    def _mark_connection_issue(self, message: str) -> None:
        """Switch UI to graceful disconnected/error state."""

        self.connection_healthy = False
        self.connection_notice = message
        self.last_server_message = message
        self.match_status = "disconnected"
        self._disconnect_from_server()

    def _maybe_send_auto_invite(self) -> None:
        """Auto-invite one available opponent to bootstrap a playable match."""

        if self.connection is None or not self.username_confirmed or self.active_game_id is not None:
            return
        if self.pending_invite_to is not None:
            return

        opponents = [user for user in self.online_users if user.casefold() != self.username.casefold()]
        if not opponents:
            return

        preferred = self.preferred_opponent
        if preferred is not None:
            matching = [user for user in opponents if user.casefold() == preferred.casefold()]
            if matching:
                opponent = matching[0]
                # Avoid invitation race: only one deterministic side sends.
                if self.username.casefold() > opponent.casefold():
                    self.last_server_message = f"Waiting invite from {opponent}"
                    return
            else:
                opponent = opponents[0]
        else:
            opponent = opponents[0]

        self.connection.send_message(
            make_invitation_message(
                from_user=self.username,
                to_user=opponent,
                action="send",
            )
        )
        self.pending_invite_to = opponent
        self.pending_invite_sent_ms = pygame.time.get_ticks()
        self.last_server_message = f"Invited {opponent}"

    def _maybe_request_spectate(self) -> None:
        """Sprint 6 PBI 6.8/6.9: join an active match as spectator."""

        if self.connection is None or not self.username_confirmed or self.active_game_id is not None:
            return
        if self.pending_spectate_to is not None:
            return

        targets = [user for user in self.online_users if user.casefold() != self.username.casefold()]
        if not targets:
            self.last_server_message = "Waiting for players to spectate..."
            return

        preferred = self.preferred_opponent
        if preferred is not None:
            matching = [user for user in targets if user.casefold() == preferred.casefold()]
            target = matching[0] if matching else targets[0]
        else:
            target = targets[0]

        self.connection.send_message(
            make_invitation_message(
                from_user=self.username,
                to_user=target,
                action="spectate",
            )
        )
        self.pending_spectate_to = target
        self.pending_spectate_sent_ms = pygame.time.get_ticks()
        self.last_server_message = f"Requesting spectate of {target}..."

    def _sync_from_game_state(self, state: dict) -> None:
        """Update rendered board entities from authoritative backend state."""

        old_snake_a = list(self.snake_a)
        old_snake_b = list(self.snake_b)
        snakes = state.get("snakes", [])
        my_snake = None
        opponent_snake = None
        for snake in snakes:
            if str(snake.get("player", "")).casefold() == self.username.casefold():
                my_snake = snake
            else:
                opponent_snake = snake

        if my_snake is None and len(snakes) >= 1:
            my_snake = snakes[0]
        if opponent_snake is None and len(snakes) >= 2:
            opponent_snake = snakes[1]

        if my_snake is not None:
            self.player_a_name = str(my_snake.get("player", self.player_a_name))
            self.snake_a = [
                (segment.get("x", 0), segment.get("y", 0))
                for segment in my_snake.get("body", [])
            ] or self.snake_a
            self.snake_a_health = int(my_snake.get("health", self.snake_a_health))

        if opponent_snake is not None:
            self.player_b_name = str(opponent_snake.get("player", self.player_b_name))
            self.snake_b = [
                (segment.get("x", 0), segment.get("y", 0))
                for segment in opponent_snake.get("body", [])
            ] or self.snake_b
            self.snake_b_health = int(opponent_snake.get("health", self.snake_b_health))

        self.pies = [
            (
                pie.get("position", {}).get("x", 0),
                pie.get("position", {}).get("y", 0),
            )
            for pie in state.get("pies", [])
        ] or self.pies

        self.obstacles = [
            (
                obstacle.get("position", {}).get("x", 0),
                obstacle.get("position", {}).get("y", 0),
            )
            for obstacle in state.get("obstacles", [])
        ] or self.obstacles

        timer = state.get("timer", {})
        self.timer_remaining = int(timer.get("remaining_seconds", self.timer_remaining))
        self.timer_elapsed = int(timer.get("elapsed_seconds", self.timer_elapsed))
        self.match_status = str(state.get("status", self.match_status))
        self.match_winner = str(state.get("winner", self.match_winner or "-"))
        self._start_state_interpolation(old_snake_a, old_snake_b)

    def _start_state_interpolation(
        self,
        old_snake_a: list[tuple[int, int]],
        old_snake_b: list[tuple[int, int]],
    ) -> None:
        """Prepare linear interpolation between previous and latest server snakes."""

        def to_float_cells(cells: list[tuple[int, int]]) -> list[tuple[float, float]]:
            return [(float(x), float(y)) for x, y in cells]

        to_a = to_float_cells(self.snake_a)
        to_b = to_float_cells(self.snake_b)

        if len(self.render_snake_a) == len(to_a) and self.render_snake_a:
            from_a = list(self.render_snake_a)
        else:
            from_a = to_float_cells(old_snake_a if old_snake_a else self.snake_a)

        if len(self.render_snake_b) == len(to_b) and self.render_snake_b:
            from_b = list(self.render_snake_b)
        else:
            from_b = to_float_cells(old_snake_b if old_snake_b else self.snake_b)

        if len(from_a) != len(to_a):
            from_a = list(to_a)
        if len(from_b) != len(to_b):
            from_b = list(to_b)

        self.interp_from_snake_a = from_a
        self.interp_from_snake_b = from_b
        self.interp_to_snake_a = to_a
        self.interp_to_snake_b = to_b
        self.state_interp_start_ms = pygame.time.get_ticks()
        self.render_snake_a = list(from_a)
        self.render_snake_b = list(from_b)

    def _update_interpolated_snakes(self) -> None:
        """Smoothly interpolate rendered snake cells toward latest server state."""

        if not self.interp_to_snake_a and not self.interp_to_snake_b:
            return

        if STATE_INTERPOLATION_MS <= 0:
            t = 1.0
        else:
            elapsed = pygame.time.get_ticks() - self.state_interp_start_ms
            t = max(0.0, min(1.0, elapsed / STATE_INTERPOLATION_MS))

        def lerp_cells(
            start_cells: list[tuple[float, float]],
            end_cells: list[tuple[float, float]],
        ) -> list[tuple[float, float]]:
            if len(start_cells) != len(end_cells):
                return list(end_cells)
            return [
                (sx + (ex - sx) * t, sy + (ey - sy) * t)
                for (sx, sy), (ex, ey) in zip(start_cells, end_cells)
            ]

        self.render_snake_a = lerp_cells(self.interp_from_snake_a, self.interp_to_snake_a)
        self.render_snake_b = lerp_cells(self.interp_from_snake_b, self.interp_to_snake_b)

    def _update_movement(self) -> None:
        """Update local movement at a fixed interval and send input to backend."""

        if self.show_result_screen or not self.connection_healthy:
            return
        if self.spectator_mode:
            return

        now = pygame.time.get_ticks()
        if now - self.last_move_ms < MOVE_INTERVAL_MS:
            if (
                self.active_game_id is None
                and self.pending_invite_to is not None
                and now - self.pending_invite_sent_ms > 1500
            ):
                # Recover from stale invite state by trying again.
                self.pending_invite_to = None
                self.pending_invite_sent_ms = 0
                self._maybe_send_auto_invite()
            return

        self.last_move_ms = now
        # Once authoritative states are flowing, avoid local preview simulation
        # to prevent jitter from competing state sources.
        if not self.has_authoritative_state:
            self._step_local_physics()
            self._check_local_game_over()
            if self.show_result_screen:
                return
        self._send_movement_command(self.snake_a_direction)

    def _send_movement_command(self, direction: str) -> None:
        """PBI 4.8: send movement command to backend server."""

        self.attempted_movement_commands += 1
        self.last_input_direction = direction

        if self.connection is None or not self.username_confirmed:
            self.last_server_message = "Movement blocked: not connected/registered"
            return
        if self.active_game_id is None:
            self.last_server_message = "Movement blocked: no active match"
            return

        try:
            self.connection.send_message(
                make_movement_message(
                    player=self.username,
                    direction=direction,
                )
            )
            self.sent_movement_commands += 1
            self.last_sent_direction = direction
        except OSError as error:
            self._mark_connection_issue(f"Failed to send movement: {error}")

    def _step_local_physics(self) -> None:
        """
        Apply local gameplay physics for both snakes.

        This keeps controls responsive even before/without authoritative physics
        from the backend.
        """

        self._step_preview_snake(
            snake_cells=self.snake_a,
            direction=self.snake_a_direction,
            own_health_attr="snake_a_health",
            opponent_cells=self.snake_b,
        )
        self._step_preview_snake(
            snake_cells=self.snake_b,
            direction=self.snake_b_direction,
            own_health_attr="snake_b_health",
            opponent_cells=self.snake_a,
        )
        self._ensure_pies_available()

    def _is_out_of_bounds(self, x: int, y: int) -> bool:
        """Return True when a cell is outside board boundaries."""

        return x < 0 or x >= BOARD_COLS or y < 0 or y >= BOARD_ROWS

    def _pushback_cell(self, head_x: int, head_y: int, dx: int, dy: int) -> tuple[int, int]:
        """Compute one-cell pushback position opposite to travel direction."""

        return (head_x - dx, head_y - dy)

    def _random_open_cell(self) -> tuple[int, int] | None:
        """Pick one free board cell not occupied by snakes, pies, or obstacles."""

        occupied = set(self.snake_a) | set(self.snake_b) | set(self.pies) | set(self.obstacles)
        candidates = [
            (x, y)
            for x in range(BOARD_COLS)
            for y in range(BOARD_ROWS)
            if (x, y) not in occupied
        ]
        if not candidates:
            return None
        return candidates[pygame.time.get_ticks() % len(candidates)]

    def _ensure_pies_available(self) -> None:
        """Keep spawning pies during the round so pickups never stop."""

        while len(self.pies) < TARGET_PIE_COUNT:
            cell = self._random_open_cell()
            if cell is None:
                break
            self.pies.append(cell)

    def _reset_local_round_layout(self) -> None:
        """Reset round pickups while keeping deterministic obstacle layout."""

        self.obstacles = [
            (2, 2),
            (3, 2),
            (24, 2),
            (25, 2),
            (2, 21),
            (3, 21),
            (24, 21),
            (25, 21),
            (6, 11),
            (7, 11),
            (13, 6),
            (14, 6),
            (13, 17),
            (14, 17),
            (20, 11),
            (21, 11),
        ]
        self.pies = []
        self._ensure_pies_available()

    def _adjust_snake_length(self, snake_cells: list[tuple[int, int]], health: int) -> None:
        """Grow snake as health exceeds 100 while keeping a minimum base length."""

        # Keep initial body length stable at match start even with high initial
        # health; growth begins only after gaining health above starting value.
        growth_segments = max(0, (health - LOCAL_INITIAL_HEALTH) // HEALTH_PER_GROWTH_SEGMENT)
        target_length = BASE_SNAKE_LENGTH + growth_segments
        if len(snake_cells) > target_length:
            del snake_cells[target_length:]
        elif len(snake_cells) < target_length and snake_cells:
            snake_cells.extend([snake_cells[-1]] * (target_length - len(snake_cells)))

    def _step_preview_snake(
        self,
        snake_cells: list[tuple[int, int]],
        direction: str,
        own_health_attr: str,
        opponent_cells: list[tuple[int, int]],
    ) -> None:
        """Move one snake with obstacle/opponent collision damage and pushback."""

        dx, dy = _DIRECTIONS[direction]
        head_x, head_y = snake_cells[0]
        next_head = (head_x + dx, head_y + dy)
        own_health = int(getattr(self, own_health_attr))

        hit_boundary = self._is_out_of_bounds(*next_head)
        hit_obstacle = next_head in self.obstacles or hit_boundary
        hit_opponent = next_head in opponent_cells
        if hit_obstacle or hit_opponent:
            damage = OBSTACLE_COLLISION_DAMAGE if hit_obstacle else SNAKE_COLLISION_DAMAGE
            own_health = max(0, own_health - damage)
            pushback = self._pushback_cell(head_x, head_y, dx, dy)
            blocked_pushback = (
                self._is_out_of_bounds(*pushback)
                or pushback in self.obstacles
                or pushback in opponent_cells
                or pushback in snake_cells
            )
            if not blocked_pushback:
                snake_cells.insert(0, pushback)
                snake_cells.pop()
            setattr(self, own_health_attr, own_health)
            self._adjust_snake_length(snake_cells, own_health)
            return

        ate_pie = next_head in self.pies
        snake_cells.insert(0, next_head)
        if ate_pie:
            own_health += PIE_HEALTH_GAIN
            self.pies.remove(next_head)
        else:
            snake_cells.pop()
        self._adjust_snake_length(snake_cells, own_health)
        setattr(self, own_health_attr, own_health)

    def _check_local_game_over(self) -> None:
        """End local match immediately when a snake health reaches zero."""

        if self.snake_a_health > 0 and self.snake_b_health > 0:
            return

        self.match_status = "finished"
        self.show_result_screen = True
        self.result_reason = "health_depleted"
        self.result_scores = {
            self.player_a_name: self.snake_a_health,
            self.player_b_name: self.snake_b_health,
        }
        if self.snake_a_health <= 0 and self.snake_b_health <= 0:
            self.result_winner = "Draw"
            self.match_winner = "Draw"
        elif self.snake_a_health <= 0:
            self.result_winner = self.player_b_name
            self.match_winner = self.player_b_name
        else:
            self.result_winner = self.player_a_name
            self.match_winner = self.player_a_name
        self.last_server_message = f"Local game over: {self.result_reason}"

    def _draw_frame(self) -> None:
        """Render the Sprint 4 gameplay shell and board."""

        self.screen.fill(BG_COLOR)

        panel_rect = pygame.Rect(70, 70, WINDOW_WIDTH - 140, WINDOW_HEIGHT - 140)
        pygame.draw.rect(self.screen, PANEL_COLOR, panel_rect, border_radius=14)
        pygame.draw.rect(self.screen, ACCENT_COLOR, panel_rect, width=3, border_radius=14)

        title = self.font_title.render("Python-Arena Board View", True, TEXT_COLOR)
        body = self.font_body.render("Sprint 4 PBI 4.9: live GAME_STATE rendering.", True, TEXT_COLOR)
        hint = self.font_hint.render("Move: Arrows/WASD  |  ESC: exit", True, SUBTEXT_COLOR)

        self.screen.blit(title, (100, 84))
        self.screen.blit(body, (100, 700))
        self.screen.blit(hint, (860, 700))

        self._draw_board()
        self._draw_obstacles()
        self._draw_pies()
        self._draw_snakes()
        self._draw_health_scores()
        self._draw_chat_panel()
        self._draw_mode_banner()
        self._draw_connection_notice()
        if self.show_result_screen:
            self._draw_result_screen()

    def _draw_mode_banner(self) -> None:
        """Sprint 6 PBI 6.8: show explicit mode state in gameplay header."""

        if self.spectator_mode:
            label = "SPECTATOR MODE - Q to leave and return to lobby"
        else:
            label = "PLAYER MODE - WASD/Arrows to move"
        text = self.font_hint.render(label, True, ACCENT_COLOR)
        self.screen.blit(text, (640, 88))

        # Sprint 6 PBI 6.10: cheer pulse visual accent.
        if pygame.time.get_ticks() < self.cheer_pulse_until_ms:
            pulse = pygame.Surface((self.board_pixel_width, self.board_pixel_height), pygame.SRCALPHA)
            pulse.fill((88, 196, 255, 28))
            self.screen.blit(pulse, (self.board_origin_x, self.board_origin_y))

    def _draw_chat_panel(self) -> None:
        """Sprint 6 PBI 6.6 + 6.7: render in-match chat panel and compose line."""

        chat_panel = pygame.Rect(70, 620, 760, 90)
        pygame.draw.rect(self.screen, BOARD_BG_COLOR, chat_panel, border_radius=10)
        pygame.draw.rect(self.screen, ACCENT_COLOR, chat_panel, width=2, border_radius=10)

        title = self.font_hint.render("Chat (Enter to send, T to type, C to cheer)", True, SUBTEXT_COLOR)
        self.screen.blit(title, (84, 628))

        y = 648
        for line in list(self.chat_messages)[-2:]:
            rendered = self.font_hint.render(line[:95], True, TEXT_COLOR)
            self.screen.blit(rendered, (84, y))
            y += 20

        compose_prefix = ">" if self.chat_typing else "(press T/Enter) >"
        compose = self.font_hint.render(f"{compose_prefix} {self.chat_input}", True, ACCENT_COLOR)
        self.screen.blit(compose, (450, 668))

    def _draw_connection_notice(self) -> None:
        """Draw non-intrusive but clear disconnect/error notice."""

        if self.connection_healthy:
            return

        panel = pygame.Rect(840, 600, 300, 90)
        pygame.draw.rect(self.screen, ERROR_PANEL_COLOR, panel, border_radius=10)
        pygame.draw.rect(self.screen, ACCENT_COLOR, panel, width=2, border_radius=10)

        title = self.font_score_label.render("Connection Issue", True, ERROR_TEXT_COLOR)
        detail_text = self.connection_notice or "Disconnected from server."
        detail = self.font_hint.render(detail_text[:56], True, ERROR_TEXT_COLOR)
        hint = self.font_hint.render("Reconnect server and relaunch client.", True, SUBTEXT_COLOR)

        self.screen.blit(title, (854, 612))
        self.screen.blit(detail, (854, 637))
        self.screen.blit(hint, (854, 660))

    def _draw_board(self) -> None:
        """Draw a 20x20 arena board with visible cell grid."""

        board_rect = pygame.Rect(
            self.board_origin_x,
            self.board_origin_y,
            self.board_pixel_width,
            self.board_pixel_height,
        )
        pygame.draw.rect(self.screen, BOARD_BG_COLOR, board_rect, border_radius=8)
        pygame.draw.rect(self.screen, ACCENT_COLOR, board_rect, width=2, border_radius=8)

        # Vertical grid lines.
        for col in range(1, BOARD_COLS):
            x = self.board_origin_x + (col * CELL_SIZE)
            pygame.draw.line(
                self.screen,
                GRID_COLOR,
                (x, self.board_origin_y),
                (x, self.board_origin_y + self.board_pixel_height),
                GRID_LINE_WIDTH,
            )

        # Horizontal grid lines.
        for row in range(1, BOARD_ROWS):
            y = self.board_origin_y + (row * CELL_SIZE)
            pygame.draw.line(
                self.screen,
                GRID_COLOR,
                (self.board_origin_x, y),
                (self.board_origin_x + self.board_pixel_width, y),
                GRID_LINE_WIDTH,
            )

    def _draw_snakes(self) -> None:
        """Render both snakes on top of the board grid."""

        if not self.has_authoritative_state:
            self._draw_single_snake(
                [(float(x), float(y)) for x, y in self.snake_a],
                SNAKE_A_BODY_COLOR,
                SNAKE_A_HEAD_COLOR,
            )
            self._draw_single_snake(
                [(float(x), float(y)) for x, y in self.snake_b],
                SNAKE_B_BODY_COLOR,
                SNAKE_B_HEAD_COLOR,
            )
            return

        self._update_interpolated_snakes()
        self._draw_single_snake(self.render_snake_a, SNAKE_A_BODY_COLOR, SNAKE_A_HEAD_COLOR)
        self._draw_single_snake(self.render_snake_b, SNAKE_B_BODY_COLOR, SNAKE_B_HEAD_COLOR)

    def _draw_single_snake(
        self,
        snake_cells: list[tuple[float, float]],
        body_color: tuple[int, int, int],
        head_color: tuple[int, int, int],
    ) -> None:
        """Draw one snake using body and head colors."""

        for index, (x, y) in enumerate(snake_cells):
            color = head_color if index == 0 else body_color
            cell_rect = pygame.Rect(
                self.board_origin_x + int(round(x * CELL_SIZE)) + 2,
                self.board_origin_y + int(round(y * CELL_SIZE)) + 2,
                CELL_SIZE - 4,
                CELL_SIZE - 4,
            )
            pygame.draw.rect(self.screen, color, cell_rect, border_radius=5)

    def _draw_pies(self) -> None:
        """Render collectible pies on the board."""

        radius = max(6, (CELL_SIZE // 2) - 4)
        for x, y in self.pies:
            center_x = self.board_origin_x + (x * CELL_SIZE) + (CELL_SIZE // 2)
            center_y = self.board_origin_y + (y * CELL_SIZE) + (CELL_SIZE // 2)
            pygame.draw.circle(self.screen, PIE_COLOR, (center_x, center_y), radius)
            pygame.draw.circle(self.screen, PIE_HIGHLIGHT_COLOR, (center_x - 4, center_y - 4), max(2, radius // 3))

    def _draw_obstacles(self) -> None:
        """Render board obstacles as blocked cells."""

        for x, y in self.obstacles:
            cell_rect = pygame.Rect(
                self.board_origin_x + (x * CELL_SIZE) + 2,
                self.board_origin_y + (y * CELL_SIZE) + 2,
                CELL_SIZE - 4,
                CELL_SIZE - 4,
            )
            pygame.draw.rect(self.screen, OBSTACLE_COLOR, cell_rect, border_radius=4)
            pygame.draw.rect(self.screen, OBSTACLE_EDGE_COLOR, cell_rect, width=1, border_radius=4)

    def _draw_health_scores(self) -> None:
        """Render health scores for both snakes."""

        score_panel = pygame.Rect(840, 120, 300, 190)
        pygame.draw.rect(self.screen, BOARD_BG_COLOR, score_panel, border_radius=10)
        pygame.draw.rect(self.screen, ACCENT_COLOR, score_panel, width=2, border_radius=10)

        title = self.font_score.render("HEALTH", True, TEXT_COLOR)
        p1_label = self.font_score_label.render(self.player_a_name, True, SNAKE_A_HEAD_COLOR)
        p1_value = self.font_score.render(str(self.snake_a_health), True, SNAKE_A_BODY_COLOR)
        p2_label = self.font_score_label.render(self.player_b_name, True, SNAKE_B_HEAD_COLOR)
        p2_value = self.font_score.render(str(self.snake_b_health), True, SNAKE_B_BODY_COLOR)

        self.screen.blit(title, (940, 136))
        self.screen.blit(p1_label, (862, 178))
        self.screen.blit(p1_value, (1070, 172))
        self.screen.blit(p2_label, (862, 225))
        self.screen.blit(p2_value, (1070, 219))

        status = self.font_hint.render(f"Server: {self.last_server_message}", True, SUBTEXT_COLOR)
        attempted = self.font_hint.render(f"Movement Attempted: {self.attempted_movement_commands}", True, SUBTEXT_COLOR)
        sent = self.font_hint.render(f"Movement Sent: {self.sent_movement_commands}  Last Sent: {self.last_sent_direction}", True, SUBTEXT_COLOR)
        last_input = self.font_hint.render(f"Last Input Direction: {self.last_input_direction}", True, SUBTEXT_COLOR)
        accepted = self.font_hint.render(f"Game States In: {self.accepted_game_states}", True, SUBTEXT_COLOR)
        rejected = self.font_hint.render(f"Movement Rejected: {self.rejected_movement_commands}", True, SUBTEXT_COLOR)
        game_meta = self.font_hint.render(f"Game: {self.active_game_id or '-'}  Status: {self.match_status}", True, SUBTEXT_COLOR)
        timer_meta = self.font_hint.render(f"Timer: remaining={self.timer_remaining}s elapsed={self.timer_elapsed}s", True, SUBTEXT_COLOR)
        winner_meta = self.font_hint.render(f"Winner: {self.match_winner}", True, SUBTEXT_COLOR)
        self.screen.blit(status, (840, 340))
        self.screen.blit(attempted, (840, 368))
        self.screen.blit(sent, (840, 396))
        self.screen.blit(last_input, (840, 424))
        self.screen.blit(accepted, (840, 452))
        self.screen.blit(rejected, (840, 480))
        self.screen.blit(game_meta, (840, 508))
        self.screen.blit(timer_meta, (840, 536))
        self.screen.blit(winner_meta, (840, 564))

    def _draw_result_screen(self) -> None:
        """PBI 4.10: render end-of-game result overlay."""

        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        card = pygame.Rect(170, 150, 660, 400)
        pygame.draw.rect(self.screen, BOARD_BG_COLOR, card, border_radius=14)
        pygame.draw.rect(self.screen, ACCENT_COLOR, card, width=3, border_radius=14)

        winner_value = "Draw" if self.result_winner.casefold() in {"none", "draw", "-"} else self.result_winner
        title = self.font_result_title.render("MATCH RESULT", True, TEXT_COLOR)
        winner = self.font_result_body.render(f"Winner: {winner_value}", True, TEXT_COLOR)
        reason = self.font_result_body.render(f"Reason: {self.result_reason}", True, SUBTEXT_COLOR)
        game_line = self.font_result_body.render(f"Game ID: {self.active_game_id or '-'}", True, SUBTEXT_COLOR)

        self.screen.blit(title, (390, 180))
        self.screen.blit(winner, (220, 245))
        self.screen.blit(reason, (220, 278))
        self.screen.blit(game_line, (220, 311))

        y = 360
        if self.result_scores:
            scores_label = self.font_result_body.render("Final Scores:", True, TEXT_COLOR)
            self.screen.blit(scores_label, (220, y))
            y += 34
            for player_name, score in self.result_scores.items():
                line = self.font_result_body.render(f"{player_name}: {score}", True, SUBTEXT_COLOR)
                self.screen.blit(line, (245, y))
                y += 30

        hint = self.font_hint.render("SPACE: hide result  |  R: lobby  |  ESC: exit", True, SUBTEXT_COLOR)
        self.screen.blit(hint, (420, 518))


def main(
    server_ip: str = "127.0.0.1",
    server_port: int = 5000,
    username: str = "Player",
    preferred_opponent: str | None = None,
    spectator_mode: bool = False,
    return_to_tk_lobby: bool = True,
    keep_window_open_on_return: bool = False,
) -> bool:
    """Entrypoint for launching the Sprint 4 Pygame client window."""

    app = PygameArenaWindow(
        server_ip=server_ip,
        server_port=server_port,
        username=username,
        preferred_opponent=preferred_opponent,
        spectator_mode=spectator_mode,
        return_to_tk_lobby=return_to_tk_lobby,
        keep_window_open_on_return=keep_window_open_on_return,
    )
    return app.run()


if __name__ == "__main__":
    main()
