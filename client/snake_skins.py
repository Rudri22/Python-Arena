"""Procedural skin draw helpers shared by the lobby preview and gameplay."""

from __future__ import annotations

import math

import pygame

from shared.protocol import SKIN_COLORS, SnakeSkin


def _darken(color: tuple[int, int, int], factor: float = 0.6) -> tuple[int, int, int]:
    return (
        max(0, int(color[0] * factor)),
        max(0, int(color[1] * factor)),
        max(0, int(color[2] * factor)),
    )


def _lighten(color: tuple[int, int, int], delta: int = 60) -> tuple[int, int, int]:
    return (
        min(255, color[0] + delta),
        min(255, color[1] + delta),
        min(255, color[2] + delta),
    )


def draw_snake_pattern(
    surf: pygame.Surface,
    cx: int,
    cy: int,
    radius: int,
    index: int,
    pattern: str,
    body_color: tuple[int, int, int],
) -> None:
    """Overlay a pattern onto one body segment circle already drawn at (cx, cy)."""
    if pattern == "solid" or radius <= 3:
        return
    dark = _darken(body_color, 0.50)
    light = _lighten(body_color, 55)

    if pattern == "scales":
        rect = pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2)
        # Bottom-half dark scale arc
        pygame.draw.arc(surf, dark, rect, math.pi + 0.25, 2 * math.pi - 0.25, max(1, radius // 3))
        # Upper-half highlight arc for 3-D pop
        pygame.draw.arc(surf, light, rect, 0.15, math.pi - 0.15, max(1, radius // 5))

    elif pattern == "stripes":
        w = max(1, radius // 3)
        half = int(radius * 0.78)
        if index % 2 == 0:
            # Dark primary stripe
            pygame.draw.line(surf, dark, (cx - half, cy), (cx + half, cy), w)
            # Thin light accent above
            if w >= 2:
                pygame.draw.line(surf, light, (cx - half + 2, cy - 1), (cx + half - 2, cy - 1), 1)
        else:
            # Alternate segment: lighter mid-tone stripe for visual rhythm
            mid = _darken(body_color, 0.72)
            pygame.draw.line(surf, mid, (cx - int(half * 0.7), cy), (cx + int(half * 0.7), cy), max(1, w - 1))

    elif pattern == "diamond":
        size = max(2, radius // 2)
        outer = [(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)]
        pygame.draw.polygon(surf, dark, outer)
        inner_s = max(1, size // 2)
        inner = [(cx, cy - inner_s), (cx + inner_s, cy), (cx, cy + inner_s), (cx - inner_s, cy)]
        pygame.draw.polygon(surf, light, inner)

    elif pattern == "speckled":
        dot_r = max(1, radius // 4)
        seeds = [
            (37, 23, 7, 3),
            (19, 41, 5, 11),
            (53, 17, 13, 2),
        ]
        for a, b, c, d in seeds:
            dx = (index * a + c) % max(1, (radius - dot_r) * 2) - (radius - dot_r)
            dy = (index * b + d) % max(1, (radius - dot_r) * 2) - (radius - dot_r)
            if dx * dx + dy * dy < (radius - dot_r) ** 2:
                pygame.draw.circle(surf, dark, (cx + dx, cy + dy), dot_r)


def draw_snake_hat(
    surf: pygame.Surface,
    cx: int,
    cy: int,
    radius: int,
    hat: str,
) -> None:
    """Draw the chosen hat sitting on top of a head centered at (cx, cy)."""
    if hat == "none":
        return

    top = cy - radius

    if hat == "helmet":
        dome_rect = pygame.Rect(cx - radius - 2, top - radius, (radius + 2) * 2, radius * 2)
        pygame.draw.rect(surf, (120, 128, 138), dome_rect, border_radius=radius)
        pygame.draw.rect(surf, (70, 78, 88), dome_rect, 2, border_radius=radius)
        # Highlight streak across dome
        hi_rect = pygame.Rect(cx - radius + 4, top - radius + 3, max(4, radius // 2), max(2, radius // 5))
        pygame.draw.ellipse(surf, (180, 190, 202), hi_rect)
        visor_h = max(2, radius // 3)
        visor = pygame.Rect(cx - radius, top + radius - visor_h // 2, radius * 2, visor_h)
        pygame.draw.rect(surf, (24, 30, 38), visor, border_radius=2)
        # Visor cyan tint slit
        inner = visor.inflate(-4, -max(1, visor_h // 3))
        pygame.draw.rect(surf, (40, 130, 180), inner, border_radius=1)

    elif hat == "crown":
        base_y = top + 2
        left = cx - radius
        base_rect = pygame.Rect(left, base_y, radius * 2, max(3, radius // 3))
        pygame.draw.rect(surf, (212, 168, 52), base_rect)
        pygame.draw.rect(surf, (132, 96, 26), base_rect, 1)
        spike_h = radius
        spike_w = max(3, (radius * 2) // 3)
        for i in range(3):
            sx = left + i * spike_w
            mid_x = sx + spike_w // 2
            pygame.draw.polygon(
                surf,
                (240, 196, 72),
                [(sx, base_y), (sx + spike_w, base_y), (mid_x, base_y - spike_h)],
            )
            # Inner light triangle for gold sheen
            pygame.draw.polygon(
                surf,
                (255, 220, 110),
                [(sx + 2, base_y - 2), (sx + spike_w - 2, base_y - 2), (mid_x, base_y - spike_h + 4)],
            )
            pygame.draw.circle(surf, (220, 78, 120), (mid_x, base_y - spike_h + 2), max(1, radius // 5))

    elif hat == "wizard":
        tip = (cx, top - int(radius * 2.2))
        left = (cx - radius, top + 2)
        right = (cx + radius, top + 2)
        pygame.draw.polygon(surf, (72, 48, 122), [tip, left, right])
        pygame.draw.polygon(surf, (40, 22, 80), [tip, left, right], 2)
        # Highlight streak along left edge of cone
        pygame.draw.line(surf, (128, 96, 200), left, tip, 1)
        band = pygame.Rect(left[0], left[1] - 3, radius * 2, max(3, radius // 3))
        pygame.draw.rect(surf, (236, 196, 72), band)
        # Star sparkle
        sx = cx + radius // 3
        sy = top - radius
        pygame.draw.circle(surf, (248, 232, 120), (sx, sy), max(2, radius // 4))
        pygame.draw.circle(surf, (255, 255, 210), (sx, sy), max(1, radius // 7))

    elif hat == "jester":
        for offset, clr in ((-radius, (220, 78, 120)), (0, (72, 170, 210)), (radius, (236, 196, 72))):
            tip_x = cx + offset
            tip_y = top - int(radius * 1.6)
            base_left = (cx + offset - radius // 2, top + 2)
            base_right = (cx + offset + radius // 2, top + 2)
            pygame.draw.polygon(surf, clr, [(tip_x, tip_y), base_left, base_right])
            # Light edge on left side of each prong
            pygame.draw.line(surf, _lighten(clr, 50), base_left, (tip_x, tip_y), 1)
            pygame.draw.circle(surf, (250, 240, 210), (tip_x, tip_y - 1), max(2, radius // 4))


def draw_snake_tail(
    surf: pygame.Surface,
    tx: int,
    ty: int,
    dx: float,
    dy: float,
    tail: str,
) -> None:
    """Draw a tail accessory at the last segment, (dx, dy) is the outward direction vector."""
    if tail == "none":
        return

    norm = max(0.01, math.sqrt(dx * dx + dy * dy))
    ux, uy = dx / norm, dy / norm
    px, py = -uy, ux

    if tail == "rattle":
        sizes = [8, 6, 4]
        for i, size in enumerate(sizes):
            ox = tx + int(ux * (6 + i * 10))
            oy = ty + int(uy * (6 + i * 10))
            pygame.draw.ellipse(surf, (180, 148, 88), pygame.Rect(ox - size, oy - size // 2, size * 2, size))
            pygame.draw.ellipse(surf, (210, 180, 120), pygame.Rect(ox - size + 1, oy - size // 2 + 1, size * 2 - 2, size - 2))
            pygame.draw.ellipse(surf, (120, 90, 40), pygame.Rect(ox - size, oy - size // 2, size * 2, size), 1)

    elif tail == "spike":
        tip = (int(tx + ux * 18), int(ty + uy * 18))
        left = (int(tx + px * 6), int(ty + py * 6))
        right = (int(tx - px * 6), int(ty - py * 6))
        pygame.draw.polygon(surf, (170, 170, 180), [tip, left, right])
        pygame.draw.polygon(surf, (220, 224, 232), [tip, left, right])
        pygame.draw.polygon(surf, (80, 86, 96), [tip, left, right], 1)
        # Highlight on spike
        mid_base = ((left[0] + right[0]) // 2, (left[1] + right[1]) // 2)
        pygame.draw.line(surf, (240, 244, 252), mid_base, tip, 1)

    elif tail == "flame":
        flicker = (pygame.time.get_ticks() // 80) % 3
        base_len = 16 + flicker * 3
        tip_outer = (int(tx + ux * base_len), int(ty + uy * base_len))
        tip_inner = (int(tx + ux * (base_len - 6)), int(ty + uy * (base_len - 6)))
        left = (int(tx + px * 7), int(ty + py * 7))
        right = (int(tx - px * 7), int(ty - py * 7))
        pygame.draw.polygon(surf, (200, 80, 20), [tip_outer, left, right])
        pygame.draw.polygon(surf, (240, 118, 42), [tip_outer, left, right])
        pygame.draw.polygon(
            surf,
            (250, 216, 96),
            [tip_inner, (int(tx + px * 3), int(ty + py * 3)), (int(tx - px * 3), int(ty - py * 3))],
        )


def draw_snake_eyes(
    surf: pygame.Surface,
    cx: int,
    cy: int,
    r: int,
    ddx: float,
    ddy: float,
    style: str,
    body_clr: tuple[int, int, int],
) -> None:
    """Draw eyes on a head segment. ddx/ddy is the normalised forward direction."""
    px, py = -ddy, ddx

    if style == "visor":
        slit_w = int(r * 1.8)
        slit_h = max(2, r // 3)
        slit = pygame.Rect(cx - slit_w // 2, cy - slit_h // 2, slit_w, slit_h)
        pygame.draw.rect(surf, (18, 22, 30), slit, border_radius=2)
        inner = slit.inflate(-4, -max(1, slit_h // 3))
        pygame.draw.rect(surf, (60, 190, 240), inner, border_radius=1)
        return

    for sign in (-1, 1):
        ex = int(cx + px * sign * (r * 0.5) + ddx * r * 0.38)
        ey = int(cy + py * sign * (r * 0.5) + ddy * r * 0.38)
        outer_r = max(2, r // 4)
        inner_r = max(1, r // 7)

        if style == "fierce":
            pygame.draw.circle(surf, (200, 50, 50), (ex, ey), outer_r + 1)
            pygame.draw.circle(surf, (230, 60, 60), (ex, ey), outer_r)
            # Slit pupil
            pygame.draw.line(surf, (16, 8, 8), (ex, ey - outer_r + 1), (ex, ey + outer_r - 1), max(1, outer_r // 2))

        elif style == "glow":
            r_g = min(255, body_clr[0] + 90)
            g_g = min(255, body_clr[1] + 90)
            b_g = min(255, body_clr[2] + 90)
            gs = pygame.Surface(((outer_r + 3) * 2, (outer_r + 3) * 2), pygame.SRCALPHA)
            pygame.draw.circle(gs, (r_g, g_g, b_g, 130), (outer_r + 3, outer_r + 3), outer_r + 2)
            surf.blit(gs, (ex - outer_r - 3, ey - outer_r - 3))
            pygame.draw.circle(surf, (r_g, g_g, b_g), (ex, ey), outer_r)
            pygame.draw.circle(surf, (250, 255, 250), (ex, ey), inner_r)

        else:  # normal
            pygame.draw.circle(surf, (240, 248, 255), (ex, ey), outer_r)
            pygame.draw.circle(surf, (12, 12, 16), (ex, ey), inner_r)
            # Tiny specular dot
            pygame.draw.circle(surf, (255, 255, 255), (ex - max(1, inner_r // 2), ey - max(1, inner_r // 2)), 1)


def derive_skin_colors(
    body: tuple[int, int, int],
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int, int]]:
    """Derive head/glow colors from a body color for consistent snake rendering."""
    head = _lighten(body, 35)
    glow = (body[0], body[1], body[2], 100)
    return body, head, glow


def _dense_centers(
    centers: list[tuple[float, float]],
    r: int,
) -> list[tuple[float, float]]:
    """Interpolate extra positions between adjacent centres so segments overlap.

    Target spacing ≈ r * 1.5 (segments overlap by ~25%).  This removes the
    need for connector lines and produces the same bumpy-scale look as the
    decorative pool snakes.
    """
    max_gap = r * 1.55
    out: list[tuple[float, float]] = []
    for i, pt in enumerate(centers):
        out.append(pt)
        if i < len(centers) - 1:
            nx, ny = centers[i + 1]
            dx, dy = nx - pt[0], ny - pt[1]
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > max_gap:
                n_extra = max(1, int(dist / max_gap))
                for k in range(1, n_extra + 1):
                    t = k / (n_extra + 1)
                    out.append((pt[0] + dx * t, pt[1] + dy * t))
    return out


def draw_segmented_snake(
    surf: pygame.Surface,
    centers: list[tuple[float, float]],
    skin: SnakeSkin,
    r: int,
) -> None:
    """Unified pool-snake-style renderer shared by preview and gameplay.

    Matches the decorative pool-snake visual: 3 concentric rings per segment
    (dark outer border → mid ring → bright body fill) with a gloss highlight
    dot per segment for a 3-D rounded look.  Segments are densified so they
    overlap naturally — no flat connector lines.
    """
    if not centers:
        return

    body_clr = SKIN_COLORS.get(skin.color, SKIN_COLORS["venom"])
    body_clr, head_clr, glow_clr = derive_skin_colors(body_clr)
    dark1 = _darken(body_clr, 0.32)   # outermost border ring
    dark2 = _darken(body_clr, 0.55)   # second ring
    light = _lighten(body_clr, 65)    # gloss highlight dot

    pts = _dense_centers(centers, r)
    n   = len(pts)

    # ── Body segments tail → head (so head is always on top) ─────────────────
    for i in range(n - 1, -1, -1):
        cx, cy = int(pts[i][0]), int(pts[i][1])
        # taper: 0.50 at tail end → 1.0 at head
        taper = 0.50 + 0.50 * (1.0 - i / max(1, n - 1))
        cr = max(3, int(r * taper))

        if i == 0:
            # ── Head ──────────────────────────────────────────────────────────
            # Glow halo
            glow_sz = r * 4 + 12
            gs = pygame.Surface((glow_sz, glow_sz), pygame.SRCALPHA)
            gc = glow_sz // 2
            pygame.draw.circle(gs, glow_clr, (gc, gc), r + 9)
            surf.blit(gs, (cx - gc, cy - gc))
            # 3-layer head circles
            pygame.draw.circle(surf, dark1, (cx, cy), r + 3)
            pygame.draw.circle(surf, dark2, (cx, cy), r + 1)
            pygame.draw.circle(surf, head_clr, (cx, cy), r)
            draw_snake_pattern(surf, cx, cy, r, 0, skin.pattern, head_clr)
            # Gloss highlight
            hl = max(2, r // 4)
            pygame.draw.circle(surf, light, (cx - hl, cy - hl), hl)
            # Direction vector for snout/eyes
            ddx = ddy = 0.0
            if n > 1:
                ddx = pts[0][0] - pts[1][0]
                ddy = pts[0][1] - pts[1][1]
                dn  = max(0.01, (ddx * ddx + ddy * ddy) ** 0.5)
                ddx /= dn
                ddy /= dn
                # Snout bump (like the pool snakes)
                snout_r  = max(3, int(r * 0.50))
                snout_cx = int(cx + ddx * r * 0.60)
                snout_cy = int(cy + ddy * r * 0.60)
                pygame.draw.circle(surf, dark2,    (snout_cx, snout_cy), snout_r + 1)
                pygame.draw.circle(surf, head_clr, (snout_cx, snout_cy), snout_r)
                pygame.draw.circle(surf, light,
                    (snout_cx - max(1, snout_r // 3), snout_cy - max(1, snout_r // 3)),
                    max(1, snout_r // 3))
                draw_snake_eyes(surf, cx, cy, r, ddx, ddy, skin.eyes, body_clr)
            # Hat sits above the head
            draw_snake_hat(surf, cx, cy, r, skin.hat)

        else:
            # ── Body / tail segment ───────────────────────────────────────────
            pygame.draw.circle(surf, dark1, (cx, cy), cr + 2)
            pygame.draw.circle(surf, dark2, (cx, cy), cr + 1)
            pygame.draw.circle(surf, body_clr, (cx, cy), cr)
            draw_snake_pattern(surf, cx, cy, cr, i, skin.pattern, body_clr)
            # Gloss highlight dot (upper-left offset for 3-D roundness)
            hl = max(1, cr // 3)
            pygame.draw.circle(surf, light, (cx - hl, cy - hl), hl)

    # ── Tail accessory (uses original non-densified direction) ────────────────
    if skin.tail != "none" and len(centers) >= 2:
        last_cx, last_cy = int(centers[-1][0]), int(centers[-1][1])
        prev_cx, prev_cy = int(centers[-2][0]), int(centers[-2][1])
        tdx = float(last_cx - prev_cx)
        tdy = float(last_cy - prev_cy)
        if tdx == 0.0 and tdy == 0.0:
            tdx = 1.0
        draw_snake_tail(surf, last_cx, last_cy, tdx, tdy, skin.tail)
