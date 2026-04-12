"""Fresh game window scaffold.

This file is intentionally minimal so arena rendering can be rebuilt from scratch
without losing the previous implementation (saved as game_window_old.py).
"""

from __future__ import annotations

import pygame

WINDOW_WIDTH = 1220
WINDOW_HEIGHT = 780
WINDOW_TITLE = "Python-Arena - Arena Rebuild"
TARGET_FPS = 60
BG_COLOR = (14, 24, 34)
TEXT_COLOR = (190, 232, 220)
SUBTEXT_COLOR = (108, 170, 156)


class PygameArenaWindow:
    """Minimal, safe arena window scaffold for rebuild work."""

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

        # Keep ctor compatibility with existing callers.
        self.server_ip = server_ip
        self.server_port = server_port
        self.username = username
        self.preferred_opponent = preferred_opponent
        self.spectator_mode = spectator_mode
        self.return_to_tk_lobby = return_to_tk_lobby
        self.keep_window_open_on_return = keep_window_open_on_return

        self.font_title = pygame.font.SysFont("consolas", 34, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 20)

    def run(self) -> bool:
        """Run minimal loop. Returns False to preserve old contract shape."""

        while self.running:
            self._handle_events()
            self._draw_frame()
            pygame.display.flip()
            self.clock.tick(TARGET_FPS)

        pygame.quit()
        return False

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.running = False

    def _draw_frame(self) -> None:
        self.screen.fill(BG_COLOR)
        title = self.font_title.render("PYTHON ARENA - REBUILD MODE", True, TEXT_COLOR)
        hint = self.font_body.render("Old implementation saved as client/game_window_old.py", True, SUBTEXT_COLOR)
        hint2 = self.font_body.render("Press ESC to exit. Start building your new arena here.", True, SUBTEXT_COLOR)
        self.screen.blit(title, (120, 120))
        self.screen.blit(hint, (120, 180))
        self.screen.blit(hint2, (120, 212))


def main(
    server_ip: str = "127.0.0.1",
    server_port: int = 5000,
    username: str = "Player",
    preferred_opponent: str | None = None,
    spectator_mode: bool = False,
    return_to_tk_lobby: bool = True,
    keep_window_open_on_return: bool = False,
) -> bool:
    """Entrypoint kept compatible with existing callers."""

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
