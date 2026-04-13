"""Arena renderer rebuilt as a clean 2.5D tile map scene.

This version intentionally focuses on environment art only:
- no gameplay HUD
- no scores/chat text
- no characters/snake entities
- centered camera with split battlefield + castle bases
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pygame

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
        # Edge-aware opacity with hole-filling:
        # 1) Flood-fill transparent pixels connected to image borders (true outside bg).
        # 2) Everything not in outside bg is considered castle interior and made opaque.
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
                    # True background remains transparent.
                    continue
                if a > 0:
                    opaque.set_at((x, y), (r, g, b, 255))
                else:
                    # Interior transparent holes become opaque backing.
                    opaque.set_at((x, y), filler)

        # Detect eye pixels on original colors BEFORE tinting.
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

        # Tint castle toward terrain's purple-brown palette so it blends with
        # the arena tiles instead of looking like a separate gray asset.
        tinted = pygame.Surface((w, h), pygame.SRCALPHA)
        # Target hue matches terrain tiles (~325 deg purple-brown).
        tint_r, tint_g, tint_b = 82, 48, 66
        for y in range(h):
            for x in range(w):
                r, g, b, a = opaque.get_at((x, y))
                if a == 0:
                    continue
                luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
                # Map: use original luma as intensity, replace color with
                # terrain hue. Gamma curve lifts shadows for visible detail.
                t = (luma / 160.0) ** 0.65
                nr = min(255, int(tint_r * t * 1.8 + 12))
                ng = min(255, int(tint_g * t * 1.8 + 8))
                nb = min(255, int(tint_b * t * 1.8 + 10))
                tinted.set_at((x, y), (nr, ng, nb, a))

        # Paint eye pixels and add glow halos.
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
        # Use a hard cut (no feathered alpha) to prevent washed-out blending.
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

        # Trim transparent margins so castle fills width cleanly after scaling.
        rect = out.get_bounding_rect(min_alpha=8)
        if rect.width > 0 and rect.height > 0:
            trimmed = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            trimmed.blit(out, (0, 0), rect)
            return trimmed
        return out

    def _build_castle_tile_texture(self, wall: pygame.Surface | None) -> pygame.Surface | None:
        """Build dark brick texture matching the provided reference style."""

        tex = pygame.Surface((TILE_SIZE, TILE_SIZE))
        tex.fill((22, 22, 24))  # mortar / gaps

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
        # Use chroma (max - min channel) to separate neutral-gray background
        # from the purple/brown wall texture. Background chroma ~3-12,
        # wall chroma ~24+. Threshold 15 sits cleanly in the gap.
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
        """Normalize tile colors so all tiles share a consistent palette.

        Computes per-channel scale factors to match a target average RGB,
        ensuring uniform color temperature across all terrain tiles.
        """
        if tex is None:
            return None
        out = tex.copy().convert_alpha()
        w, h = out.get_size()
        # Target: average of inner land's raw profile.
        target_r, target_g, target_b = 80.0, 47.0, 64.0
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
        # Per-channel scale, clamped to avoid extreme correction.
        sr = max(0.85, min(1.2, target_r / avg_r)) if avg_r > 0 else 1.0
        sg = max(0.85, min(1.2, target_g / avg_g)) if avg_g > 0 else 1.0
        sb = max(0.85, min(1.2, target_b / avg_b)) if avg_b > 0 else 1.0
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

    def _load_lava_frames(self) -> list[pygame.Surface]:
        """Load and scale animated lava frames from terrain assets."""

        frames: list[pygame.Surface] = []
        for idx in range(1, 8):
            path = TERRAIN_DIR / f"burninglava{idx}.png"
            if not path.exists():
                continue
            try:
                src = pygame.image.load(str(path)).convert_alpha()
                # Crop tighter into the DOWNMOST lava region (no synthetic filler).
                content = src.get_bounding_rect(min_alpha=8)
                if content.width > 0 and content.height > 0:
                    base_side = min(content.width, content.height)
                    side = max(16, int(base_side * 0.32))  # tiny bit more zoom-in
                    cx = content.x + (content.width - side) // 2
                    cy = content.y + (content.height - side)  # downmost focus
                    square = src.subsurface(pygame.Rect(cx, cy, side, side)).copy()
                else:
                    # Fallback: downmost square crop from full image.
                    w, h = src.get_size()
                    side = max(16, int(min(w, h) * 0.32))
                    cx = (w - side) // 2
                    cy = h - side
                    square = src.subsurface(pygame.Rect(cx, cy, side, side)).copy()
                frames.append(pygame.transform.smoothscale(square, (TILE_SIZE, TILE_SIZE)))
            except pygame.error:
                continue
        return frames

    def _load_lava_rock_frames(self) -> list[pygame.Surface]:
        """Load and scale animated lava rock frames."""

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
                frames.append(pygame.transform.smoothscale(rock, (rock_size, rock_size)))
            except pygame.error:
                continue
        return frames

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

        # Unified castle-themed stone texture for all tiles (single biome).
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

    def _draw_lava(self) -> None:
        """Draw animated lava across arena background; tiles render on top."""

        if not self.lava_frames:
            return

        frame = self.lava_frames[(pygame.time.get_ticks() // 120) % len(self.lava_frames)]
        tile = TILE_SIZE

        # Fill the whole scene with lava animation; grid/castle paint over it.
        for y in range(0, self.screen.get_height(), tile):
            for x in range(0, self.screen.get_width(), tile):
                self.screen.blit(frame, (x, y))

    def _draw_lava_rocks(self) -> None:
        """Draw many animated lava rocks in lava zones, excluding castle vicinity."""

        if not self.lava_rock_frames:
            return
        frame = self.lava_rock_frames[(pygame.time.get_ticks() // 110) % len(self.lava_rock_frames)]
        rw, rh = frame.get_size()

        # Exclude area close to castle.
        castle_layout = self._compute_castle_layout()
        if castle_layout is None:
            castle_exclusion = pygame.Rect(0, 0, 0, 0)
        else:
            cx, cy, cw, ch = castle_layout
            castle_exclusion = pygame.Rect(cx, cy, cw, ch).inflate(TILE_SIZE * 3, TILE_SIZE * 2)

        # Grid area is not lava-visible; skip it.
        grid_rect = pygame.Rect(self.arena_x, self.arena_y - (SIDE_EXTENSION_ROWS * TILE_SIZE), self.arena_w, self.arena_h + (SIDE_EXTENSION_ROWS * TILE_SIZE))

        step_x = max(28, int(TILE_SIZE * 1.05))
        step_y = max(24, int(TILE_SIZE * 0.95))
        for y in range(0, self.screen.get_height(), step_y):
            for x in range(0, self.screen.get_width(), step_x):
                # Deterministic density filter to avoid uniform stamping.
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
        """Compute castle (x, y, width, height) with current sizing rules."""

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

    def _draw_castles(self) -> None:
        """Draw one unified castle wall spanning full arena width above the grid."""

        layout = self._compute_castle_layout()
        if layout is None:
            return
        x, y, scaled_w, scaled_h = layout
        wall = pygame.transform.smoothscale(self.castle_wall, (scaled_w, scaled_h))
        self.screen.blit(wall, (x, y))

    def _draw_grid(self) -> None:
        """Draw the full arena grid and its lighting passes."""

        # 4 extra side-only rows above the main grid:
        # each row has 5 tiles on the left and 5 on the right.
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

        # Tile grid (clear, readable, symmetrical split).
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

        # Global top-left directional light/shadow pass covering the full
        # visible tile area (main grid + side extensions) for uniform lighting.
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

    def _draw_frame(self) -> None:
        """Render complete environment scene."""

        self.screen.fill(SKY_BG_COLOR)

        self._draw_lava()
        self._draw_lava_rocks()
        self._draw_grid()
        # Draw castle after grid/effects so it stays on top (no color bleed under tiles).
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
