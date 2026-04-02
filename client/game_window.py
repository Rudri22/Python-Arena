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

import pygame

from client.network import ClientConnection
from shared.protocol import (
    MessageType,
    make_invitation_message,
    make_movement_message,
    make_username_message,
)

WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 700
WINDOW_TITLE = "Python-Arena - Sprint 4"
TARGET_FPS = 60
MOVE_INTERVAL_MS = 140
BOARD_COLS = 20
BOARD_ROWS = 20
CELL_SIZE = 24
GRID_LINE_WIDTH = 1
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

    def __init__(self, server_ip: str, server_port: int, username: str) -> None:
        pygame.init()
        pygame.display.set_caption(WINDOW_TITLE)
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.clock = pygame.time.Clock()
        self.running = True
        self.server_ip = server_ip
        self.server_port = server_port
        self.username = username
        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.incoming_queue: queue.Queue[dict] = queue.Queue()
        self.username_confirmed = False
        self.online_users: list[str] = []
        self.pending_invite_to: str | None = None
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
        self.player_a_name = "Snake A"
        self.player_b_name = "Snake B"
        self.last_game_state_ms = 0
        self.show_result_screen = False
        self.result_winner = "-"
        self.result_reason = "-"
        self.result_scores: dict[str, int] = {}
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
        self.snake_a_direction = "right"
        self.snake_b_direction = "left"
        self.snake_a_health = 100
        self.snake_b_health = 100
        # PBI 4.4 baseline pie rendering.
        self.pies: list[tuple[int, int]] = [(7, 5), (12, 14)]
        # PBI 4.5 baseline obstacle rendering.
        self.obstacles: list[tuple[int, int]] = [(9, 7), (10, 7), (9, 12), (10, 12), (5, 10), (14, 10)]
        self.last_move_ms = pygame.time.get_ticks()
        self._connect_to_server()

    def run(self) -> None:
        """Start the main window loop until user exits."""

        while self.running:
            self._handle_events()
            self._drain_incoming_queue()
            self._update_movement()
            self._draw_frame()
            pygame.display.flip()
            self.clock.tick(TARGET_FPS)

        self._disconnect_from_server()
        pygame.quit()

    def _handle_events(self) -> None:
        """Process window close and keyboard exit events."""

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_SPACE:
                    self.show_result_screen = False
                self._handle_direction_input(event.key)

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
            self.last_server_message = f"Offline mode ({error})"
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
                        "type": MessageType.ERROR.value,
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
            self._maybe_send_auto_invite()
            return

        if msg_type == MessageType.INVITATION.value:
            action = str(payload.get("action", "")).lower()
            from_user = str(payload.get("from_user", ""))
            to_user = str(payload.get("to_user", ""))
            if action == "send" and to_user.casefold() == self.username.casefold():
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
                self.last_server_message = f"Match started ({self.active_game_id})"
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
            return

    def _maybe_send_auto_invite(self) -> None:
        """Auto-invite one available opponent to bootstrap a playable match."""

        if self.connection is None or not self.username_confirmed or self.active_game_id is not None:
            return
        if self.pending_invite_to is not None:
            return

        opponents = [user for user in self.online_users if user.casefold() != self.username.casefold()]
        if not opponents:
            return

        opponent = opponents[0]
        self.connection.send_message(
            make_invitation_message(
                from_user=self.username,
                to_user=opponent,
                action="send",
            )
        )
        self.pending_invite_to = opponent
        self.last_server_message = f"Invited {opponent}"

    def _sync_from_game_state(self, state: dict) -> None:
        """Update rendered board entities from authoritative backend state."""

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

    def _update_movement(self) -> None:
        """Send movement input at a fixed interval (server-authoritative rendering)."""

        now = pygame.time.get_ticks()
        if now - self.last_move_ms < MOVE_INTERVAL_MS:
            return

        self.last_move_ms = now
        # Preview movement before matchmaking completes so controls feel responsive.
        if self.active_game_id is None:
            self._step_preview_snake(self.snake_a, self.snake_a_direction)
            self._step_preview_snake(self.snake_b, self.snake_b_direction)
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
        except OSError:
            self.last_server_message = "Failed to send movement"

    def _step_preview_snake(self, snake_cells: list[tuple[int, int]], direction: str) -> None:
        """Move one snake locally for pre-match visual feedback."""

        dx, dy = _DIRECTIONS[direction]
        head_x, head_y = snake_cells[0]
        snake_cells.insert(0, ((head_x + dx) % BOARD_COLS, (head_y + dy) % BOARD_ROWS))
        snake_cells.pop()

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
        self.screen.blit(body, (100, 640))
        self.screen.blit(hint, (730, 640))

        self._draw_board()
        self._draw_obstacles()
        self._draw_pies()
        self._draw_snakes()
        self._draw_health_scores()
        if self.show_result_screen:
            self._draw_result_screen()

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

        self._draw_single_snake(self.snake_a, SNAKE_A_BODY_COLOR, SNAKE_A_HEAD_COLOR)
        self._draw_single_snake(self.snake_b, SNAKE_B_BODY_COLOR, SNAKE_B_HEAD_COLOR)

    def _draw_single_snake(
        self,
        snake_cells: list[tuple[int, int]],
        body_color: tuple[int, int, int],
        head_color: tuple[int, int, int],
    ) -> None:
        """Draw one snake using body and head colors."""

        for index, (x, y) in enumerate(snake_cells):
            color = head_color if index == 0 else body_color
            cell_rect = pygame.Rect(
                self.board_origin_x + (x * CELL_SIZE) + 2,
                self.board_origin_y + (y * CELL_SIZE) + 2,
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

        score_panel = pygame.Rect(620, 120, 230, 160)
        pygame.draw.rect(self.screen, BOARD_BG_COLOR, score_panel, border_radius=10)
        pygame.draw.rect(self.screen, ACCENT_COLOR, score_panel, width=2, border_radius=10)

        title = self.font_score.render("HEALTH", True, TEXT_COLOR)
        p1_label = self.font_score_label.render(self.player_a_name, True, SNAKE_A_HEAD_COLOR)
        p1_value = self.font_score.render(str(self.snake_a_health), True, SNAKE_A_BODY_COLOR)
        p2_label = self.font_score_label.render(self.player_b_name, True, SNAKE_B_HEAD_COLOR)
        p2_value = self.font_score.render(str(self.snake_b_health), True, SNAKE_B_BODY_COLOR)

        self.screen.blit(title, (690, 136))
        self.screen.blit(p1_label, (642, 178))
        self.screen.blit(p1_value, (770, 172))
        self.screen.blit(p2_label, (642, 225))
        self.screen.blit(p2_value, (770, 219))

        status = self.font_hint.render(f"Server: {self.last_server_message}", True, SUBTEXT_COLOR)
        attempted = self.font_hint.render(f"Movement Attempted: {self.attempted_movement_commands}", True, SUBTEXT_COLOR)
        sent = self.font_hint.render(f"Movement Sent: {self.sent_movement_commands}  Last Sent: {self.last_sent_direction}", True, SUBTEXT_COLOR)
        last_input = self.font_hint.render(f"Last Input Direction: {self.last_input_direction}", True, SUBTEXT_COLOR)
        accepted = self.font_hint.render(f"Game States In: {self.accepted_game_states}", True, SUBTEXT_COLOR)
        rejected = self.font_hint.render(f"Movement Rejected: {self.rejected_movement_commands}", True, SUBTEXT_COLOR)
        game_meta = self.font_hint.render(f"Game: {self.active_game_id or '-'}  Status: {self.match_status}", True, SUBTEXT_COLOR)
        timer_meta = self.font_hint.render(f"Timer: remaining={self.timer_remaining}s elapsed={self.timer_elapsed}s", True, SUBTEXT_COLOR)
        winner_meta = self.font_hint.render(f"Winner: {self.match_winner}", True, SUBTEXT_COLOR)
        self.screen.blit(status, (620, 300))
        self.screen.blit(attempted, (620, 328))
        self.screen.blit(sent, (620, 356))
        self.screen.blit(last_input, (620, 384))
        self.screen.blit(accepted, (620, 412))
        self.screen.blit(rejected, (620, 440))
        self.screen.blit(game_meta, (620, 468))
        self.screen.blit(timer_meta, (620, 496))
        self.screen.blit(winner_meta, (620, 524))

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

        hint = self.font_hint.render("SPACE: hide result  |  ESC: exit", True, SUBTEXT_COLOR)
        self.screen.blit(hint, (420, 518))


def main(server_ip: str = "127.0.0.1", server_port: int = 5000, username: str = "Player") -> None:
    """Entrypoint for launching the Sprint 4 Pygame client window."""

    app = PygameArenaWindow(server_ip=server_ip, server_port=server_port, username=username)
    app.run()


if __name__ == "__main__":
    main()
