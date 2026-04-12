"""Arena renderer rebuilt as a clean 2.5D tile map scene.

This version intentionally focuses on environment art only:
- no gameplay HUD
- no scores/chat text
- no characters/snake entities
- centered camera with split battlefield + castle bases
"""

from __future__ import annotations

from pathlib import Path

import pygame

WINDOW_WIDTH = 1220
WINDOW_HEIGHT = 780
WINDOW_TITLE = "Python-Arena - Arena Rebuild"
TARGET_FPS = 60

SKY_BG_COLOR = (230, 232, 236)
GRID_COLS = 20
GRID_ROWS = 9
TILE_SIZE = 52
ARENA_TOP = 255
ARENA_DIR = Path(__file__).resolve().parents[1] / "assets" / "arena"


class PygameArenaWindow:
    """Static arena scene with tile grid + castle assets."""

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

        # Keep compatibility with existing callers.
        self.server_ip = server_ip
        self.server_port = server_port
        self.username = username
        self.preferred_opponent = preferred_opponent
        self.spectator_mode = spectator_mode
        self.return_to_tk_lobby = return_to_tk_lobby
        self.keep_window_open_on_return = keep_window_open_on_return

        self.arena_w = GRID_COLS * TILE_SIZE
        self.arena_h = GRID_ROWS * TILE_SIZE
        self.arena_x = (WINDOW_WIDTH - self.arena_w) // 2
        self.arena_y = ARENA_TOP
        self.split_col = GRID_COLS // 2

        self.castle_wall = self._load_castle_wall("castle_wall.png")

    def _load_castle_wall(self, filename: str) -> pygame.Surface | None:
        """Load unified top wall and strip uniform backdrop to transparency."""

        path = ARENA_DIR / filename
        if not path.exists():
            return None
        try:
            src = pygame.image.load(str(path)).convert_alpha()
        except pygame.error:
            return None

        return self._remove_wall_backdrop(src)

    def _remove_wall_backdrop(self, src: pygame.Surface) -> pygame.Surface:
        """Remove flat background by color-distance from corner samples.

        Keeps castle structures intact while turning backdrop fully transparent.
        """

        w, h = src.get_size()
        out = src.copy()

        # Estimate backdrop color from corners.
        corners = [
            src.get_at((0, 0)),
            src.get_at((w - 1, 0)),
            src.get_at((0, h - 1)),
            src.get_at((w - 1, h - 1)),
        ]
        bg_r = sum(c.r for c in corners) // 4
        bg_g = sum(c.g for c in corners) // 4
        bg_b = sum(c.b for c in corners) // 4

        # Tight threshold to avoid eating dark castle details.
        hard_cut = 22
        soft_cut = 34

        for y in range(h):
            for x in range(w):
                c = out.get_at((x, y))
                dr = c.r - bg_r
                dg = c.g - bg_g
                db = c.b - bg_b
                dist = (dr * dr + dg * dg + db * db) ** 0.5

                if dist <= hard_cut:
                    out.set_at((x, y), (c.r, c.g, c.b, 0))
                elif dist <= soft_cut:
                    # Feather edge to avoid halos.
                    t = (dist - hard_cut) / max(1.0, (soft_cut - hard_cut))
                    alpha = int(255 * t)
                    out.set_at((x, y), (c.r, c.g, c.b, alpha))
                else:
                    out.set_at((x, y), (c.r, c.g, c.b, 255))

        # Trim transparent margins so castle fills width cleanly after scaling.
        rect = out.get_bounding_rect(min_alpha=8)
        if rect.width > 0 and rect.height > 0:
            trimmed = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            trimmed.blit(out, (0, 0), rect)
            return trimmed
        return out

    def run(self) -> bool:
        """Run loop and render only arena environment."""

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

    def _draw_tile(self, x: int, y: int, left_side: bool) -> None:
        """Draw one seamless tile with soft lighting, depth, and ultra-subtle texture."""

        if left_side:
            fill = (58, 206, 118)
            scale_light = (116, 236, 170, 14)
            scale_dark = (34, 132, 86, 10)
            light_col = (210, 255, 230, 18)
            shade_col = (18, 74, 54, 20)
            inner_shadow_col = (22, 88, 64, 18)
        else:
            # Warm sand/red-biome side to match red castle theme.
            fill = (224, 206, 166)
            scale_light = (238, 222, 192, 12)
            scale_dark = (170, 134, 102, 10)
            light_col = (255, 246, 224, 16)
            shade_col = (136, 108, 78, 18)
            inner_shadow_col = (144, 114, 82, 16)

        rect = pygame.Rect(x, y, TILE_SIZE, TILE_SIZE)
        # Gentle organic color variation per tile.
        tx = x // TILE_SIZE
        ty = y // TILE_SIZE
        tile_hash = (tx * 73856093) ^ (ty * 19349663)
        var = ((tile_hash & 7) - 3)  # -3..+4
        fill_var = (
            max(0, min(255, fill[0] + var)),
            max(0, min(255, fill[1] + var)),
            max(0, min(255, fill[2] + var)),
        )

        # Soft ambient under-shadow for depth separation (no hard borders).
        shadow = pygame.Surface((TILE_SIZE + 4, TILE_SIZE + 4), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 10), pygame.Rect(1, 2, TILE_SIZE, TILE_SIZE), border_radius=5)
        self.screen.blit(shadow, (rect.x - 2, rect.y - 1))

        pygame.draw.rect(self.screen, fill_var, rect)

        # Local matte lighting gradient (very soft); global pass is applied later.
        grad = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
        for i in range(TILE_SIZE):
            top_alpha = max(0, 8 - (i // 6))
            left_alpha = max(0, 7 - (i // 7))
            bottom_alpha = max(0, 7 - ((TILE_SIZE - i) // 6))
            pygame.draw.line(grad, (light_col[0], light_col[1], light_col[2], min(light_col[3], top_alpha)), (0, i), (TILE_SIZE, i), 1)
            pygame.draw.line(grad, (light_col[0], light_col[1], light_col[2], min(light_col[3], left_alpha)), (i, 0), (i, TILE_SIZE), 1)
            pygame.draw.line(grad, (shade_col[0], shade_col[1], shade_col[2], min(shade_col[3], bottom_alpha)), (0, i), (TILE_SIZE, i), 1)
        self.screen.blit(grad, (rect.x, rect.y))

        # Slight bevel/inner shadow (extremely low contrast, no hard outlines).
        pygame.draw.line(self.screen, light_col, (rect.left + 1, rect.top + 1), (rect.right - 2, rect.top + 1), 1)
        pygame.draw.line(self.screen, inner_shadow_col, (rect.left + 1, rect.bottom - 2), (rect.right - 2, rect.bottom - 2), 1)

        # Very subtle, larger snake-scale texture (irregular, sparse, non-aligned).
        overlay = pygame.Surface((TILE_SIZE, TILE_SIZE), pygame.SRCALPHA)
        scale_w = 15 + (tile_hash & 1)
        scale_h = 9 + ((tile_hash >> 1) & 1)
        row_step = 14 + ((tile_hash >> 2) & 1)
        x_jitter = ((tile_hash >> 3) & 3) - 1
        y_jitter = ((tile_hash >> 5) & 3) - 1
        for row_i, sy in enumerate(range(9 + y_jitter, TILE_SIZE - 6, row_step)):
            offset = (row_i % 2) * (scale_w // 2)
            start_x = 6 + offset + x_jitter
            for sx in range(start_x, TILE_SIZE - 6, scale_w + 4):
                density_gate = ((sx + sy + tile_hash) & 3)
                if density_gate == 0:
                    continue
                srect = pygame.Rect(sx, sy, scale_w, scale_h)
                pygame.draw.ellipse(overlay, scale_light, srect)
                pygame.draw.arc(overlay, scale_dark, srect, 0.25, 2.9, 1)

        # Orientation variation per tile to avoid visible global diagonals.
        orient = (tile_hash >> 7) & 3
        if orient == 1:
            overlay = pygame.transform.flip(overlay, True, False)
        elif orient == 2:
            overlay = pygame.transform.flip(overlay, False, True)
        elif orient == 3:
            overlay = pygame.transform.flip(overlay, True, True)

        # Soft blend so texture does not overpower gameplay readability.
        self.screen.blit(overlay, (rect.x, rect.y))

    def _draw_bottom_tile_faces(self) -> None:
        """Draw per-tile dirt front faces for bottom-row floating-island thickness."""

        face_h = 18
        top_y = self.arena_y + self.arena_h
        for col in range(GRID_COLS):
            x = self.arena_x + col * TILE_SIZE
            face = pygame.Rect(x, top_y, TILE_SIZE, face_h)
            pygame.draw.rect(self.screen, (194, 132, 76), face)
            pygame.draw.line(self.screen, (214, 156, 96), (face.left, face.top), (face.right - 1, face.top), 1)
            pygame.draw.line(self.screen, (168, 108, 58), (face.left, face.bottom - 1), (face.right - 1, face.bottom - 1), 1)
            # Vertical seam so each bottom tile has its own attached front face.
            pygame.draw.line(self.screen, (174, 116, 66), (face.left, face.top), (face.left, face.bottom - 1), 1)
            if col == GRID_COLS - 1:
                pygame.draw.line(self.screen, (174, 116, 66), (face.right - 1, face.top), (face.right - 1, face.bottom - 1), 1)
            # Tiny speckles.
            pygame.draw.circle(self.screen, (162, 102, 56), (x + 12, top_y + 10), 1)
            pygame.draw.circle(self.screen, (162, 102, 56), (x + 32, top_y + 7), 1)

    def _draw_castles(self) -> None:
        """Draw one unified castle wall spanning full arena width above the grid."""

        if self.castle_wall is None:
            return

        src_w, src_h = self.castle_wall.get_size()
        if src_w <= 0 or src_h <= 0:
            return

        # Fit strategy:
        # 1) Target full arena width.
        # 2) If too tall for available space above row 1, scale down to fit height.
        #    (never crop, never overlap row 1).
        max_h = max(1, self.arena_y)
        scaled_w = self.arena_w
        scaled_h = max(1, int((src_h / src_w) * scaled_w))

        if scaled_h > max_h:
            s = max_h / max(1, scaled_h)
            scaled_h = max(1, int(scaled_h * s))
            scaled_w = max(1, int(scaled_w * s))

        wall = pygame.transform.smoothscale(self.castle_wall, (scaled_w, scaled_h))

        # Bottom edge aligns exactly with top edge of first tile row.
        x = self.arena_x + (self.arena_w - scaled_w) // 2
        y = self.arena_y - scaled_h
        self.screen.blit(wall, (x, y))

    def _draw_frame(self) -> None:
        """Render complete environment scene."""

        self.screen.fill(SKY_BG_COLOR)

        # Tile grid (clear, readable, symmetrical split).
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                x = self.arena_x + col * TILE_SIZE
                y = self.arena_y + row * TILE_SIZE
                self._draw_tile(x, y, left_side=(col < self.split_col))

        # Global top-left directional light/shadow pass (smooth, non-banded).
        global_light = pygame.Surface((self.arena_w, self.arena_h), pygame.SRCALPHA)
        for i in range(self.arena_h):
            a = max(0, 20 - (i // 22))
            pygame.draw.line(global_light, (255, 255, 255, a), (0, i), (self.arena_w, i), 1)
        for i in range(self.arena_w):
            a = max(0, 16 - (i // 28))
            pygame.draw.line(global_light, (255, 255, 255, a), (i, 0), (i, self.arena_h), 1)
        self.screen.blit(global_light, (self.arena_x, self.arena_y))

        global_shadow = pygame.Surface((self.arena_w, self.arena_h), pygame.SRCALPHA)
        for i in range(self.arena_h):
            a = max(0, 16 - ((self.arena_h - i) // 24))
            pygame.draw.line(global_shadow, (0, 0, 0, a), (0, i), (self.arena_w, i), 1)
        self.screen.blit(global_shadow, (self.arena_x, self.arena_y))

        self._draw_bottom_tile_faces()
        self._draw_castles()


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
