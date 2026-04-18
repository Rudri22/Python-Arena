"""Arena renderer rebuilt as a clean 2.5D tile map scene.

This version intentionally focuses on environment art only:
- no gameplay HUD
- no scores/chat text
- no characters/snake entities
- centered camera with split battlefield + castle bases
"""

from __future__ import annotations

import math
import random
from collections import deque
from pathlib import Path

import pygame

import queue
import threading

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
WINDOW_TITLE = "Python-Arena - Arena Rebuild"
TARGET_FPS = 60

SKY_BG_COLOR = (94, 98, 108)
GRID_COLS = 20
GRID_ROWS = 9
TILE_SIZE = 52
SIDE_EXTENSION_ROWS = 4
SIDE_EXTENSION_TILES = 5
ARENA_TOP = 255
ARENA_DIR = Path(__file__).resolve().parents[1] / "assets" / "arena"
TERRAIN_DIR = Path(__file__).resolve().parents[1] / "assets" / "terrain"

# ── Gameplay grid (mirrors server/game_engine.py) ──────────────────────────
GAME_BOARD_COLS = 20
GAME_BOARD_ROWS = 20
# Cell dimensions — X aligns with visual tiles; Y covers main arena + side wings
GAME_CELL_W = float(GRID_COLS * TILE_SIZE) / GAME_BOARD_COLS                      # 52.0 px
GAME_CELL_H = float((GRID_ROWS + SIDE_EXTENSION_ROWS) * TILE_SIZE) / GAME_BOARD_ROWS  # 33.8 px

# The centre gap where the serpent lives (unwalkable for player snakes).
# Expressed in game-grid columns and the number of rows that fall inside the wings.
GAP_COL_START = SIDE_EXTENSION_TILES                      # col 5
GAP_COL_END   = GRID_COLS - SIDE_EXTENSION_TILES          # col 15
GAP_ROW_END   = int(SIDE_EXTENSION_ROWS * TILE_SIZE / GAME_CELL_H) + 1  # ≈ row 7
BASE_SNAKE_LENGTH = 3
LOCAL_INITIAL_HEALTH = 1000
HEALTH_PER_GROWTH_SEGMENT = 10
PIE_HEALTH_GAIN = 15
OBSTACLE_COLLISION_DAMAGE = 20
SNAKE_COLLISION_DAMAGE = 20
MOVE_INTERVAL_MS = 120
STATE_INTERPOLATION_MS = 120
# Static obstacle positions matching game_engine.py (game-grid coords)
STATIC_OBSTACLES_GAME: tuple[tuple[int, int], ...] = (
    (9, 7), (10, 7), (9, 12), (10, 12), (5, 10), (14, 10),
)
# Snake palette
SNAKE_A_BODY = (50, 215, 90)
SNAKE_A_HEAD = (20, 160, 50)
SNAKE_A_GLOW = (30, 240, 80, 90)
SNAKE_B_BODY = (255, 175, 35)
SNAKE_B_HEAD = (220, 125, 15)
SNAKE_B_GLOW = (255, 200, 60, 90)
BERRY_COLOR  = (200, 255, 100)
# HUD palette
HUD_BG       = (12, 16, 24)
HUD_TEXT     = (230, 240, 248)
HUD_DIM      = (130, 155, 180)
HUD_ACCENT   = (80, 200, 255)
HUD_HEALTH_A = (50, 215, 90)
HUD_HEALTH_B = (255, 175, 35)
HUD_DANGER   = (220, 60, 60)

_DIRECTIONS: dict[str, tuple[int, int]] = {
    "up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
}
_OPPOSITE_DIRECTION: dict[str, str] = {
    "up": "down", "down": "up", "left": "right", "right": "left",
}


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

        self.grid_cols = GRID_COLS
        self.arena_w = self.grid_cols * TILE_SIZE
        self.arena_h = GRID_ROWS * TILE_SIZE
        self.arena_x = (WINDOW_WIDTH - self.arena_w) // 2
        self.arena_y = ARENA_TOP
        self.split_col = self.grid_cols // 2
        self.castle_wall = self._load_castle_wall("castle_wall.png")
        self.inner_land_texture = self._normalize_tile_color(
            self._load_terrain_tile_texture("inner land.png"))
        self.border_land_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_terrain_tile_texture("border land.png")))
        self.left_corner_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_terrain_tile_texture("left corner (2).png", crop_anchor="left_bottom")))
        # Mirror the left corner for a matching right corner curve.
        self.right_corner_texture = (
            pygame.transform.flip(self.left_corner_texture, True, False)
            if self.left_corner_texture else None
        )
        self.left_of_castle_corner_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_first_terrain_texture(
                ["leftofcastlecorner(2).png", "leftofcastlecorner (2).png", "leftofcastlecorner.png"],
                crop_anchor="left_bottom",
            )))
        self.right_of_castle_corner_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_first_terrain_texture(
                ["rightofcastlecorner(2).png", "rightofcastlecorner (2).png", "rightofcastlecorner.png"],
                crop_anchor="right_bottom",
            )))
        self.top_left_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_terrain_tile_texture("topleft.png", crop_anchor="left_top")))
        self.top_right_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_terrain_tile_texture("topright.png", crop_anchor="right_top")))
        self.left_side_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_terrain_tile_texture("leftside.png", crop_anchor="left_bottom")))
        # Mirror left side for a matching right side edge profile.
        self.right_side_texture = (
            pygame.transform.flip(self.left_side_texture, True, False)
            if self.left_side_texture else None
        )
        self.top_side_texture = self._normalize_tile_color(self._make_black_transparent(
            self._load_terrain_tile_texture("top side.png", crop_anchor="center")))
        self.castle_tile_texture = self._build_castle_tile_texture(self.castle_wall)
        self.lava_frames = self._load_lava_frames()
        self.lava_rock_frames = self._load_lava_rock_frames()

        # Agent-based pool snakes: each has a real 2-D head position, a heading
        # angle that wanders freely, and a position-history deque so body segments
        # trail the head along the actual path (no fixed lane or strip).
        _rng_s = random.Random(17)
        _HIST = 300

        def _mk(x: float, y: float, ang: float, spd: float, ln: int, sc: float, ph: float) -> dict:
            return {
                "x": x, "y": y,
                "angle": ang, "target_angle": ang,
                "speed": spd, "length": ln, "scale": sc, "phase": ph,
                "turn_rate": _rng_s.uniform(1.0, 2.0),
                "turn_timer": _rng_s.uniform(0.5, 2.0),
                "history": deque([(x, y)] * _HIST, maxlen=_HIST),
            }

        self.snakes = [
            # Left strip area
            _mk(45.0,  200.0,  math.pi * 0.5,  62.0, 13, 0.95, 0.0),
            _mk(68.0,  500.0,  math.pi * 1.5,  55.0, 11, 0.82, 1.5),
            _mk(30.0,  700.0,  math.pi * 0.35, 48.0,  9, 0.72, 2.7),
            # Right strip area
            _mk(1175.0, 200.0, math.pi * 0.5,  58.0, 12, 0.90, 0.8),
            _mk(1155.0, 480.0, math.pi * 1.7,  65.0, 14, 1.00, 1.9),
            _mk(1185.0, 680.0, math.pi * 1.5,  52.0, 10, 0.78, 3.4),
            # Bottom strip area
            _mk(300.0,  750.0, 0.05,           70.0, 11, 0.86, 0.3),
            _mk(850.0,  750.0, math.pi,        60.0, 12, 0.92, 2.4),
        ]
        del _mk, _rng_s

        # Poison drip particles falling from the arch serpent.
        rng = random.Random(42)
        self._poison_drips = [
            {
                "t":        rng.uniform(0.05, 0.95),
                "y_offset": rng.uniform(0, 100),
                "vy":       rng.uniform(0.4, 1.4),
                "alpha":    rng.randint(130, 220),
                "size":     rng.randint(3, 6),
            }
            for _ in range(24)
        ]

        # Toxic-spike obstacles on the arena - clearly hazardous tiles to avoid.
        # Each obstacle occupies a single tile (col, row) on the main grid.
        self.obstacles = [
            {"col": 3,  "row": 1, "phase": 0.0},
            {"col": 16, "row": 1, "phase": 0.7},
            {"col": 3,  "row": 7, "phase": 1.4},
            {"col": 16, "row": 7, "phase": 2.1},
            {"col": 7,  "row": 4, "phase": 2.8},
            {"col": 12, "row": 4, "phase": 3.5},
        ]

        # Big serpent throwing state machine.
        # IDLE -> WIND_UP -> THROW (blob spawns) -> RECOVER -> IDLE, every ~5s.
        now_ms = pygame.time.get_ticks()
        self._throw_state = "idle"
        self._throw_state_start = now_ms
        self._next_throw_time = now_ms + 2500  # first throw 2.5s after start
        self._throw_blob_spawned = False
        self._poison_blobs: list[dict] = []

        # Big serpent agent — navigates the gap above the arena.
        _ext_h = SIDE_EXTENSION_ROWS * TILE_SIZE
        _gx1 = float(self.arena_x + SIDE_EXTENSION_TILES * TILE_SIZE + 56)
        _gx2 = float(self.arena_x + self.arena_w - SIDE_EXTENSION_TILES * TILE_SIZE - 56)
        _gy1 = float(self.arena_y - _ext_h + 24)
        _gy2 = float(self.arena_y - 20)
        _sx  = (_gx1 + _gx2) * 0.5
        _sy  = (_gy1 + _gy2) * 0.5
        self._serpent_agent: dict = {
            "x": _sx, "y": _sy,
            "angle": 0.25, "target_angle": 0.25,
            "speed": 58.0, "turn_rate": 0.85, "turn_timer": 2.2,
            "history": deque([(_sx, _sy)] * 650, maxlen=650),
        }

        # Frame timing for delta-time-based motion (snakes, blobs).
        self._last_tick_ms = now_ms
        self._step_dt = 1.0 / TARGET_FPS

        # ── Gameplay networking ────────────────────────────────────────
        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.incoming_queue: queue.Queue[dict] = queue.Queue()
        self.username_confirmed = False
        self.online_users: list[str] = []
        self.pending_invite_to: str | None = None
        self.pending_invite_sent_ms = 0
        self.pending_spectate_to: str | None = None
        self.active_game_id: str | None = None
        self.has_authoritative_state = False
        self.match_status = "waiting"
        self.match_winner = "-"
        self.timer_remaining = 0
        self.timer_elapsed = 0
        self.connection_healthy = True
        self.connection_notice = ""
        self.player_a_name = username
        self.player_b_name = "Opponent"
        self.last_server_message = "Connecting..."
        self.last_move_ms = now_ms

        # ── Game entity state ──────────────────────────────────────────
        self.snake_a: list[tuple[int, int]] = [(3, 9), (2, 9), (1, 9)]
        self.snake_b: list[tuple[int, int]] = [(16, 11), (17, 11), (18, 11)]
        self.snake_a_direction = "right"
        self.snake_b_direction = "left"
        self.snake_a_health = LOCAL_INITIAL_HEALTH
        self.snake_b_health = LOCAL_INITIAL_HEALTH
        self.game_pies: list[tuple[int, int]] = []
        self.game_obstacles: list[tuple[int, int]] = []

        # ── Interpolation ──────────────────────────────────────────────
        self.render_snake_a: list[tuple[float, float]] = [(float(x), float(y)) for x, y in self.snake_a]
        self.render_snake_b: list[tuple[float, float]] = [(float(x), float(y)) for x, y in self.snake_b]
        self.interp_from_snake_a: list[tuple[float, float]] = []
        self.interp_from_snake_b: list[tuple[float, float]] = []
        self.interp_to_snake_a: list[tuple[float, float]] = []
        self.interp_to_snake_b: list[tuple[float, float]] = []
        self.state_interp_start_ms = 0

        # ── Result + chat ──────────────────────────────────────────────
        self.show_result_screen = False
        self.result_winner = "-"
        self.result_reason = "-"
        self.result_scores: dict[str, int] = {}
        self.return_to_lobby_requested = False
        self.chat_messages: deque[str] = deque(maxlen=6)
        self.chat_input = ""
        self.chat_typing = False
        self.cheer_pulse_until_ms = 0

        # ── Fonts ──────────────────────────────────────────────────────
        self.font_hud       = pygame.font.SysFont("consolas", 22, bold=True)
        self.font_hud_sm    = pygame.font.SysFont("consolas", 16)
        self.font_result    = pygame.font.SysFont("consolas", 34, bold=True)
        self.font_result_body = pygame.font.SysFont("consolas", 21)
        self.font_result_title = pygame.font.SysFont("consolas", 54, bold=True)
        self.font_result_name  = pygame.font.SysFont("consolas", 22, bold=True)
        self.font_result_hp    = pygame.font.SysFont("consolas", 20)

        # ── Game rendering area — aligned exactly to the visual island tiles ─
        # x-cells are 52 px wide (= TILE_SIZE), y-cells are 23.4 px tall so
        # all 20 server rows fit within the 9-tile-high arena rectangle.
        self.game_origin_x = self.arena_x
        self.game_origin_y = self.arena_y - SIDE_EXTENSION_ROWS * TILE_SIZE

        self._reset_local_round_layout()
        self._connect_to_server()

    # ------------------------------------------------------------------
    # Castle wall loading (kept for castle_tile_texture; never drawn)
    # ------------------------------------------------------------------

    def _load_castle_wall(self, filename: str) -> pygame.Surface | None:
        """Load unified top wall and strip uniform backdrop to transparency."""

        path = ARENA_DIR / filename
        if not path.exists():
            return None
        try:
            src = pygame.image.load(str(path)).convert_alpha()
        except pygame.error:
            return None

        cleaned = self._remove_wall_backdrop(src)
        w, h = cleaned.get_size()
        outside = [[False] * w for _ in range(h)]
        q: deque[tuple[int, int]] = deque()

        def _push_if_outside(x: int, y: int) -> None:
            if outside[y][x]:
                return
            if cleaned.get_at((x, y)).a != 0:
                return
            outside[y][x] = True
            q.append((x, y))

        for x in range(w):
            _push_if_outside(x, 0)
            _push_if_outside(x, h - 1)
        for y in range(h):
            _push_if_outside(0, y)
            _push_if_outside(w - 1, y)

        while q:
            x, y = q.popleft()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    _push_if_outside(nx, ny)

        opaque = pygame.Surface((w, h), pygame.SRCALPHA)
        filler = (44, 44, 52, 255)
        for y in range(h):
            for x in range(w):
                r, g, b, a = cleaned.get_at((x, y))
                if outside[y][x]:
                    continue
                if a > 0:
                    opaque.set_at((x, y), (r, g, b, 255))
                else:
                    opaque.set_at((x, y), filler)

        green_eyes: list[tuple[int, int]] = []
        red_eyes: list[tuple[int, int]] = []
        for y in range(h):
            for x in range(w):
                r, g, b, a = opaque.get_at((x, y))
                if a < 8:
                    continue
                if g > 70 and (g - r) > 20 and (g - b) > 16:
                    green_eyes.append((x, y))
                elif r > 90 and (r - g) > 25 and (r - b) > 18:
                    red_eyes.append((x, y))

        tinted = pygame.Surface((w, h), pygame.SRCALPHA)
        tint_r, tint_g, tint_b = 82, 48, 66
        for y in range(h):
            for x in range(w):
                r, g, b, a = opaque.get_at((x, y))
                if a == 0:
                    continue
                luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
                t = (luma / 160.0) ** 0.65
                nr = min(255, int(tint_r * t * 1.8 + 12))
                ng = min(255, int(tint_g * t * 1.8 + 8))
                nb = min(255, int(tint_b * t * 1.8 + 10))
                tinted.set_at((x, y), (nr, ng, nb, a))

        for ex, ey in green_eyes:
            tinted.set_at((ex, ey), (34, 255, 85, 255))
        for ex, ey in red_eyes:
            tinted.set_at((ex, ey), (255, 51, 51, 255))
        glow = pygame.Surface((w, h), pygame.SRCALPHA)
        for ex, ey in green_eyes:
            pygame.draw.circle(glow, (0, 255, 68, 56), (ex, ey), 5)
            pygame.draw.circle(glow, (0, 255, 68, 26), (ex, ey), 8)
        for ex, ey in red_eyes:
            pygame.draw.circle(glow, (255, 34, 34, 58), (ex, ey), 5)
            pygame.draw.circle(glow, (255, 34, 34, 28), (ex, ey), 8)
        tinted.blit(glow, (0, 0))

        return tinted

    def _remove_wall_backdrop(self, src: pygame.Surface) -> pygame.Surface:
        """Remove flat background by color-distance from corner samples."""

        w, h = src.get_size()
        out = src.copy()

        corners = [
            src.get_at((0, 0)),
            src.get_at((w - 1, 0)),
            src.get_at((0, h - 1)),
            src.get_at((w - 1, h - 1)),
        ]
        bg_r = sum(c.r for c in corners) // 4
        bg_g = sum(c.g for c in corners) // 4
        bg_b = sum(c.b for c in corners) // 4

        hard_cut = 22

        for y in range(h):
            for x in range(w):
                c = out.get_at((x, y))
                dr = c.r - bg_r
                dg = c.g - bg_g
                db = c.b - bg_b
                dist = (dr * dr + dg * dg + db * db) ** 0.5

                if dist <= hard_cut:
                    out.set_at((x, y), (c.r, c.g, c.b, 0))
                else:
                    out.set_at((x, y), (c.r, c.g, c.b, 255))

        rect = out.get_bounding_rect(min_alpha=8)
        if rect.width > 0 and rect.height > 0:
            trimmed = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            trimmed.blit(out, (0, 0), rect)
            return trimmed
        return out

    def _build_castle_tile_texture(self, wall: pygame.Surface | None) -> pygame.Surface | None:
        """Build dark brick texture matching the provided reference style."""

        tex = pygame.Surface((TILE_SIZE, TILE_SIZE))
        tex.fill((22, 22, 24))

        brick_h = max(8, TILE_SIZE // 5)
        brick_w = max(14, TILE_SIZE // 2)
        for row in range(0, TILE_SIZE + brick_h, brick_h):
            row_index = row // brick_h
            x_offset = 0 if row_index % 2 == 0 else (brick_w // 2)
            for col in range(-brick_w, TILE_SIZE + brick_w, brick_w):
                bx = col + x_offset
                by = row
                bw = brick_w - 2
                bh = brick_h - 2
                if bx + bw < 0 or bx >= TILE_SIZE:
                    continue

                brick_hash = (row_index * 92821) ^ (col * 68917)
                tone = ((brick_hash & 7) - 3) * 3
                base = 76 + tone
                color = (
                    max(48, min(118, base)),
                    max(48, min(118, base)),
                    max(52, min(122, base + 4)),
                )
                brick = pygame.Rect(bx, by, bw, bh)
                pygame.draw.rect(tex, color, brick)
                pygame.draw.line(tex, (96, 96, 106), (brick.left, brick.top), (brick.right - 1, brick.top), 1)
                pygame.draw.line(tex, (42, 42, 48), (brick.left, brick.bottom - 1), (brick.right - 1, brick.bottom - 1), 1)

        return tex

    def _load_terrain_tile_texture(self, filename: str, crop_anchor: str = "center") -> pygame.Surface | None:
        """Load a terrain texture and crop/scale it to one tile."""

        path = TERRAIN_DIR / filename
        if not path.exists():
            return None
        try:
            src = pygame.image.load(str(path)).convert_alpha()
            content = src.get_bounding_rect(min_alpha=8)
            if content.width > 0 and content.height > 0:
                crop = src.subsurface(content).copy()
            else:
                crop = src.copy()

            side = min(crop.get_width(), crop.get_height())
            if crop_anchor == "left_bottom":
                cx = 0
                cy = crop.get_height() - side
            elif crop_anchor == "right_bottom":
                cx = crop.get_width() - side
                cy = crop.get_height() - side
            elif crop_anchor == "left_top":
                cx = 0
                cy = 0
            elif crop_anchor == "right_top":
                cx = crop.get_width() - side
                cy = 0
            else:
                cx = (crop.get_width() - side) // 2
                cy = (crop.get_height() - side) // 2
            square = crop.subsurface(pygame.Rect(cx, cy, side, side)).copy()
            return pygame.transform.smoothscale(square, (TILE_SIZE, TILE_SIZE))
        except pygame.error:
            return None

    def _load_first_terrain_texture(self, filenames: list[str], crop_anchor: str = "center") -> pygame.Surface | None:
        """Try multiple filename variants and return the first texture that loads."""

        for name in filenames:
            tex = self._load_terrain_tile_texture(name, crop_anchor=crop_anchor)
            if tex is not None:
                return tex
        return None

    def _make_black_transparent(self, tex: pygame.Surface | None) -> pygame.Surface | None:
        """Turn near-black background pixels transparent so lava shows through."""
        if tex is None:
            return None
        out = tex.copy().convert_alpha()
        w, h = out.get_size()
        chroma_cutoff = 15
        for y in range(h):
            for x in range(w):
                r, g, b, a = out.get_at((x, y))
                if a == 0:
                    continue
                chroma = max(r, g, b) - min(r, g, b)
                if chroma <= chroma_cutoff:
                    out.set_at((x, y), (r, g, b, 0))
                else:
                    out.set_at((x, y), (r, g, b, 255))
        return out

    def _normalize_tile_color(self, tex: pygame.Surface | None) -> pygame.Surface | None:
        """Normalize tile colors so all tiles share a consistent palette."""
        if tex is None:
            return None
        out = tex.copy().convert_alpha()
        w, h = out.get_size()
        # Shift island tiles to a darker swamp-green tone that matches the poison rocks.
        target_r, target_g, target_b = 44.0, 86.0, 40.0
        r_sum, g_sum, b_sum, count = 0.0, 0.0, 0.0, 0
        for y in range(h):
            for x in range(w):
                r, g, b, a = out.get_at((x, y))
                if a < 128:
                    continue
                r_sum += r
                g_sum += g
                b_sum += b
                count += 1
        if count == 0:
            return out
        avg_r, avg_g, avg_b = r_sum / count, g_sum / count, b_sum / count
        sr = max(0.60, min(1.60, target_r / avg_r)) if avg_r > 0 else 1.0
        sg = max(0.60, min(1.60, target_g / avg_g)) if avg_g > 0 else 1.0
        sb = max(0.60, min(1.60, target_b / avg_b)) if avg_b > 0 else 1.0
        for y in range(h):
            for x in range(w):
                r, g, b, a = out.get_at((x, y))
                if a < 128:
                    continue
                out.set_at((x, y), (
                    min(255, int(r * sr)),
                    min(255, int(g * sg)),
                    min(255, int(b * sb)),
                    a,
                ))
        return out

    # ------------------------------------------------------------------
    # Poison (lava) frame loading
    # ------------------------------------------------------------------

    def _apply_poison_filter(self, surf: pygame.Surface) -> pygame.Surface:
        """Recolor lava pixels to toxic green poison palette."""
        out = surf.copy().convert_alpha()
        w, h = out.get_size()
        for py in range(h):
            for px in range(w):
                r, g, b, a = out.get_at((px, py))
                if a == 0:
                    continue
                luma = r * 0.6 + g * 0.3 + b * 0.1
                nr = max(0, min(255, int(luma * 0.08)))
                ng = max(0, min(255, int(luma * 0.90)))
                nb = max(0, min(255, int(luma * 0.18)))
                out.set_at((px, py), (nr, ng, nb, a))
        return out

    def _load_lava_frames(self) -> list[pygame.Surface]:
        """Load, scale, and poison-filter animated lava frames."""

        frames: list[pygame.Surface] = []
        for idx in range(1, 8):
            path = TERRAIN_DIR / f"burninglava{idx}.png"
            if not path.exists():
                continue
            try:
                src = pygame.image.load(str(path)).convert_alpha()
                content = src.get_bounding_rect(min_alpha=8)
                if content.width > 0 and content.height > 0:
                    base_side = min(content.width, content.height)
                    side = max(16, int(base_side * 0.32))
                    cx = content.x + (content.width - side) // 2
                    cy = content.y + (content.height - side)
                    square = src.subsurface(pygame.Rect(cx, cy, side, side)).copy()
                else:
                    w, h = src.get_size()
                    side = max(16, int(min(w, h) * 0.32))
                    cx = (w - side) // 2
                    cy = h - side
                    square = src.subsurface(pygame.Rect(cx, cy, side, side)).copy()
                scaled = pygame.transform.smoothscale(square, (TILE_SIZE, TILE_SIZE))
                frames.append(self._apply_poison_filter(scaled))
            except pygame.error:
                continue
        return frames

    def _load_lava_rock_frames(self) -> list[pygame.Surface]:
        """Load, scale, and poison-filter animated lava rock frames."""

        frames: list[pygame.Surface] = []
        rock_size = max(14, int(TILE_SIZE * 0.62))
        for idx in range(1, 8):
            path = TERRAIN_DIR / f"lavarock{idx}.png"
            if not path.exists():
                continue
            try:
                src = pygame.image.load(str(path)).convert_alpha()
                content = src.get_bounding_rect(min_alpha=8)
                if content.width > 0 and content.height > 0:
                    rock = src.subsurface(content).copy()
                else:
                    rock = src.copy()
                scaled = pygame.transform.smoothscale(rock, (rock_size, rock_size))
                frames.append(self._apply_poison_filter(scaled))
            except pygame.error:
                continue
        return frames

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> bool:
        """Main game loop."""

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
            pass
        else:
            pygame.quit()
        return self.return_to_lobby_requested

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if self._handle_chat_key(event):
                    continue
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_r and self.show_result_screen:
                    self.return_to_lobby_requested = True
                    self.running = False
                elif event.key == pygame.K_c:
                    self.cheer_pulse_until_ms = pygame.time.get_ticks() + 650
                elif event.key == pygame.K_t:
                    self.chat_typing = True
                self._handle_direction_input(event.key)

    def _handle_chat_key(self, event: pygame.event.Event) -> bool:
        if event.key == pygame.K_RETURN:
            if self.chat_typing:
                self._submit_chat()
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
        if event.unicode and event.unicode.isprintable():
            self.chat_input += event.unicode
            if len(self.chat_input) > 120:
                self.chat_input = self.chat_input[:120]
            return True
        return False

    def _submit_chat(self) -> None:
        text = self.chat_input.strip()
        self.chat_typing = False
        self.chat_input = ""
        if not text or self.connection is None:
            return
        try:
            self.connection.send_message(make_chat_message(sender=self.username, message=text))
        except OSError:
            self.chat_messages.append("[CHAT] Send failed.")

    # ------------------------------------------------------------------
    # Tile drawing
    # ------------------------------------------------------------------

    def _draw_tile(
        self,
        x: int,
        y: int,
        left_side: bool,
        border_row: bool = False,
        corner_type: str | None = None,
        edge_type: str | None = None,
        top_edge: bool = False,
    ) -> None:
        """Draw one seamless tile with soft lighting, depth, and ultra-subtle texture."""

        fill = (58, 58, 72)

        rect = pygame.Rect(x, y, TILE_SIZE, TILE_SIZE)

        if corner_type == "left":
            selected_texture = self.left_corner_texture or self.border_land_texture
        elif corner_type == "right":
            selected_texture = self.right_corner_texture or self.border_land_texture
        elif corner_type == "left_of_castle":
            selected_texture = self.left_of_castle_corner_texture or self.border_land_texture
        elif corner_type == "right_of_castle":
            selected_texture = self.right_of_castle_corner_texture or self.border_land_texture
        elif corner_type == "top_left":
            selected_texture = self.top_left_texture or self.border_land_texture
        elif corner_type == "top_right":
            selected_texture = self.top_right_texture or self.border_land_texture
        elif top_edge:
            selected_texture = self.top_side_texture or self.inner_land_texture
        elif edge_type == "left":
            selected_texture = self.left_side_texture or self.border_land_texture
        elif edge_type == "right":
            selected_texture = self.right_side_texture or self.border_land_texture
        else:
            selected_texture = self.border_land_texture if border_row else self.inner_land_texture
        if selected_texture is not None:
            self.screen.blit(selected_texture, (rect.x, rect.y))
        elif self.castle_tile_texture is not None:
            self.screen.blit(self.castle_tile_texture, (rect.x, rect.y))
        else:
            pygame.draw.rect(self.screen, fill, rect)

    def _draw_bottom_tile_faces(self) -> None:
        """Bottom strip removed: grid remains the bottommost visual element."""
        return

    # ------------------------------------------------------------------
    # Poison pool (lava) drawing
    # ------------------------------------------------------------------

    def _draw_lava(self) -> None:
        """Draw animated poison pool across arena background; tiles render on top."""

        if not self.lava_frames:
            return

        frame = self.lava_frames[(pygame.time.get_ticks() // 120) % len(self.lava_frames)]
        tile = TILE_SIZE

        for y in range(0, self.screen.get_height(), tile):
            for x in range(0, self.screen.get_width(), tile):
                self.screen.blit(frame, (x, y))

    def _draw_lava_rocks(self) -> None:
        """Draw animated poison rocks in lava zones, excluding grid vicinity."""

        if not self.lava_rock_frames:
            return
        frame = self.lava_rock_frames[(pygame.time.get_ticks() // 110) % len(self.lava_rock_frames)]
        rw, rh = frame.get_size()

        castle_layout = self._compute_castle_layout()
        if castle_layout is None:
            castle_exclusion = pygame.Rect(0, 0, 0, 0)
        else:
            cx, cy, cw, ch = castle_layout
            castle_exclusion = pygame.Rect(cx, cy, cw, ch).inflate(TILE_SIZE * 3, TILE_SIZE * 2)

        grid_rect = pygame.Rect(self.arena_x, self.arena_y - (SIDE_EXTENSION_ROWS * TILE_SIZE), self.arena_w, self.arena_h + (SIDE_EXTENSION_ROWS * TILE_SIZE))

        step_x = max(28, int(TILE_SIZE * 1.05))
        step_y = max(24, int(TILE_SIZE * 0.95))
        for y in range(0, self.screen.get_height(), step_y):
            for x in range(0, self.screen.get_width(), step_x):
                gate = ((x // step_x) * 92821) ^ ((y // step_y) * 68917)
                if (gate % 5) != 0:
                    continue

                jitter_x = ((gate >> 3) & 7) - 3
                jitter_y = ((gate >> 6) & 7) - 3
                px = x + jitter_x
                py = y + jitter_y
                r = pygame.Rect(px, py, rw, rh)
                if r.colliderect(grid_rect):
                    continue
                if r.colliderect(castle_exclusion):
                    continue
                self.screen.blit(frame, (px, py))

    def _compute_castle_layout(self) -> tuple[int, int, int, int] | None:
        """Compute castle (x, y, width, height) — kept for rock exclusion zone."""

        if self.castle_wall is None:
            return None
        src_w, src_h = self.castle_wall.get_size()
        if src_w <= 0 or src_h <= 0:
            return None

        canvas_height = self.screen.get_height()
        canvas_width = self.screen.get_width()
        aspect_ratio = src_w / src_h

        scaled_w = int(canvas_width * 0.65)
        scaled_h = int(scaled_w / aspect_ratio)

        max_allowed_h = int(canvas_height * 0.95)
        if scaled_h > max_allowed_h:
            scaled_h = max_allowed_h
            scaled_w = int(scaled_h * aspect_ratio)

        x = (canvas_width - scaled_w) // 2
        y = self.arena_y - scaled_h

        if y < 5:
            scaled_h = self.arena_y - 5
            scaled_w = int(scaled_h * aspect_ratio)
            x = (canvas_width - scaled_w) // 2
            y = 5
        return (x, y, scaled_w, scaled_h)

    # ------------------------------------------------------------------
    # Giant arch serpent (replaces castle above the grid)
    # ------------------------------------------------------------------

    # ---- throw state machine helpers (big serpent) -------------------

    def _update_throw_state(self, ticks: int) -> None:
        """Advance the big serpent's throw state machine (idle/wind_up/throw/recover)."""

        state = self._throw_state
        elapsed = ticks - self._throw_state_start

        if state == "idle":
            if ticks >= self._next_throw_time:
                self._throw_state = "wind_up"
                self._throw_state_start = ticks
                self._throw_blob_spawned = False
        elif state == "wind_up":
            if elapsed >= 650:  # 0.65s rear-up
                self._throw_state = "throw"
                self._throw_state_start = ticks
        elif state == "throw":
            if elapsed >= 320:  # 0.32s strike
                self._throw_state = "recover"
                self._throw_state_start = ticks
        elif state == "recover":
            if elapsed >= 600:  # 0.6s settle
                self._throw_state = "idle"
                self._throw_state_start = ticks
                self._next_throw_time = ticks + random.randint(4400, 5600)

    def _throw_progress(self, ticks: int) -> float:
        """Progress 0..1 within the current throw state."""

        elapsed = ticks - self._throw_state_start
        state = self._throw_state
        if state == "wind_up":
            return min(1.0, elapsed / 650.0)
        if state == "throw":
            return min(1.0, elapsed / 320.0)
        if state == "recover":
            return min(1.0, elapsed / 600.0)
        return 0.0

    def _spawn_blob_from_head(self, head_pos: tuple[float, float], head_dir: tuple[float, float]) -> None:
        """Spawn a poison blob projectile thrown from the serpent's head."""

        hx, hy = head_pos
        # Aim at a random arena tile (avoid the very edges).
        target_col = random.uniform(2.0, GRID_COLS - 3.0)
        target_row = random.uniform(1.5, GRID_ROWS - 1.5)
        target_x = self.arena_x + target_col * TILE_SIZE
        target_y = self.arena_y + target_row * TILE_SIZE

        # Compute initial velocity for a parabolic arc to that target.
        flight_time = random.uniform(0.95, 1.25)
        gravity = 780.0
        vx = (target_x - hx) / flight_time
        vy = (target_y - hy - 0.5 * gravity * flight_time * flight_time) / flight_time

        # Add a small launch-impulse along the head's strike direction.
        dx, dy = head_dir
        vx += dx * 60.0
        vy += dy * 40.0

        self._poison_blobs.append({
            "x": float(hx),
            "y": float(hy),
            "vx": float(vx),
            "vy": float(vy),
            "target_y": float(target_y),
            "wobble": random.uniform(0.0, math.tau),
            "alive": True,
            "splat_at": 0,
            "splat_x": 0.0,
            "splat_y": 0.0,
        })

    # ---- big serpent rendering ---------------------------------------

    def _update_serpent_agent(self, dt: float) -> None:
        """Steer the big serpent's head through the gap above the arena each frame."""

        s = self._serpent_agent
        ext_h = SIDE_EXTENSION_ROWS * TILE_SIZE
        gx1 = self.arena_x + SIDE_EXTENSION_TILES * TILE_SIZE + 56
        gx2 = self.arena_x + self.arena_w - SIDE_EXTENSION_TILES * TILE_SIZE - 56
        gy1 = self.arena_y - ext_h + 24
        gy2 = self.arena_y - 20

        # Periodic random turn.
        s["turn_timer"] -= dt
        if s["turn_timer"] <= 0:
            s["target_angle"] += random.uniform(-math.pi * 0.55, math.pi * 0.55)
            s["turn_timer"] = random.uniform(1.4, 3.0)

        # Repulsion from gap walls.
        MARGIN = 52.0
        x, y = s["x"], s["y"]
        rx, ry = 0.0, 0.0
        if x - gx1 < MARGIN: rx += (MARGIN - (x - gx1)) / MARGIN * 5.0
        if gx2 - x < MARGIN: rx -= (MARGIN - (gx2 - x)) / MARGIN * 5.0
        if y - gy1 < MARGIN: ry += (MARGIN - (y - gy1)) / MARGIN * 5.0
        if gy2 - y < MARGIN: ry -= (MARGIN - (gy2 - y)) / MARGIN * 5.0
        if rx != 0.0 or ry != 0.0:
            repel_angle = math.atan2(ry, rx)
            strength = (rx * rx + ry * ry) ** 0.5
            blend = min(1.0, strength / 3.0)
            s["target_angle"] = (repel_angle
                                 + (1.0 - blend) * random.uniform(-0.18, 0.18))

        # Smooth rotation toward target angle.
        da = s["target_angle"] - s["angle"]
        while da >  math.pi: da -= math.tau
        while da < -math.pi: da += math.tau
        rate = s["turn_rate"] * dt
        s["angle"] += max(-rate, min(rate, da))

        # Advance head.
        s["x"] += math.cos(s["angle"]) * s["speed"] * dt
        s["y"] += math.sin(s["angle"]) * s["speed"] * dt

        # Hard-clamp so the serpent never escapes the gap.
        s["x"] = max(float(gx1), min(float(gx2), s["x"]))
        s["y"] = max(float(gy1), min(float(gy2), s["y"]))

        s["history"].appendleft((s["x"], s["y"]))

    def _draw_arena_serpent(self) -> None:
        """Draw the giant decorative serpent fully inside the center gap.

        The serpent is the arena's "main character": it slithers in idle motion
        and periodically rears its head up like an anaconda before snapping
        forward to throw a poison-coated blob into the arena.
        """

        ticks = pygame.time.get_ticks()
        phase = ticks / 900.0

        # Advance throw state machine before sampling motion modifiers.
        self._update_throw_state(ticks)
        state = self._throw_state
        sp = self._throw_progress(ticks)

        # Compute head_y_offset (positive = head goes DOWN, negative = UP)
        # and forward_extend (positive = head reaches further along its trail).
        y_offset = 0.0
        forward_extend = 0.0
        if state == "wind_up":
            eased = 1 - (1 - sp) ** 2
            y_offset = -78.0 * eased  # rear up like an anaconda
            forward_extend = -8.0 * eased  # slight pull back
        elif state == "throw":
            # Snap from -78 (high) to +24 (struck low), then settle.
            eased = 1 - (1 - sp) ** 3
            y_offset = -78.0 + eased * 102.0
            forward_extend = 34.0 * math.sin(sp * math.pi)
        elif state == "recover":
            # Glide back from +24 to 0.
            eased = 1 - (1 - sp) ** 2
            y_offset = 24.0 * (1.0 - eased)
            forward_extend = -6.0 * math.sin(sp * math.pi)

        ext_h = SIDE_EXTENSION_ROWS * TILE_SIZE
        gap_y = self.arena_y - ext_h + 16
        gap_h = ext_h - 30

        # Move the serpent agent and sample body positions via arc-length.
        self._update_serpent_agent(self._step_dt)
        num_seg = 45
        raw_pts = self._sample_arc_positions(self._serpent_agent["history"], num_seg, 14.0)
        # Reverse so index 0 = tail and index -1 = head — matches original draw order.
        draw_pts: list[tuple[float, float]] = list(reversed(raw_pts))

        # Apply throw animation to the head section (last 45% of segments).
        if y_offset != 0.0 or forward_extend != 0.0:
            fdx, fdy = 1.0, 0.0
            if len(draw_pts) > 1:
                ddx = draw_pts[-1][0] - draw_pts[-2][0]
                ddy = draw_pts[-1][1] - draw_pts[-2][1]
                dn = max(0.01, (ddx * ddx + ddy * ddy) ** 0.5)
                fdx, fdy = ddx / dn, ddy / dn
            for i in range(num_seg):
                t_seg = i / (num_seg - 1)
                hw_q = max(0.0, (t_seg - 0.55) / 0.45) ** 2
                if hw_q > 0.0:
                    px_, py_ = draw_pts[i]
                    draw_pts[i] = (
                        px_ + forward_extend * hw_q * fdx,
                        py_ + y_offset * hw_q + forward_extend * hw_q * fdy,
                    )

        pts = draw_pts  # alias — all downstream code uses pts unchanged

        # Body segments rendered tail->head so the head sits on top.
        for i in range(num_seg):
            t = i / (num_seg - 1)
            px, py = int(pts[i][0]), int(pts[i][1])
            body = math.sin(math.pi * t)
            # Subtle radius "breathing" makes the snake feel alive.
            breath = 1.0 + 0.04 * math.sin(phase * 2.2 + t * 6.0)
            r = max(4, int((7 + 9 * (0.35 + 0.65 * body)) * breath))

            pygame.draw.circle(self.screen, (16, 58, 10), (px, py), r + 2)
            pygame.draw.circle(self.screen, (27, 94, 18), (px, py), r + 1)
            pygame.draw.circle(self.screen, (40, 132, 28), (px, py), r)
            pygame.draw.circle(self.screen, (60, 172, 42), (px, py + max(1, r // 4)), max(2, r - 5))

            if i % 4 == 0 and 1 <= i < num_seg - 1:
                tx = pts[i + 1][0] - pts[i - 1][0]
                ty = pts[i + 1][1] - pts[i - 1][1]
                norm = max(0.01, (tx * tx + ty * ty) ** 0.5)
                perp_x, perp_y = -ty / norm, tx / norm
                if perp_y > 0:
                    perp_x, perp_y = -perp_x, -perp_y
                sx = int(px + perp_x * (r - 1))
                sy = int(py + perp_y * (r - 1))
                ex = int(px + perp_x * (r + 4))
                ey = int(py + perp_y * (r + 4))
                pygame.draw.line(self.screen, (12, 44, 8), (sx, sy), (ex, ey), 2)

        # Head & facial features.
        hx, hy = int(pts[-1][0]), int(pts[-1][1])
        dir_x = pts[-1][0] - pts[-2][0]
        dir_y = pts[-1][1] - pts[-2][1]
        norm = max(0.01, (dir_x * dir_x + dir_y * dir_y) ** 0.5)
        dir_x /= norm
        dir_y /= norm
        perp_x, perp_y = -dir_y, dir_x

        # Head grows slightly when rearing up to add menace.
        head_pulse = 1.0
        if state == "wind_up":
            head_pulse = 1.0 + 0.18 * sp
        elif state == "throw":
            head_pulse = 1.18 - 0.22 * sp  # shrinks a touch on strike
        elif state == "recover":
            head_pulse = 0.96 + 0.04 * sp
        head_r = max(12, int(16 * head_pulse))

        pygame.draw.circle(self.screen, (14, 50, 8), (hx, hy), head_r + 4)
        pygame.draw.circle(self.screen, (24, 84, 16), (hx, hy), head_r + 1)
        pygame.draw.circle(self.screen, (38, 124, 26), (hx, hy), head_r)

        # Jaw - extra bulge near the lower head edge for character.
        jaw_x = int(hx + perp_x * (head_r * 0.45) + dir_x * 4)
        jaw_y = int(hy + perp_y * (head_r * 0.45) + dir_y * 4)
        pygame.draw.circle(self.screen, (32, 102, 22), (jaw_x, jaw_y), max(4, head_r - 6))

        snout_x = int(hx + dir_x * (head_r - 3))
        snout_y = int(hy + dir_y * (head_r - 3))
        pygame.draw.circle(self.screen, (34, 108, 24), (snout_x, snout_y), max(4, head_r - 5))

        # Nostrils.
        for sign in (-1, 1):
            nx = int(snout_x + perp_x * sign * 3 + dir_x * 4)
            ny = int(snout_y + perp_y * sign * 3 + dir_y * 4)
            pygame.draw.circle(self.screen, (12, 38, 8), (nx, ny), 1)

        # Eyes - brighter when rearing/striking.
        eye_intensity = 1.0
        if state in ("wind_up", "throw"):
            eye_intensity = 1.4
        for sign in (-1, 1):
            ex = int(hx + perp_x * sign * (head_r * 0.55) + dir_x * 5)
            ey = int(hy + perp_y * sign * (head_r * 0.55) + dir_y * 5)
            glow_s = pygame.Surface((40, 40), pygame.SRCALPHA)
            pygame.draw.circle(glow_s, (0, 220, 60, int(60 * eye_intensity)), (20, 20), 14)
            pygame.draw.circle(glow_s, (0, 255, 90, int(110 * eye_intensity)), (20, 20), 8)
            self.screen.blit(glow_s, (ex - 20, ey - 20))
            pygame.draw.circle(self.screen, (0, 255, 100), (ex, ey), 5)
            # Slit pupil aligned with movement direction.
            pup_a = (int(ex - dir_x * 1.5), int(ey - dir_y * 1.5))
            pup_b = (int(ex + dir_x * 1.5), int(ey + dir_y * 1.5))
            pygame.draw.line(self.screen, (0, 0, 0), pup_a, pup_b, 2)

        # Crown spikes.
        for st in (-0.26, 0.0, 0.26):
            bx = int(hx + perp_x * st * 16 - dir_x * 8)
            by = int(hy + perp_y * st * 16 - dir_y * 8)
            tip_x = int(bx - dir_x * 10)
            tip_y = int(by - dir_y * 10)
            pygame.draw.line(self.screen, (50, 180, 35), (bx, by), (tip_x, tip_y), 2)
            pygame.draw.circle(self.screen, (88, 250, 55), (tip_x, tip_y), 3)

        # Tongue - flickers more aggressively while idle.
        tongue_visible = math.sin(ticks / 170.0) > 0.52 or state == "wind_up"
        if state == "throw":
            tongue_visible = False  # mouth open / striking
        if tongue_visible:
            base_x = int(snout_x + dir_x * 7)
            base_y = int(snout_y + dir_y * 7)
            mid_x = int(base_x + dir_x * 10)
            mid_y = int(base_y + dir_y * 10)
            pygame.draw.line(self.screen, (212, 28, 28), (base_x, base_y), (mid_x, mid_y), 2)
            for sign in (-1, 1):
                fx = int(mid_x + (dir_x * 6 + perp_x * sign * 5))
                fy = int(mid_y + (dir_y * 6 + perp_y * sign * 5))
                pygame.draw.line(self.screen, (212, 28, 28), (mid_x, mid_y), (fx, fy), 2)

        # Open jaw / charge-up glow inside the mouth during wind-up & throw.
        if state in ("wind_up", "throw"):
            charge_t = sp if state == "wind_up" else (1.0 - sp)
            mouth_x = int(snout_x + dir_x * 6)
            mouth_y = int(snout_y + dir_y * 6)
            mouth_r = max(3, int(7 + 4 * charge_t))
            charge = pygame.Surface((mouth_r * 4 + 4, mouth_r * 4 + 4), pygame.SRCALPHA)
            cc = mouth_r * 2 + 2
            pygame.draw.circle(charge, (60, 220, 40, int(120 * charge_t)), (cc, cc), mouth_r + 4)
            pygame.draw.circle(charge, (160, 255, 90, int(200 * charge_t)), (cc, cc), mouth_r)
            pygame.draw.circle(charge, (240, 255, 200, int(240 * charge_t)), (cc, cc), max(1, mouth_r - 3))
            self.screen.blit(charge, (mouth_x - cc, mouth_y - cc))

        # Spawn the blob mid-throw, exactly once per cycle.
        if state == "throw" and not self._throw_blob_spawned and sp >= 0.42:
            self._spawn_blob_from_head((float(hx), float(hy)), (dir_x, dir_y))
            self._throw_blob_spawned = True

        # Tail tip.
        tx, ty = int(pts[0][0]), int(pts[0][1])
        pygame.draw.circle(self.screen, (20, 70, 12), (tx, ty), 5)
        pygame.draw.circle(self.screen, (38, 118, 26), (tx, ty), 3)

        # Poison drip particles trailing off the body.
        max_drop = max(22, int(gap_h * 0.3))
        for drip in self._poison_drips:
            t = max(0.0, min(1.0, float(drip["t"])))
            idx = min(num_seg - 2, max(0, int(t * (num_seg - 1))))
            frac = t * (num_seg - 1) - idx
            bx = pts[idx][0] * (1.0 - frac) + pts[idx + 1][0] * frac
            by = pts[idx][1] * (1.0 - frac) + pts[idx + 1][1] * frac

            drip["y_offset"] += drip["vy"]
            if drip["y_offset"] > max_drop:
                drip["y_offset"] = 0.0
                drip["alpha"] = random.randint(130, 220)

            px = int(bx)
            py = int(by + 8 + drip["y_offset"])
            alpha = max(0, drip["alpha"] - int(drip["y_offset"] * 3.0))
            if alpha > 12 and gap_y <= py <= gap_y + gap_h + 18:
                sz = drip["size"]
                ds = pygame.Surface((sz * 4 + 2, sz * 4 + 2), pygame.SRCALPHA)
                pygame.draw.circle(ds, (80, 255, 40, alpha), (sz * 2 + 1, sz * 2 + 1), sz)
                self.screen.blit(ds, (px - sz * 2 - 1, py - sz * 2 - 1))

    # ---- poison blobs (projectiles) ----------------------------------

    def _draw_blobs(self) -> None:
        """Update and render poison blob projectiles (and their splats)."""

        if not self._poison_blobs:
            return

        ticks = pygame.time.get_ticks()
        dt = self._step_dt
        gravity = 780.0

        survivors: list[dict] = []
        for blob in self._poison_blobs:
            if not blob["alive"]:
                # Splat fades out over ~450 ms.
                age = ticks - blob["splat_at"]
                if age < 450:
                    self._draw_splat(blob, age)
                    survivors.append(blob)
                continue

            blob["vy"] += gravity * dt
            blob["x"] += blob["vx"] * dt
            blob["y"] += blob["vy"] * dt

            # Land when the blob reaches (or passes) its intended tile y.
            if blob["y"] >= blob["target_y"]:
                blob["alive"] = False
                blob["splat_at"] = ticks
                blob["splat_x"] = blob["x"]
                blob["splat_y"] = blob["target_y"]
                # Blob landing spawns a collectible health berry — only on valid island tiles.
                gcol = int((blob["x"] - self.game_origin_x) / GAME_CELL_W)
                grow = int((blob["target_y"] - self.game_origin_y) / GAME_CELL_H)
                if not self._is_out_of_bounds(gcol, grow):
                    cell = (gcol, grow)
                    occupied = (set(self.game_obstacles) | set(self.snake_a)
                                | set(self.snake_b) | set(self.game_pies))
                    if cell not in occupied:
                        self.game_pies.append(cell)
                survivors.append(blob)
                continue
            if blob["x"] < -60 or blob["x"] > WINDOW_WIDTH + 60 or blob["y"] > WINDOW_HEIGHT + 60:
                continue

            bx, by = int(blob["x"]), int(blob["y"])
            wob = math.sin(ticks / 80.0 + blob["wobble"])
            r = 11 + int(2 * wob)

            # Trailing droplets (drawn behind the blob).
            for ti in range(3):
                trail_dt = -0.045 * (ti + 1)
                tx_ = blob["x"] + blob["vx"] * trail_dt
                ty_ = blob["y"] + blob["vy"] * trail_dt - 0.5 * gravity * trail_dt * trail_dt
                tr = max(2, r - 3 - ti * 2)
                ta = 170 - ti * 50
                ts = pygame.Surface((tr * 4 + 2, tr * 4 + 2), pygame.SRCALPHA)
                tc = tr * 2 + 1
                pygame.draw.circle(ts, (60, 200, 40, ta), (tc, tc), tr)
                self.screen.blit(ts, (int(tx_) - tc, int(ty_) - tc))

            # Outer glow.
            gs = pygame.Surface((r * 4 + 8, r * 4 + 8), pygame.SRCALPHA)
            gc = r * 2 + 4
            pygame.draw.circle(gs, (60, 220, 40, 70),  (gc, gc), r + 7)
            pygame.draw.circle(gs, (90, 255, 60, 110), (gc, gc), r + 3)
            self.screen.blit(gs, (bx - gc, by - gc))

            # Body layers.
            pygame.draw.circle(self.screen, (16, 58, 10),    (bx, by),         r + 2)
            pygame.draw.circle(self.screen, (40, 138, 28),   (bx, by),         r)
            pygame.draw.circle(self.screen, (110, 220, 70),  (bx - 2, by - 2), max(2, r - 4))
            pygame.draw.circle(self.screen, (220, 255, 200), (bx - 3, by - 3), max(1, r - 8))

            survivors.append(blob)

        self._poison_blobs = survivors

    def _draw_splat(self, blob: dict, age_ms: int) -> None:
        """Draw a poison splat that expands and fades on impact."""

        sx = int(blob["splat_x"])
        sy = int(blob["splat_y"])
        age_t = age_ms / 450.0  # 0..1
        radius = int(14 + 18 * age_t)
        alpha = max(0, int(220 * (1.0 - age_t)))
        s = pygame.Surface((radius * 2 + 6, radius * 2 + 6), pygame.SRCALPHA)
        cc = radius + 3
        pygame.draw.circle(s, (40, 200, 40, alpha // 2), (cc, cc), radius)
        pygame.draw.circle(s, (90, 255, 60, alpha), (cc, cc), max(2, radius - 5))
        pygame.draw.circle(s, (200, 255, 180, alpha // 3), (cc, cc), max(1, radius - 12))
        self.screen.blit(s, (sx - cc, sy - cc))

    # ---- toxic-spike obstacles ---------------------------------------

    def _draw_obstacles(self) -> None:
        """Draw mushrooms/skulls directly at the authoritative game-obstacle positions.

        game_obstacles uses the server's 20×20 grid, so the art sits exactly
        where collision is checked — snakes cannot visually walk through them.
        """

        ticks = pygame.time.get_ticks()

        for idx, (gcol, grow) in enumerate(self.game_obstacles):
            px, py = self._game_to_pixel(gcol, grow)
            cx = int(px)
            cy = int(py)
            phase = (gcol * 1.1 + grow * 0.7) % (2 * math.pi)
            glow  = 0.55 + 0.45 * math.sin(ticks / 600.0 + phase)
            rng   = random.Random(gcol * 101 + grow * 97)

            if idx % 2 == 0:
                # anchor mushrooms at bottom of the cell
                self._draw_mushroom_cluster(cx, cy + int(GAME_CELL_H * 0.45), glow, rng)
            else:
                self._draw_skull_obstacle(cx, cy, glow, rng)

    # ---- mushroom cluster (original bioluminescent design) ---------------

    def _draw_mushroom_cluster(self, cx: int, cy: int, glow: float, rng: random.Random) -> None:
        """Draw three bioluminescent toxic mushrooms anchored at tile bottom-center."""

        configs = [
            (cx + rng.randint(-11, 11), cy,                        rng.uniform(0.75, 1.0)),
            (cx + rng.randint(-18, -7), cy + rng.randint(-3, 3),   rng.uniform(0.52, 0.72)),
            (cx + rng.randint(7,  18),  cy + rng.randint(-3, 3),   rng.uniform(0.55, 0.75)),
        ]
        for mx, my, sc in configs:
            self._draw_one_mushroom(int(mx), int(my), sc, glow, rng)

    def _draw_one_mushroom(self, mx: int, my: int, scale: float, glow: float, rng: random.Random) -> None:
        """Draw one toxic mushroom anchored at (mx, my) — a front-facing sprite."""

        cap_rx  = max(5, int(17 * scale))
        cap_ry  = max(3, int(10 * scale))
        stem_h  = max(4, int(20 * scale))
        stem_wt = max(1, int(cap_rx * 0.30))
        stem_wb = max(2, int(cap_rx * 0.42))
        cap_cy  = my - stem_h - cap_ry // 2

        shad = pygame.Surface((cap_rx * 2 + 10, 8), pygame.SRCALPHA)
        pygame.draw.ellipse(shad, (0, 18, 0, 130), shad.get_rect().inflate(-4, -2))
        self.screen.blit(shad, (mx - cap_rx - 5, my - 4))

        stem_pts = [
            (mx - stem_wt, my - stem_h),
            (mx + stem_wt, my - stem_h),
            (mx + stem_wb, my),
            (mx - stem_wb, my),
        ]
        pygame.draw.polygon(self.screen, (62, 118, 38), stem_pts)
        pygame.draw.line(self.screen, (100, 168, 58),
                         (mx, my - stem_h + 2), (mx, my - 2), max(1, stem_wt - 1))

        pygame.draw.ellipse(self.screen, (48, 112, 26),
                            pygame.Rect(mx - cap_rx, cap_cy + cap_ry // 2,
                                        cap_rx * 2, max(3, cap_ry // 2)))

        cap_rect = pygame.Rect(mx - cap_rx, cap_cy - cap_ry, cap_rx * 2, cap_ry * 2 + cap_ry // 2)
        pygame.draw.ellipse(self.screen, (20, 74, 10), cap_rect)
        pygame.draw.ellipse(self.screen, (36, 118, 18), cap_rect.inflate(-4, -3))
        pygame.draw.ellipse(self.screen, (72, 188, 36),
                            pygame.Rect(mx - cap_rx // 2, cap_cy - cap_ry + 2, cap_rx, cap_ry - 2))

        for _ in range(rng.randint(2, 5)):
            sx = mx + rng.randint(-cap_rx + 3, cap_rx - 3)
            sy = cap_cy - cap_ry // 2 + rng.randint(-cap_ry // 2, cap_ry // 4)
            sr = rng.randint(1, max(2, int(3 * scale)))
            pygame.draw.circle(self.screen, (185, 255, 75), (sx, sy), sr)
            pygame.draw.circle(self.screen, (235, 255, 190), (sx, sy), max(1, sr - 1))

        aura_w = cap_rx * 2 + 14
        aura_h = cap_ry * 2 + 8
        cap_aura = pygame.Surface((aura_w, aura_h), pygame.SRCALPHA)
        pygame.draw.ellipse(cap_aura, (50, 220, 28, int(60 * glow)), cap_aura.get_rect())
        self.screen.blit(cap_aura, (mx - aura_w // 2, cap_cy - cap_ry - 4))

    # ---- venom skull (poison-dungeon themed) -----------------------------

    def _draw_skull_obstacle(self, cx: int, cy: int, glow: float, rng: random.Random) -> None:
        """Draw a dungeon skull stained with venom — glowing green eye sockets match the arena."""

        r = 15

        # Ground shadow.
        shad = pygame.Surface((r * 2 + 10, 10), pygame.SRCALPHA)
        pygame.draw.ellipse(shad, (0, 0, 0, 100), shad.get_rect().inflate(-4, -2))
        self.screen.blit(shad, (cx - r - 5, cy + r - 3))

        # Skull dome — dark stone, not bright bone, so it reads as "dungeon artefact".
        pygame.draw.circle(self.screen, (30, 34, 30), (cx, cy), r + 1)   # dark rim
        pygame.draw.circle(self.screen, (56, 62, 52), (cx, cy), r)        # muted stone-green
        pygame.draw.circle(self.screen, (74, 82, 68), (cx - 3, cy - 4), r - 5)  # top highlight

        # Eye sockets with bright poison glow — the visual hook that ties the skull
        # to the rest of the arena's green-poison colour language.
        eye_y = cy + 2
        for sign in (-1, 1):
            ex = cx + sign * 6
            # Socket cavity.
            pygame.draw.ellipse(self.screen, (12, 16, 10),
                                pygame.Rect(ex - 5, eye_y - 4, 10, 8))
            # Inner glow surface.
            gs = pygame.Surface((12, 10), pygame.SRCALPHA)
            pygame.draw.ellipse(gs, (0, 210, 45, int(180 * glow)), gs.get_rect().inflate(-2, -2))
            self.screen.blit(gs, (ex - 6, eye_y - 5))
            # Bright core point.
            pygame.draw.ellipse(self.screen, (80, 255, 100),
                                pygame.Rect(ex - 3, eye_y - 2, 6, 4))

        # Nasal cavity.
        pygame.draw.polygon(self.screen, (14, 18, 12), [
            (cx,     cy + 6),
            (cx - 3, cy + 10),
            (cx + 3, cy + 10),
        ])

        # Jaw strip.
        pygame.draw.ellipse(self.screen, (44, 50, 40),
                            pygame.Rect(cx - r + 3, cy + r - 5, (r - 3) * 2, 8))
        pygame.draw.ellipse(self.screen, (30, 34, 28),
                            pygame.Rect(cx - r + 5, cy + r - 3, (r - 5) * 2, 4))

        # Venom-stained cracks — green tint matches the poison lake.
        for crack_pts in [
            [(cx - 2, cy - r + 2), (cx - 8, cy - 3), (cx - 4, cy + 3)],
            [(cx + 3, cy - r + 4), (cx + 7, cy + 1), (cx + 2, cy + 7)],
        ]:
            pygame.draw.lines(self.screen, (20, 75, 18), False, crack_pts, 1)

    # ------------------------------------------------------------------
    # Small pool snakes
    # ------------------------------------------------------------------

    def _sample_arc_positions(
        self,
        history: deque,
        num_segs: int,
        spacing: float,
    ) -> list[tuple[float, float]]:
        """Return num_segs positions spaced 'spacing' pixels apart along the path history.

        Uses true arc-length parameterisation with linear interpolation between
        samples so segments flow smoothly regardless of frame rate or speed.
        """
        pts: list[tuple[float, float]] = []
        it = iter(history)
        try:
            px, py = next(it)
        except StopIteration:
            return [(0.0, 0.0)] * num_segs

        pts.append((px, py))
        if num_segs <= 1:
            return pts

        target = spacing
        acc = 0.0

        for nx, ny in it:
            dx = nx - px
            dy = ny - py
            seg_len = (dx * dx + dy * dy) ** 0.5
            while acc + seg_len >= target and len(pts) < num_segs:
                t = (target - acc) / seg_len if seg_len > 0.0 else 0.0
                pts.append((px + t * dx, py + t * dy))
                target += spacing
            acc += seg_len
            px, py = nx, ny
            if len(pts) >= num_segs:
                break

        while len(pts) < num_segs:
            pts.append((px, py))
        return pts

    def _draw_snakes(self) -> None:
        """Update and draw pool snakes using full 2-D agent motion.

        Each snake has a heading angle that drifts randomly and is repelled
        from the grid boundary and screen edges.  Body segments follow the
        head via a position-history deque, giving completely natural trailing
        motion regardless of how the head turns.
        """

        ticks = pygame.time.get_ticks()
        dt = self._step_dt

        # Grid exclusion zone (snakes get repelled from this rectangle).
        g_left  = float(self.arena_x - 5)
        g_right = float(self.arena_x + self.arena_w + 5)
        g_top   = float(self.arena_y - SIDE_EXTENSION_ROWS * TILE_SIZE - 5)
        g_bot   = float(self.arena_y + self.arena_h + 5)
        GMARGIN = 65.0
        SM      = 18.0   # screen-edge repulsion margin

        for snake in self.snakes:
            x, y     = snake["x"], snake["y"]
            sc       = snake["scale"]
            speed    = snake["speed"]

            # ---- random turn target ------------------------------------------
            snake["turn_timer"] -= dt
            if snake["turn_timer"] <= 0:
                snake["target_angle"] += random.uniform(-math.pi * 0.6, math.pi * 0.6)
                snake["turn_timer"] = random.uniform(1.0, 2.6)

            # ---- repulsion forces --------------------------------------------
            rx, ry = 0.0, 0.0

            # Grid repulsion: vector points from nearest grid surface toward snake.
            if g_left <= x <= g_right and g_top <= y <= g_bot:
                # Inside grid — emergency eject toward nearest wall.
                d_l = x - g_left;  d_r = g_right - x
                d_t = y - g_top;   d_b = g_bot   - y
                m   = min(d_l, d_r, d_t, d_b)
                rx  = -8.0 if m == d_l else (8.0 if m == d_r else 0.0)
                ry  = -8.0 if m == d_t else (8.0 if m == d_b else 0.0)
            else:
                nx   = max(g_left, min(g_right, x))
                ny   = max(g_top,  min(g_bot,   y))
                dvx, dvy = x - nx, y - ny
                dist = max(0.5, (dvx * dvx + dvy * dvy) ** 0.5)
                if dist < GMARGIN:
                    strength = (1.0 - dist / GMARGIN) ** 2 * 5.5
                    rx += (dvx / dist) * strength
                    ry += (dvy / dist) * strength

            # Screen-edge repulsion.
            if   x < SM:               rx += (SM - x) / SM * 5.0
            elif x > WINDOW_WIDTH - SM: rx -= (x - (WINDOW_WIDTH  - SM)) / SM * 5.0
            if   y < SM:               ry += (SM - y) / SM * 5.0
            elif y > WINDOW_HEIGHT- SM: ry -= (y - (WINDOW_HEIGHT - SM)) / SM * 5.0

            if rx != 0.0 or ry != 0.0:
                repel_angle = math.atan2(ry, rx)
                repel_str   = (rx * rx + ry * ry) ** 0.5
                blend = min(1.0, repel_str / 3.5)
                snake["target_angle"] = (repel_angle
                                         + (1.0 - blend) * random.uniform(-0.25, 0.25))

            # ---- smooth angle rotation --------------------------------------
            da = snake["target_angle"] - snake["angle"]
            while da >  math.pi: da -= math.tau
            while da < -math.pi: da += math.tau
            turn_max = snake["turn_rate"] * dt
            snake["angle"] += max(-turn_max, min(turn_max, da))

            # ---- move head --------------------------------------------------
            spd_mod = 1.0 + 0.14 * math.sin(ticks / 1800.0 + snake["phase"])
            snake["x"] += math.cos(snake["angle"]) * speed * spd_mod * dt
            snake["y"] += math.sin(snake["angle"]) * speed * spd_mod * dt

            # ---- screen wrap (reset history to avoid visual jump) -----------
            WRAP = 85.0
            if snake["x"] < -WRAP or snake["x"] > WINDOW_WIDTH + WRAP or \
               snake["y"] < -WRAP or snake["y"] > WINDOW_HEIGHT + WRAP:
                snake["x"] = max(-WRAP + 1, min(WINDOW_WIDTH + WRAP - 1, snake["x"]))
                snake["y"] = max(-WRAP + 1, min(WINDOW_HEIGHT + WRAP - 1, snake["y"]))
                # Pick a re-entry angle that points back toward the poison zone.
                cx_mid = WINDOW_WIDTH  / 2
                cy_mid = WINDOW_HEIGHT / 2
                snake["angle"] = math.atan2(cy_mid - snake["y"], cx_mid - snake["x"])
                snake["target_angle"] = snake["angle"]
                snake["history"] = deque([(snake["x"], snake["y"])] * 300, maxlen=300)

            # ---- update position history ------------------------------------
            snake["history"].appendleft((snake["x"], snake["y"]))

            # ---- build segment positions from history (arc-length) ----------
            length = snake["length"]
            spacing = max(6.0, 11.0 * sc)
            segments = self._sample_arc_positions(snake["history"], length, spacing)

            # Skip drawing if fully off-screen.
            if not any(-50 < sx < WINDOW_WIDTH + 50 and -50 < sy < WINDOW_HEIGHT + 50
                       for sx, sy in segments):
                continue

            # ---- draw body (tail→head so head is on top) --------------------
            for i in range(length - 1, -1, -1):
                bx, by = int(segments[i][0]), int(segments[i][1])
                taper  = 0.45 + 0.55 * (1.0 - i / length)
                r      = max(3, int((5 + 6 * taper) * sc))
                pygame.draw.circle(self.screen, (14, 54,  8),  (bx, by), r + 2)
                pygame.draw.circle(self.screen, (26, 90, 16),  (bx, by), r + 1)
                pygame.draw.circle(self.screen, (42, 138, 28), (bx, by), r)
                if i < length * 3 // 4:
                    pygame.draw.circle(self.screen, (60, 174, 42),
                                       (bx, by + max(1, r // 4)), max(1, r - 3))

            # ---- head -------------------------------------------------------
            hx, hy = int(segments[0][0]), int(segments[0][1])
            hr     = max(5, int(11 * sc))
            pygame.draw.circle(self.screen, (10, 44,  6),  (hx, hy), hr + 3)
            pygame.draw.circle(self.screen, (22, 82, 14),  (hx, hy), hr + 1)
            pygame.draw.circle(self.screen, (38, 126, 24), (hx, hy), hr)

            if len(segments) > 1:
                fdx = segments[0][0] - segments[1][0]
                fdy = segments[0][1] - segments[1][1]
                fdn = max(0.01, (fdx * fdx + fdy * fdy) ** 0.5)
                fdx /= fdn;  fdy /= fdn
                fpx, fpy = -fdy, fdx

                # Snout.
                pygame.draw.circle(self.screen, (30, 105, 20),
                    (int(hx + fdx * hr * 0.6), int(hy + fdy * hr * 0.6)), max(3, hr - 4))

                # Eyes with slit pupils.
                for sign in (-1, 1):
                    ex = int(hx + fpx * sign * max(2, int(5 * sc)) + fdx * 3)
                    ey = int(hy + fpy * sign * max(2, int(5 * sc)) + fdy * 3)
                    pygame.draw.circle(self.screen, (0, 240, 80), (ex, ey), max(2, int(3 * sc)))
                    pygame.draw.line(self.screen, (0, 0, 0),
                        (int(ex - fdx * 1.2), int(ey - fdy * 1.2)),
                        (int(ex + fdx * 1.2), int(ey + fdy * 1.2)),
                        max(1, int(1.5 * sc)))

                # Tongue.
                body_phase = ticks / 380.0 + snake["phase"]
                if math.sin(body_phase * 1.6) > 0.6:
                    tbx = int(hx + fdx * (hr + 3) * sc)
                    tby = int(hy + fdy * (hr + 3) * sc)
                    tmx = int(tbx + fdx * 6 * sc)
                    tmy = int(tby + fdy * 6 * sc)
                    w2  = max(1, int(1.5 * sc))
                    pygame.draw.line(self.screen, (215, 28, 28), (tbx, tby), (tmx, tmy), w2)
                    for sign in (-1, 1):
                        fx = int(tmx + (fdx * 5 + fpx * sign * 4) * sc)
                        fy = int(tmy + (fdy * 5 + fpy * sign * 4) * sc)
                        pygame.draw.line(self.screen, (215, 28, 28), (tmx, tmy), (fx, fy), w2)

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    def _draw_grid(self) -> None:
        """Draw the full arena grid and its lighting passes."""

        for row in range(1, SIDE_EXTENSION_ROWS + 1):
            y = self.arena_y - (row * TILE_SIZE)
            row_from_top = SIDE_EXTENSION_ROWS - row + 1
            for col in range(SIDE_EXTENSION_TILES):
                x = self.arena_x + col * TILE_SIZE
                is_castle_border_left = (row in {2, 3}) and (col == SIDE_EXTENSION_TILES - 1)
                is_target_row_left_border = (row_from_top == 4) and (col == SIDE_EXTENSION_TILES - 1)
                self._draw_tile(
                    x,
                    y,
                    left_side=True,
                    border_row=False,
                    top_edge=(
                        row == SIDE_EXTENSION_ROWS
                        and col not in {0, SIDE_EXTENSION_TILES - 1}
                        and not is_castle_border_left
                    ),
                    corner_type=(
                        "top_left"
                        if (row == SIDE_EXTENSION_ROWS and col == 0)
                        else "left_of_castle"
                        if (
                            row == SIDE_EXTENSION_ROWS
                            and col == SIDE_EXTENSION_TILES - 1
                            and not is_castle_border_left
                        )
                        else None
                    ),
                    edge_type=(
                        "right"
                        if is_target_row_left_border
                        else "right"
                        if is_castle_border_left
                        else "left"
                        if col == 0
                        else None
                    ),
                )
            for col in range(self.grid_cols - SIDE_EXTENSION_TILES, self.grid_cols):
                x = self.arena_x + col * TILE_SIZE
                is_castle_border_right = (row in {2, 3}) and (col == self.grid_cols - SIDE_EXTENSION_TILES)
                is_target_row_right_border = (row_from_top == 4) and (col == self.grid_cols - SIDE_EXTENSION_TILES)
                self._draw_tile(
                    x,
                    y,
                    left_side=False,
                    border_row=False,
                    top_edge=(
                        row == SIDE_EXTENSION_ROWS
                        and col not in {self.grid_cols - SIDE_EXTENSION_TILES, self.grid_cols - 1}
                        and not is_castle_border_right
                    ),
                    corner_type=(
                        "right_of_castle"
                        if (
                            row == SIDE_EXTENSION_ROWS
                            and col == self.grid_cols - SIDE_EXTENSION_TILES
                            and not is_castle_border_right
                        )
                        else "top_right"
                        if (row == SIDE_EXTENSION_ROWS and col == self.grid_cols - 1)
                        else None
                    ),
                    edge_type=(
                        "left"
                        if is_target_row_right_border
                        else "left"
                        if is_castle_border_right
                        else "right"
                        if col == self.grid_cols - 1
                        else None
                    ),
                )

        for row in range(GRID_ROWS):
            for col in range(self.grid_cols):
                x = self.arena_x + col * TILE_SIZE
                y = self.arena_y + row * TILE_SIZE
                self._draw_tile(
                    x,
                    y,
                    left_side=(col < self.split_col),
                    border_row=(row == GRID_ROWS - 1),
                    top_edge=(
                        row == 0
                        and SIDE_EXTENSION_TILES <= col < (self.grid_cols - SIDE_EXTENSION_TILES)
                    ),
                    corner_type=(
                        "left"
                        if (row == GRID_ROWS - 1 and col == 0)
                        else "right"
                        if (row == GRID_ROWS - 1 and col == self.grid_cols - 1)
                        else None
                    ),
                    edge_type=(
                        "left"
                        if (col == 0 and row != GRID_ROWS - 1)
                        else "right"
                        if (col == self.grid_cols - 1 and row != GRID_ROWS - 1)
                        else None
                    ),
                )

        ext_h = SIDE_EXTENSION_ROWS * TILE_SIZE
        full_h = self.arena_h + ext_h
        full_y = self.arena_y - ext_h

        global_light = pygame.Surface((self.arena_w, full_h), pygame.SRCALPHA)
        for i in range(full_h):
            a = max(0, 20 - (i // 28))
            pygame.draw.line(global_light, (255, 255, 255, a), (0, i), (self.arena_w, i), 1)
        for i in range(self.arena_w):
            a = max(0, 16 - (i // 34))
            pygame.draw.line(global_light, (255, 255, 255, a), (i, 0), (i, full_h), 1)
        self.screen.blit(global_light, (self.arena_x, full_y))

        global_shadow = pygame.Surface((self.arena_w, full_h), pygame.SRCALPHA)
        for i in range(full_h):
            a = max(0, 16 - ((full_h - i) // 30))
            pygame.draw.line(global_shadow, (0, 0, 0, a), (0, i), (self.arena_w, i), 1)
        self.screen.blit(global_shadow, (self.arena_x, full_y))
        self._draw_bottom_tile_faces()

    # ==================================================================
    # Gameplay: networking
    # ==================================================================

    def _connect_to_server(self) -> None:
        try:
            self.connection = ClientConnection(server_ip=self.server_ip, server_port=self.server_port)
        except Exception as err:
            self._mark_connection_issue(f"Unable to connect: {err}")
            return
        self.last_server_message = f"Connected to {self.server_ip}:{self.server_port}"
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receiver_thread.start()
        self.connection.send_message(make_username_message(self.username))

    def _disconnect_from_server(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None

    def _receiver_loop(self) -> None:
        while self.running and self.connection is not None:
            try:
                msg = self.connection.receive_message()
            except Exception as err:
                self.incoming_queue.put({"type": "socket_error", "payload": {"message": str(err)}})
                break
            self.incoming_queue.put(msg)

    def _drain_incoming_queue(self) -> None:
        while True:
            try:
                msg = self.incoming_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_server_message(msg)

    def _handle_server_message(self, message: dict) -> None:
        msg_type = message.get("type")
        payload  = message.get("payload", {})

        if msg_type == MessageType.CONNECT.value:
            self.last_server_message = f"Session: {payload.get('client_id', 'unknown')}"
            return

        if msg_type == MessageType.ONLINE_USERS.value:
            self.online_users = list(payload.get("users", []))
            self.username_confirmed = any(
                u.casefold() == self.username.casefold() for u in self.online_users
            )
            self.last_server_message = f"Online: {len(self.online_users)}"
            if self.spectator_mode:
                self._maybe_request_spectate()
            else:
                self._maybe_send_auto_invite()
            return

        if msg_type == MessageType.INVITATION.value:
            action    = str(payload.get("action", "")).lower()
            from_user = str(payload.get("from_user", ""))
            to_user   = str(payload.get("to_user", ""))
            if action == "send" and to_user.casefold() == self.username.casefold():
                if self.spectator_mode:
                    if self.connection is not None:
                        self.connection.send_message(
                            make_invitation_message(from_user=from_user, to_user=self.username, action="decline")
                        )
                    return
                if self.connection is not None:
                    self.connection.send_message(
                        make_invitation_message(from_user=from_user, to_user=self.username, action="accept")
                    )
                self.last_server_message = f"Accepted invite from {from_user}"
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
                return
            if action in {"declined", "cancelled"}:
                self.pending_invite_to = None
            return

        if msg_type == MessageType.GAME_STATE.value:
            self.active_game_id = payload.get("game_id", self.active_game_id)
            state = payload.get("state", {})
            self._sync_from_game_state(state)
            self.has_authoritative_state = True
            self.last_server_message = "Game state updated"
            return

        if msg_type == MessageType.CHAT.value:
            sender = str(payload.get("sender", "SERVER"))
            text   = str(payload.get("message", ""))
            if str(payload.get("scope", "")).lower() == "lobby":
                return
            self.chat_messages.append(f"{sender}: {text}")
            return

        if msg_type == MessageType.GAME_OVER.value:
            self.show_result_screen = True
            self.result_winner = str(payload.get("winner", "-"))
            self.result_reason = str(payload.get("reason", "-"))
            self.result_scores = dict(payload.get("final_scores", {}))
            self.last_server_message = f"Game over. Winner: {self.result_winner}"
            return

        if msg_type == MessageType.ERROR.value:
            self.last_server_message = str(payload.get("message", "Error"))
            low = self.last_server_message.lower()
            if "invitation" in low or "busy" in low or "offline" in low:
                self.pending_invite_to  = None
                self.pending_spectate_to = None
                if self.spectator_mode:
                    self._maybe_request_spectate()
                else:
                    self._maybe_send_auto_invite()
            return

        if msg_type == "socket_error":
            self._mark_connection_issue(str(payload.get("message", "Lost connection.")))

    def _mark_connection_issue(self, message: str) -> None:
        self.connection_healthy  = False
        self.connection_notice   = message
        self.last_server_message = message
        self.match_status = "disconnected"
        self._disconnect_from_server()

    def _maybe_send_auto_invite(self) -> None:
        if self.connection is None or not self.username_confirmed or self.active_game_id is not None:
            return
        if self.pending_invite_to is not None:
            return
        opponents = [u for u in self.online_users if u.casefold() != self.username.casefold()]
        if not opponents:
            return
        preferred = self.preferred_opponent
        if preferred:
            matching = [u for u in opponents if u.casefold() == preferred.casefold()]
            if matching:
                opponent = matching[0]
                if self.username.casefold() > opponent.casefold():
                    return
            else:
                opponent = opponents[0]
        else:
            opponent = opponents[0]
        self.connection.send_message(
            make_invitation_message(from_user=self.username, to_user=opponent, action="send")
        )
        self.pending_invite_to      = opponent
        self.pending_invite_sent_ms = pygame.time.get_ticks()
        self.last_server_message    = f"Invited {opponent}"

    def _maybe_request_spectate(self) -> None:
        if self.connection is None or not self.username_confirmed or self.active_game_id is not None:
            return
        if self.pending_spectate_to is not None:
            return
        targets = [u for u in self.online_users if u.casefold() != self.username.casefold()]
        if not targets:
            return
        preferred = self.preferred_opponent
        target = (
            next((u for u in targets if u.casefold() == preferred.casefold()), targets[0])
            if preferred else targets[0]
        )
        self.connection.send_message(
            make_invitation_message(from_user=self.username, to_user=target, action="spectate")
        )
        self.pending_spectate_to    = target
        self.last_server_message    = f"Spectating {target}..."

    # ==================================================================
    # Gameplay: state sync + interpolation
    # ==================================================================

    def _sync_from_game_state(self, state: dict) -> None:
        old_a = list(self.snake_a)
        old_b = list(self.snake_b)
        snakes = state.get("snakes", [])
        my_snake  = next((s for s in snakes if str(s.get("player", "")).casefold() == self.username.casefold()), None)
        opp_snake = next((s for s in snakes if str(s.get("player", "")).casefold() != self.username.casefold()), None)
        if my_snake is None and snakes:
            my_snake = snakes[0]
        if opp_snake is None and len(snakes) >= 2:
            opp_snake = snakes[1]

        if my_snake is not None:
            self.player_a_name = str(my_snake.get("player", self.player_a_name))
            body = my_snake.get("body", [])
            if body:
                self.snake_a = [(int(seg.get("x", 0)), int(seg.get("y", 0))) for seg in body]
            self.snake_a_health = int(my_snake.get("health", self.snake_a_health))

        if opp_snake is not None:
            self.player_b_name = str(opp_snake.get("player", self.player_b_name))
            body = opp_snake.get("body", [])
            if body:
                self.snake_b = [(int(seg.get("x", 0)), int(seg.get("y", 0))) for seg in body]
            self.snake_b_health = int(opp_snake.get("health", self.snake_b_health))

        pies_raw = state.get("pies", [])
        if pies_raw:
            self.game_pies = [
                (px, py)
                for p in pies_raw
                for px, py in [(
                    int(p.get("position", {}).get("x", 0)),
                    int(p.get("position", {}).get("y", 0)),
                )]
                if not self._is_out_of_bounds(px, py)
            ]

        # Blob-spawned pies live only on the client — the server's authoritative
        # snake update won't remove them. Consume any pie a snake head just moved onto.
        self._consume_local_pies_on_heads()

        obs_raw = state.get("obstacles", [])
        if obs_raw:
            self.game_obstacles = [
                (int(o.get("position", {}).get("x", 0)), int(o.get("position", {}).get("y", 0)))
                for o in obs_raw
            ]

        timer = state.get("timer", {})
        self.timer_remaining = int(timer.get("remaining_seconds", self.timer_remaining))
        self.timer_elapsed   = int(timer.get("elapsed_seconds",   self.timer_elapsed))
        self.match_status    = str(state.get("status", self.match_status))
        self.match_winner    = str(state.get("winner") or self.match_winner)
        self._start_state_interpolation(old_a, old_b)

    def _start_state_interpolation(self, old_a: list, old_b: list) -> None:
        def to_float(cells: list) -> list:
            return [(float(x), float(y)) for x, y in cells]

        to_a = to_float(self.snake_a)
        to_b = to_float(self.snake_b)
        from_a = (list(self.render_snake_a) if len(self.render_snake_a) == len(to_a) and self.render_snake_a
                  else to_float(old_a or self.snake_a))
        from_b = (list(self.render_snake_b) if len(self.render_snake_b) == len(to_b) and self.render_snake_b
                  else to_float(old_b or self.snake_b))
        if len(from_a) != len(to_a):
            from_a = list(to_a)
        if len(from_b) != len(to_b):
            from_b = list(to_b)
        self.interp_from_snake_a   = from_a
        self.interp_to_snake_a     = to_a
        self.interp_from_snake_b   = from_b
        self.interp_to_snake_b     = to_b
        self.state_interp_start_ms = pygame.time.get_ticks()
        self.render_snake_a = list(from_a)
        self.render_snake_b = list(from_b)

    def _update_interpolated_snakes(self) -> None:
        if not self.interp_to_snake_a and not self.interp_to_snake_b:
            return
        elapsed = pygame.time.get_ticks() - self.state_interp_start_ms
        t = max(0.0, min(1.0, elapsed / STATE_INTERPOLATION_MS)) if STATE_INTERPOLATION_MS > 0 else 1.0

        def lerp(start: list, end: list) -> list:
            if len(start) != len(end):
                return list(end)
            return [(sx + (ex - sx) * t, sy + (ey - sy) * t) for (sx, sy), (ex, ey) in zip(start, end)]

        self.render_snake_a = lerp(self.interp_from_snake_a, self.interp_to_snake_a)
        self.render_snake_b = lerp(self.interp_from_snake_b, self.interp_to_snake_b)

    # ==================================================================
    # Gameplay: movement + input
    # ==================================================================

    def _handle_direction_input(self, key: int) -> None:
        direction_map = {
            pygame.K_UP: "up",    pygame.K_w: "up",
            pygame.K_DOWN: "down", pygame.K_s: "down",
            pygame.K_LEFT: "left", pygame.K_a: "left",
            pygame.K_RIGHT: "right", pygame.K_d: "right",
        }
        if key in direction_map:
            requested = direction_map[key]
            if _OPPOSITE_DIRECTION.get(self.snake_a_direction) != requested:
                self.snake_a_direction = requested

    def _update_movement(self) -> None:
        if self.show_result_screen or not self.connection_healthy:
            return
        if self.spectator_mode:
            return
        now = pygame.time.get_ticks()
        if now - self.last_move_ms < MOVE_INTERVAL_MS:
            if (self.active_game_id is None and self.pending_invite_to is not None
                    and now - self.pending_invite_sent_ms > 1500):
                self.pending_invite_to = None
                self._maybe_send_auto_invite()
            return
        self.last_move_ms = now
        if not self.has_authoritative_state:
            self._step_local_physics()
            self._check_local_game_over()
            if self.show_result_screen:
                return
        self._send_movement_command(self.snake_a_direction)

    def _send_movement_command(self, direction: str) -> None:
        if self.connection is None or not self.username_confirmed or self.active_game_id is None:
            return
        try:
            self.connection.send_message(make_movement_message(player=self.username, direction=direction))
        except OSError as err:
            self._mark_connection_issue(f"Move send failed: {err}")

    # ==================================================================
    # Gameplay: local physics fallback (used before server state arrives)
    # ==================================================================

    def _is_out_of_bounds(self, x: int, y: int) -> bool:
        # Outer walls
        if x < 1 or x >= GAME_BOARD_COLS - 1 or y < 1 or y >= GAME_BOARD_ROWS - 1:
            return True
        # Centre-top gap (serpent's domain between the two wings) is impassable.
        # The left (cols 1-4) and right (cols 15-18) wings at rows 1-6 ARE valid island tiles.
        if GAP_COL_START <= x < GAP_COL_END and y < GAP_ROW_END:
            return True
        return False

    def _pushback_cell(self, hx: int, hy: int, dx: int, dy: int) -> tuple[int, int]:
        return (hx - dx, hy - dy)

    def _reset_local_round_layout(self) -> None:
        self.game_obstacles = list(STATIC_OBSTACLES_GAME)
        self.game_pies = []  # berries come only from serpent blob landings

    def _consume_local_pies_on_heads(self) -> None:
        """Remove any blob-spawned pie that sits under a snake head and heal locally.

        Needed because blob pies are client-only; the server's snake step won't
        flag them as eaten, so without this they'd phase right through the snake.
        """

        if not self.game_pies:
            return
        pies = set(self.game_pies)
        eaten: set[tuple[int, int]] = set()
        if self.snake_a:
            head_a = self.snake_a[0]
            if head_a in pies:
                eaten.add(head_a)
                self.snake_a_health += PIE_HEALTH_GAIN
        if self.snake_b:
            head_b = self.snake_b[0]
            if head_b in pies:
                eaten.add(head_b)
                self.snake_b_health += PIE_HEALTH_GAIN
        if eaten:
            self.game_pies = [p for p in self.game_pies if p not in eaten]

    def _adjust_snake_length(self, snake_cells: list, health: int) -> None:
        growth = max(0, (health - LOCAL_INITIAL_HEALTH) // HEALTH_PER_GROWTH_SEGMENT)
        target = BASE_SNAKE_LENGTH + growth
        if len(snake_cells) > target:
            del snake_cells[target:]
        elif len(snake_cells) < target and snake_cells:
            snake_cells.extend([snake_cells[-1]] * (target - len(snake_cells)))

    def _step_preview_snake(
        self,
        snake_cells: list,
        direction: str,
        health_attr: str,
        opponent_cells: list,
    ) -> None:
        dx, dy   = _DIRECTIONS[direction]
        hx, hy   = snake_cells[0]
        next_head = (hx + dx, hy + dy)
        health    = int(getattr(self, health_attr))

        hit_wall = self._is_out_of_bounds(*next_head)
        hit_obs  = next_head in self.game_obstacles or hit_wall
        hit_opp  = next_head in opponent_cells

        if hit_obs or hit_opp:
            dmg    = OBSTACLE_COLLISION_DAMAGE if hit_obs else SNAKE_COLLISION_DAMAGE
            health = max(0, health - dmg)
            pb = self._pushback_cell(hx, hy, dx, dy)
            if not (self._is_out_of_bounds(*pb) or pb in self.game_obstacles
                    or pb in opponent_cells or pb in snake_cells):
                snake_cells.insert(0, pb)
                snake_cells.pop()
            setattr(self, health_attr, health)
            self._adjust_snake_length(snake_cells, health)
            return

        ate = next_head in self.game_pies
        snake_cells.insert(0, next_head)
        if ate:
            health += PIE_HEALTH_GAIN
            self.game_pies.remove(next_head)
        else:
            snake_cells.pop()
        self._adjust_snake_length(snake_cells, health)
        setattr(self, health_attr, health)

    def _step_local_physics(self) -> None:
        self._step_preview_snake(self.snake_a, self.snake_a_direction, "snake_a_health", self.snake_b)
        self._step_preview_snake(self.snake_b, self.snake_b_direction, "snake_b_health", self.snake_a)

    def _check_local_game_over(self) -> None:
        if self.snake_a_health > 0 and self.snake_b_health > 0:
            return
        self.match_status     = "finished"
        self.show_result_screen = True
        self.result_reason    = "health_depleted"
        self.result_scores    = {
            self.player_a_name: self.snake_a_health,
            self.player_b_name: self.snake_b_health,
        }
        if self.snake_a_health <= 0 and self.snake_b_health <= 0:
            self.result_winner = "Draw"
        elif self.snake_a_health <= 0:
            self.result_winner = self.player_b_name
        else:
            self.result_winner = self.player_a_name

    # ==================================================================
    # Gameplay: rendering
    # ==================================================================

    def _game_to_pixel(self, col: float, row: float) -> tuple[float, float]:
        return (
            self.game_origin_x + col * GAME_CELL_W + GAME_CELL_W * 0.5,
            self.game_origin_y + row * GAME_CELL_H + GAME_CELL_H * 0.5,
        )

    def _draw_game_snakes(self) -> None:
        if self.has_authoritative_state:
            self._update_interpolated_snakes()
            self._draw_single_game_snake(self.render_snake_a, SNAKE_A_BODY, SNAKE_A_HEAD, SNAKE_A_GLOW)
            self._draw_single_game_snake(self.render_snake_b, SNAKE_B_BODY, SNAKE_B_HEAD, SNAKE_B_GLOW)
        else:
            fa = [(float(x), float(y)) for x, y in self.snake_a]
            fb = [(float(x), float(y)) for x, y in self.snake_b]
            self._draw_single_game_snake(fa, SNAKE_A_BODY, SNAKE_A_HEAD, SNAKE_A_GLOW)
            self._draw_single_game_snake(fb, SNAKE_B_BODY, SNAKE_B_HEAD, SNAKE_B_GLOW)

    def _draw_single_game_snake(
        self,
        cells: list,
        body_clr: tuple,
        head_clr: tuple,
        glow_clr: tuple,
    ) -> None:
        if not cells:
            return
        r = max(5, int(GAME_CELL_H * 0.5) - 1)   # fits inside 23.4 px cell height
        centers = [self._game_to_pixel(x, y) for x, y in cells]

        # Connecting tube drawn first so circles sit on top
        for i in range(len(centers) - 1):
            ax, ay = int(centers[i][0]),   int(centers[i][1])
            bx, by = int(centers[i+1][0]), int(centers[i+1][1])
            pygame.draw.line(self.screen, body_clr, (ax, ay), (bx, by), r * 2 - 2)

        # Draw body circles tail→head
        for i in range(len(centers) - 1, -1, -1):
            cx, cy = int(centers[i][0]), int(centers[i][1])
            if i == 0:
                # Glow halo
                gs = pygame.Surface((r * 4 + 6, r * 4 + 6), pygame.SRCALPHA)
                pygame.draw.circle(gs, glow_clr, (r * 2 + 3, r * 2 + 3), r + 6)
                self.screen.blit(gs, (cx - r * 2 - 3, cy - r * 2 - 3))
                # Head
                pygame.draw.circle(self.screen, head_clr, (cx, cy), r + 2)
                pygame.draw.circle(self.screen, body_clr, (cx, cy), r)
                # Eyes
                if len(centers) > 1:
                    ddx = centers[0][0] - centers[1][0]
                    ddy = centers[0][1] - centers[1][1]
                    dn  = max(0.01, (ddx * ddx + ddy * ddy) ** 0.5)
                    ddx /= dn;  ddy /= dn
                    px, py = -ddy, ddx
                    for sign in (-1, 1):
                        ex = int(cx + px * sign * (r * 0.5) + ddx * r * 0.35)
                        ey = int(cy + py * sign * (r * 0.5) + ddy * r * 0.35)
                        pygame.draw.circle(self.screen, (255, 255, 255), (ex, ey), max(2, r // 4))
                        pygame.draw.circle(self.screen, (0, 0, 0),       (ex, ey), max(1, r // 7))
            else:
                taper = 0.55 + 0.45 * (i / len(centers))
                cr    = max(3, int(r * taper))
                pygame.draw.circle(self.screen, body_clr, (cx, cy), cr)

    def _draw_game_pies(self) -> None:
        ticks = pygame.time.get_ticks()
        for col, row in self.game_pies:
            cx, cy = self._game_to_pixel(col, row)
            cx, cy = int(cx), int(cy)
            pulse = 0.7 + 0.3 * math.sin(ticks / 400.0 + col * 0.7 + row * 1.3)
            r = max(4, int(GAME_CELL_H * 0.4 - 1 + 2 * pulse))
            # Glow
            gs = pygame.Surface((r * 4 + 4, r * 4 + 4), pygame.SRCALPHA)
            pygame.draw.circle(gs, (*BERRY_COLOR, int(75 * pulse)), (r * 2 + 2, r * 2 + 2), r + 5)
            self.screen.blit(gs, (cx - r * 2 - 2, cy - r * 2 - 2))
            # Berry body
            pygame.draw.circle(self.screen, (25, 90, 15),   (cx, cy), r + 1)
            pygame.draw.circle(self.screen, BERRY_COLOR,    (cx, cy), r)
            pygame.draw.circle(self.screen, (240, 255, 200), (cx - r // 3, cy - r // 3), max(2, r // 3))

    def _draw_hud(self) -> None:
        """Minimal HUD: slim top bar + optional chat overlay."""

        BAR_H  = 34
        BAR_W  = 200   # health bar pixel width
        MARGIN = 12
        mid_y  = BAR_H // 2

        # ── Semi-transparent top strip ──────────────────────────────
        strip = pygame.Surface((WINDOW_WIDTH, BAR_H), pygame.SRCALPHA)
        strip.fill((8, 10, 20, 210))
        self.screen.blit(strip, (0, 0))

        # ── Player A — left side ─────────────────────────────────────
        a_frac = max(0.0, min(1.0, self.snake_a_health / LOCAL_INITIAL_HEALTH))
        a_clr  = SNAKE_A_BODY if a_frac > 0.3 else HUD_DANGER
        pygame.draw.circle(self.screen, a_clr, (MARGIN, mid_y), 5)
        n_a = self.font_hud_sm.render(self.player_a_name[:14], True, HUD_TEXT)
        self.screen.blit(n_a, (MARGIN + 10, mid_y - n_a.get_height() // 2))
        bx_a = MARGIN + 10 + n_a.get_width() + 8
        pygame.draw.rect(self.screen, (28, 34, 48), (bx_a, mid_y - 5, BAR_W, 10), border_radius=3)
        fw_a = max(0, int(BAR_W * a_frac))
        if fw_a:
            pygame.draw.rect(self.screen, a_clr, (bx_a, mid_y - 5, fw_a, 10), border_radius=3)
        hp_a = self.font_hud_sm.render(str(max(0, self.snake_a_health)), True, a_clr)
        self.screen.blit(hp_a, (bx_a + BAR_W + 5, mid_y - hp_a.get_height() // 2))

        # ── Player B — right side ────────────────────────────────────
        b_frac = max(0.0, min(1.0, self.snake_b_health / LOCAL_INITIAL_HEALTH))
        b_clr  = SNAKE_B_BODY if b_frac > 0.3 else HUD_DANGER
        pygame.draw.circle(self.screen, b_clr, (WINDOW_WIDTH - MARGIN, mid_y), 5)
        n_b = self.font_hud_sm.render(self.player_b_name[:14], True, HUD_TEXT)
        self.screen.blit(n_b, (WINDOW_WIDTH - MARGIN - 10 - n_b.get_width(), mid_y - n_b.get_height() // 2))
        bx_b_end = WINDOW_WIDTH - MARGIN - 10 - n_b.get_width() - 8
        bx_b = bx_b_end - BAR_W
        pygame.draw.rect(self.screen, (28, 34, 48), (bx_b, mid_y - 5, BAR_W, 10), border_radius=3)
        fw_b = max(0, int(BAR_W * b_frac))
        if fw_b:
            pygame.draw.rect(self.screen, b_clr, (bx_b + BAR_W - fw_b, mid_y - 5, fw_b, 10), border_radius=3)
        hp_b = self.font_hud_sm.render(str(max(0, self.snake_b_health)), True, b_clr)
        self.screen.blit(hp_b, (bx_b - hp_b.get_width() - 5, mid_y - hp_b.get_height() // 2))

        # ── Timer — centre ───────────────────────────────────────────
        cx = WINDOW_WIDTH // 2
        if self.has_authoritative_state and self.timer_remaining > 0:
            t_str = f"{self.timer_remaining // 60}:{self.timer_remaining % 60:02d}"
        elif self.active_game_id:
            t_str = "—"
        else:
            t_str = "lobby"
        t_surf = self.font_hud.render(t_str, True, HUD_ACCENT)
        self.screen.blit(t_surf, (cx - t_surf.get_width() // 2, mid_y - t_surf.get_height() // 2))

        # ── Chat overlay — bottom of screen ─────────────────────────
        msgs = list(self.chat_messages)[-4:]
        if msgs or self.chat_typing:
            lines = msgs + ([f"> {self.chat_input}_"] if self.chat_typing else [])
            ch = len(lines) * 18 + 8
            cy_chat = WINDOW_HEIGHT - ch - 4
            bg = pygame.Surface((620, ch), pygame.SRCALPHA)
            bg.fill((6, 8, 16, 185))
            self.screen.blit(bg, (self.arena_x + 8, cy_chat))
            for i, line in enumerate(lines):
                clr = HUD_ACCENT if (self.chat_typing and i == len(lines) - 1) else HUD_DIM
                ls  = self.font_hud_sm.render(line[:90], True, clr)
                self.screen.blit(ls, (self.arena_x + 14, cy_chat + 4 + i * 18))

    def _draw_result_screen(self) -> None:
        ticks = pygame.time.get_ticks()

        # Semi-transparent backdrop
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 6, 210))
        self.screen.blit(overlay, (0, 0))

        # Determine outcome from local player's perspective
        win_raw = str(self.result_winner)
        is_draw = win_raw.casefold() in {"none", "draw", "-", ""}
        player_won = not is_draw and win_raw == self.username

        if is_draw:
            title_text = "DRAW"
            title_clr  = (255, 215, 60)
            accent     = (210, 180, 50)
        elif player_won:
            title_text = "VICTORY"
            title_clr  = (80, 255, 130)
            accent     = (50, 215, 90)
        else:
            title_text = "DEFEAT"
            title_clr  = (255, 90, 80)
            accent     = (200, 55, 55)

        # Card
        card_w, card_h = 580, 330
        cx = (WINDOW_WIDTH  - card_w) // 2
        cy = (WINDOW_HEIGHT - card_h) // 2
        card = pygame.Rect(cx, cy, card_w, card_h)
        pygame.draw.rect(self.screen, (8, 12, 20), card, border_radius=18)

        # Animated border (gentle pulse)
        pulse = 0.55 + 0.45 * math.sin(ticks / 480.0)
        bc = tuple(min(255, int(c * pulse)) for c in accent)
        pygame.draw.rect(self.screen, bc, card, width=3, border_radius=18)

        mid = card.centerx

        # Big outcome title
        t_surf = self.font_result_title.render(title_text, True, title_clr)
        self.screen.blit(t_surf, (mid - t_surf.get_width() // 2, card.y + 22))

        # Thin divider
        div_y = card.y + 96
        pygame.draw.line(self.screen, (*accent, 80), (card.x + 50, div_y), (card.right - 50, div_y))

        # Resolve health values
        a_name = self.player_a_name
        b_name = self.player_b_name
        a_hp = int(self.result_scores.get(a_name, self.snake_a_health))
        b_hp = int(self.result_scores.get(b_name, self.snake_b_health))

        row_y = div_y + 20
        left_x  = card.x + 50
        right_x = card.right - 50

        # Player A — left
        a_name_surf = self.font_result_name.render(a_name[:16], True, HUD_HEALTH_A)
        a_hp_clr    = HUD_HEALTH_A if a_hp > 0 else HUD_DANGER
        a_hp_surf   = self.font_result_hp.render(f"{a_hp} HP", True, a_hp_clr)
        self.screen.blit(a_name_surf, (left_x, row_y))
        self.screen.blit(a_hp_surf,   (left_x, row_y + 30))

        # "vs" centre
        vs_surf = self.font_result_body.render("vs", True, HUD_DIM)
        self.screen.blit(vs_surf, (mid - vs_surf.get_width() // 2, row_y + 16))

        # Player B — right
        b_name_surf = self.font_result_name.render(b_name[:16], True, HUD_HEALTH_B)
        b_hp_clr    = HUD_HEALTH_B if b_hp > 0 else HUD_DANGER
        b_hp_surf   = self.font_result_hp.render(f"{b_hp} HP", True, b_hp_clr)
        self.screen.blit(b_name_surf, (right_x - b_name_surf.get_width(), row_y))
        self.screen.blit(b_hp_surf,   (right_x - b_hp_surf.get_width(),   row_y + 30))

        # Winner name (skipped for draw)
        if not is_draw:
            winner_label = self.font_result_body.render(
                f"{win_raw} wins", True, accent)
            self.screen.blit(winner_label, (mid - winner_label.get_width() // 2, row_y + 72))

        # Hint strip
        hint = self.font_hud_sm.render("R  return to lobby     ESC  quit", True, HUD_DIM)
        self.screen.blit(hint, (mid - hint.get_width() // 2, card.bottom - 34))

    def _launch_lobby(self) -> None:
        try:
            from client.gui import ArenaGuiApp
            app = ArenaGuiApp()
            app.server_ip_var.set(self.server_ip)
            app.server_port_var.set(str(self.server_port))
            app.username_var.set(self.username)
            app._retry_same_username = True
            app.root.after(450, app._connect)
            app.run()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Frame composition
    # ------------------------------------------------------------------

    def _draw_frame(self) -> None:
        """Render complete arena scene plus live gameplay entities."""

        # Compute frame delta-time once for any motion that needs it.
        now = pygame.time.get_ticks()
        self._step_dt = max(0.001, min(0.1, (now - self._last_tick_ms) / 1000.0))
        self._last_tick_ms = now

        self.screen.fill(SKY_BG_COLOR)

        self._draw_lava()
        self._draw_snakes()          # decorative pool snakes swim in poison
        self._draw_lava_rocks()      # rocks float on top
        self._draw_arena_serpent()   # giant serpent throws blobs that spawn berries
        self._draw_grid()            # tile island
        self._draw_obstacles()       # decorative mushrooms / skulls
        self._draw_blobs()           # poison projectiles (also spawn game berries)

        # ── Live gameplay entities ──────────────────────────────────────
        self._draw_game_pies()       # health berries (eat to regenerate health)
        self._draw_game_snakes()     # player snakes

        # ── HUD + overlays ─────────────────────────────────────────────
        self._draw_hud()
        if self.show_result_screen:
            self._draw_result_screen()


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
