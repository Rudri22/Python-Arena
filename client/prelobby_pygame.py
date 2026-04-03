from __future__ import annotations

import math
import queue
import random
import threading
import tempfile
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pygame

from client.network import ClientConnection
from shared.protocol import MessageType, make_chat_message, make_invitation_message, make_username_message

WIDTH = 1280
HEIGHT = 720
FPS = 60

SKINS = [
    ("Shadow Ops", (92, 118, 158), (56, 74, 102)),
    ("Venom Core", (72, 198, 138), (38, 126, 88)),
    ("Deep Sea", (72, 158, 210), (42, 90, 144)),
    ("Jungle Viper", (94, 182, 112), (54, 118, 70)),
    ("Arctic Strike", (164, 214, 222), (94, 154, 166)),
    ("Solar Fang", (214, 176, 92), (142, 108, 56)),
]

BG_TOP = (11, 28, 44)
BG_MID = (10, 24, 40)
BG_BOTTOM = (8, 20, 34)
GRID_COLOR = (82, 196, 156, 14)
UI_CARD = (32, 36, 62, 228)
EXP_ORANGE = (236, 170, 70)
EXP_RED = (220, 96, 102)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (int(lerp(a[0], b[0], t)), int(lerp(a[1], b[1], t)), int(lerp(a[2], b[2], t)))


def draw_round_rect(surface: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int, int] | tuple[int, int, int], radius: int, width: int = 0) -> None:
    pygame.draw.rect(surface, color, rect, border_radius=radius, width=width)


class MusicController:
    def __init__(self) -> None:
        self.ready = False
        self.playing = False
        self.wave_path: Path | None = None
        self.chomp_path: Path | None = None
        self.error_path: Path | None = None
        self.chomp_sound: pygame.mixer.Sound | None = None
        self.error_sound: pygame.mixer.Sound | None = None

    def init(self) -> None:
        if self.ready:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            self.wave_path = self._make_loop()
            pygame.mixer.music.load(str(self.wave_path))
            self.chomp_path = self._make_numm()
            self.chomp_sound = pygame.mixer.Sound(str(self.chomp_path))
            self.chomp_sound.set_volume(0.45)
            self.error_path = self._make_error_buzz()
            self.error_sound = pygame.mixer.Sound(str(self.error_path))
            self.error_sound.set_volume(0.5)
            self.ready = True
        except Exception:
            self.ready = False

    def _unique_wav_path(self, prefix: str) -> Path:
        return Path(tempfile.gettempdir()) / f"{prefix}_{uuid4().hex}.wav"

    def _make_loop(self) -> Path:
        sample_rate = 22050
        length = int(sample_rate * 2.4)
        notes = [174.61, 196.00, 220.00, 196.00, 164.81, 196.00, 220.00, 246.94]
        part = max(1, length // len(notes))
        out = array("h")
        for i, hz in enumerate(notes):
            for s in range(part):
                idx = i * part + s
                if idx >= length:
                    break
                t = idx / sample_rate
                env = 0.6 if s < part * 0.7 else 0.28
                val = int(8600 * env * math.sin(2 * math.pi * hz * t))
                out.append(val)
        temp = self._unique_wav_path("snake_arena_radio_loop")
        with wave.open(str(temp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(out.tobytes())
        return temp

    def _make_numm(self) -> Path:
        sample_rate = 22050
        duration = 0.18
        total = int(sample_rate * duration)
        data = array("h")
        for i in range(total):
            t = i / sample_rate
            # playful "numm": quick down-up pitch wobble
            base = 420 - 180 * min(1.0, t / 0.08)
            if t > 0.08:
                base = 240 + 130 * min(1.0, (t - 0.08) / 0.1)
            env = max(0.0, 1.0 - t / duration)
            val = int(9500 * env * math.sin(2 * math.pi * base * t))
            data.append(val)
        temp = self._unique_wav_path("snake_arena_numm")
        with wave.open(str(temp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(data.tobytes())
        return temp

    def _make_error_buzz(self) -> Path:
        sample_rate = 22050
        duration = 0.20
        total = int(sample_rate * duration)
        data = array("h")
        for i in range(total):
            t = i / sample_rate
            hz = 240 if (i // 120) % 2 == 0 else 180
            env = max(0.0, 1.0 - t / duration)
            val = int(10000 * env * math.sin(2 * math.pi * hz * t))
            data.append(val)
        temp = self._unique_wav_path("snake_arena_error")
        with wave.open(str(temp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(data.tobytes())
        return temp

    def toggle(self) -> bool:
        self.init()
        if not self.ready:
            return False
        if self.playing:
            pygame.mixer.music.pause()
            self.playing = False
        else:
            if pygame.mixer.music.get_pos() < 0:
                pygame.mixer.music.play(-1)
            else:
                pygame.mixer.music.unpause()
            self.playing = True
        return self.playing

    def play_numm(self) -> None:
        if self.ready and self.chomp_sound is not None:
            self.chomp_sound.play()

    def play_error(self) -> None:
        if self.ready and self.error_sound is not None:
            self.error_sound.play()


@dataclass
class Star:
    x: float
    y: float
    phase: float
    size: int


@dataclass
class Particle:
    pos: pygame.Vector2
    vel: pygame.Vector2
    life: int
    max_life: int
    color: tuple[int, int, int]
    radius: float

    def update(self) -> None:
        self.pos += self.vel
        self.vel *= 0.96
        self.life -= 1
        self.radius *= 0.97


@dataclass
class Smoke:
    pos: pygame.Vector2
    vel: pygame.Vector2
    life: int
    max_life: int
    radius: float

    def update(self) -> None:
        self.pos += self.vel
        self.vel.x *= 0.98
        self.radius *= 1.01
        self.life -= 1


@dataclass
class FoodPellet:
    pos: pygame.Vector2
    life: int = -1


@dataclass
class SnakeUnit:
    body: list[pygame.Vector2]
    velocity: pygame.Vector2
    target: pygame.Vector2
    speed: float
    phase: float
    primary: tuple[int, int, int]
    secondary: tuple[int, int, int]
    name: str
    alpha: float = 255.0
    respawn_timer: int = 0
    panic_timer: int = 0
    panic_target: pygame.Vector2 | None = None

    def head(self) -> pygame.Vector2:
        return self.body[0]


class PreLobby:
    def __init__(
        self,
        *,
        initial_username: str = "",
        error_message: str = "",
        username_validator: Callable[[str], tuple[bool, str]] | None = None,
    ) -> None:
        pygame.init()
        pygame.display.set_caption("Snake Arena - Deployment Lobby")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.w = WIDTH
        self.h = HEIGHT
        self.frame = 0
        self.running = True
        self.result_name: str | None = None

        self.music = MusicController()
        self.music_on = self.music.toggle()

        self.skin_idx = 0
        self.name_text = initial_username[:16]
        self.error_message = error_message
        self.username_validator = username_validator
        self.input_focus = False
        self.ui_intro_ms = pygame.time.get_ticks()

        self.stars = [Star(random.uniform(0, self.w), random.uniform(0, self.h * 0.65), random.uniform(0, math.tau), random.randint(1, 2)) for _ in range(85)]
        self.particles: list[Particle] = []
        self.smoke: list[Smoke] = []
        self.food_pellets: list[FoodPellet] = []
        self.snakes: list[SnakeUnit] = []
        self._spawn_snakes()

        self.last_explosion = 0
        self.suggestions = ["VenomStrike", "CobraCMD", "ViperOps", "MambaX", "ScaleForce", "Slyther"]
        self.hovered_suggestion = -1
        self.invalid_feedback_frames = 0

        self.title_font = pygame.font.SysFont("bahnschrift", 58, bold=True)
        self.h1 = pygame.font.SysFont("arial black", 30, bold=True)
        self.h2 = pygame.font.SysFont("arial black", 20, bold=True)
        self.body_font = pygame.font.SysFont("bahnschrift", 18, bold=True)
        self.small = pygame.font.SysFont("arial", 16, bold=True)
        self.skin_icon = self._load_skin_icon()
        self.music_icon_on = self._load_asset_icon("music.png")
        self.music_icon_off = self._load_asset_icon("no-music.png")

    def _load_skin_icon(self) -> pygame.Surface | None:
        return self._load_asset_icon("snake.png")

    def _load_asset_icon(self, filename: str) -> pygame.Surface | None:
        icon_path = Path(__file__).resolve().parent / "assets" / filename
        if not icon_path.exists():
            return None
        try:
            return pygame.image.load(str(icon_path)).convert_alpha()
        except pygame.error:
            return None

    def _spawn_snakes(self) -> None:
        pool = SKINS.copy()
        random.shuffle(pool)
        count = random.randint(4, 5)
        for i in range(count):
            _, c1, c2 = pool[i]
            from_left = random.random() < 0.5
            x = random.uniform(-100, -35) if from_left else random.uniform(self.w + 35, self.w + 100)
            y = random.uniform(90, self.h - 90)
            speed = random.uniform(1.0, 1.5)
            vel = pygame.Vector2(1 if from_left else -1, random.uniform(-0.3, 0.3)).normalize() * speed
            body = [pygame.Vector2(x - n * vel.x * 3, y - n * vel.y * 3) for n in range(20)]
            target = pygame.Vector2(
                random.uniform(-140, self.w + 140),
                random.uniform(-120, self.h + 120),
            )
            self.snakes.append(SnakeUnit(body=body, velocity=vel, target=target, speed=speed, phase=random.uniform(0, math.tau), primary=c1, secondary=c2, name=f"unit_{i+1}"))

    def _respawn_snake(self, snake: SnakeUnit) -> None:
        side = random.choice(("left", "right"))
        if side == "left":
            head = pygame.Vector2(-80, random.uniform(60, self.h - 60))
            dirv = pygame.Vector2(1, random.uniform(-0.35, 0.35))
        else:
            head = pygame.Vector2(self.w + 80, random.uniform(60, self.h - 60))
            dirv = pygame.Vector2(-1, random.uniform(-0.35, 0.35))

        if dirv.length_squared() < 0.01:
            dirv = pygame.Vector2(1, 0)
        dirv = dirv.normalize()
        snake.velocity = dirv * snake.speed
        snake.body = [head - dirv * (n * 10) for n in range(len(snake.body))]
        snake.target = pygame.Vector2(
            random.uniform(-140, self.w + 140),
            random.uniform(-120, self.h + 120),
        )
        snake.respawn_timer = random.randint(20, 70)
        snake.alpha = 0.0

    def _segment_alpha(self, pt: pygame.Vector2) -> float:
        margin = 95.0
        dx = 0.0
        dy = 0.0
        if pt.x < 0:
            dx = -pt.x
        elif pt.x > self.w:
            dx = pt.x - self.w
        if pt.y < 0:
            dy = -pt.y
        elif pt.y > self.h:
            dy = pt.y - self.h
        dist = max(dx, dy)
        if dist <= 0:
            return 1.0
        return max(0.0, 1.0 - min(1.0, dist / margin))

    def _spawn_explosion(self, pos: pygame.Vector2) -> None:
        for _ in range(15):
            ang = random.uniform(0, math.tau)
            speed = random.uniform(1.8, 5.0)
            color = EXP_ORANGE if random.random() < 0.65 else EXP_RED
            self.particles.append(
                Particle(
                    pos=pos.copy(),
                    vel=pygame.Vector2(math.cos(ang) * speed, math.sin(ang) * speed - 0.8),
                    life=52,
                    max_life=52,
                    color=color,
                    radius=random.uniform(2.0, 4.0),
                )
            )
        for _ in range(5):
            self.smoke.append(
                Smoke(
                    pos=pos.copy() + pygame.Vector2(random.uniform(-10, 10), random.uniform(-3, 3)),
                    vel=pygame.Vector2(random.uniform(-0.4, 0.4), random.uniform(-1.2, -0.5)),
                    life=70,
                    max_life=70,
                    radius=random.uniform(8, 14),
                )
            )

    def _spawn_crumb_burst(self, pos: pygame.Vector2) -> None:
        crumb_palette = [(244, 196, 112), (216, 142, 74), (168, 96, 58), (255, 224, 140)]
        for _ in range(22):
            ang = random.uniform(0, math.tau)
            speed = random.uniform(0.8, 3.2)
            color = random.choice(crumb_palette)
            self.particles.append(
                Particle(
                    pos=pos.copy(),
                    vel=pygame.Vector2(math.cos(ang) * speed, math.sin(ang) * speed),
                    life=42,
                    max_life=42,
                    color=color,
                    radius=random.uniform(1.6, 3.6),
                )
            )

    def _throw_food(self, x: float, y: float) -> None:
        # Never place food inside the central panel area.
        intro_t = min(1.0, (pygame.time.get_ticks() - self.ui_intro_ms) / 600.0)
        ease = 1 - (1 - intro_t) ** 3
        dy = int((1 - ease) * 40)
        card = self._card_rect().move(0, dy)
        if card.collidepoint((x, y)):
            return
        if len(self.food_pellets) >= 3:
            self.food_pellets.pop(0)
        self.food_pellets.append(FoodPellet(pos=pygame.Vector2(x, y), life=360))

    def _trigger_scatter(self, eater_index: int, eaten_food_index: int | None = None, contenders: list[int] | None = None) -> None:
        # Remove only the eaten pellet.
        if eaten_food_index is not None and 0 <= eaten_food_index < len(self.food_pellets):
            eaten_pos = self.food_pellets[eaten_food_index].pos
            self.food_pellets.pop(eaten_food_index)
            self._spawn_crumb_burst(eaten_pos)
            self.music.play_numm()
        if contenders is None:
            contenders = [eater_index]
        eater_head = self.snakes[eater_index].head()
        for i, snake in enumerate(self.snakes):
            if i not in contenders:
                continue
            snake.panic_timer = random.randint(80, 140)
            away = snake.head() - eater_head
            if away.length_squared() < 0.01:
                away = pygame.Vector2(random.uniform(-1, 1), random.uniform(-1, 1))
            away = away.normalize() * random.uniform(180, 320)
            scatter = snake.head() + away + pygame.Vector2(random.uniform(-80, 80), random.uniform(-60, 60))
            snake.panic_target = scatter
            if i == eater_index:
                snake.panic_timer = random.randint(45, 85)

    def _update_snakes(self) -> None:
        eater_index: int | None = None
        eaten_food_index: int | None = None
        contenders: list[int] = []
        for i, snake in enumerate(self.snakes):
            if snake.respawn_timer > 0:
                snake.respawn_timer -= 1
                continue

            head = snake.head()
            speed_mult = 1.0
            steer_lerp = 0.035

            if snake.panic_timer > 0:
                snake.panic_timer -= 1
                speed_mult = 1.35
                steer_lerp = 0.08
                if snake.panic_target is None or head.distance_to(snake.panic_target) < 40:
                    snake.panic_target = head + pygame.Vector2(random.uniform(-240, 240), random.uniform(-180, 180))
                snake.target = snake.panic_target
            elif self.food_pellets:
                # Rush nearest food pellet.
                nearest_idx = min(range(len(self.food_pellets)), key=lambda k: head.distance_to(self.food_pellets[k].pos))
                snake.target = self.food_pellets[nearest_idx].pos
                speed_mult = 1.55
                steer_lerp = 0.07
                if head.distance_to(self.food_pellets[nearest_idx].pos) < 15:
                    eater_index = i
                    eaten_food_index = nearest_idx
            elif head.distance_to(snake.target) < 110:
                snake.target = pygame.Vector2(
                    random.uniform(-140, self.w + 140),
                    random.uniform(-120, self.h + 120),
                )

            desired = snake.target - head
            if desired.length_squared() > 0.001:
                desired = desired.normalize() * (snake.speed * speed_mult)
            snake.velocity = snake.velocity.lerp(desired, steer_lerp)
            snake.phase += 0.06
            wiggle = pygame.Vector2(-snake.velocity.y, snake.velocity.x)
            if wiggle.length_squared() > 0.001:
                wiggle = wiggle.normalize() * math.sin(snake.phase) * 0.35
            snake.velocity += wiggle * (0.03 if speed_mult > 1.3 else 0.045)
            if snake.velocity.length_squared() > 0.001:
                snake.velocity.scale_to_length(snake.speed * speed_mult)

            new_head = head + snake.velocity
            snake.body[0] = new_head

            spacing = 10
            for s in range(1, len(snake.body)):
                prev = snake.body[s - 1]
                cur = snake.body[s]
                diff = prev - cur
                dist = diff.length()
                if dist > spacing:
                    target = prev - diff.normalize() * spacing
                    snake.body[s] = cur.lerp(target, 0.45)  # tighter spring follow for connected body

            # Fade behavior for realistic leave/return instead of instant teleport.
            margin = 120.0
            fully_far = all(
                (pt.x < -margin or pt.x > self.w + margin or pt.y < -margin or pt.y > self.h + margin)
                for pt in snake.body
            )
            if fully_far:
                snake.alpha = max(0.0, snake.alpha - 15.0)
                if snake.alpha <= 0.0:
                    self._respawn_snake(snake)
            else:
                snake.alpha = min(255.0, snake.alpha + 5.0)

        if eaten_food_index is not None and eater_index is not None:
            # Only snakes that were contesting this exact pellet should panic/flee.
            contenders = []
            for j, other in enumerate(self.snakes):
                if other.respawn_timer > 0:
                    continue
                # Determine what this snake was aiming at right now.
                if self.food_pellets:
                    nearest_idx_j = min(range(len(self.food_pellets)), key=lambda k: other.head().distance_to(self.food_pellets[k].pos))
                    if nearest_idx_j == eaten_food_index:
                        contenders.append(j)
            if eater_index not in contenders:
                contenders.append(eater_index)
            self._trigger_scatter(eater_index, eaten_food_index, contenders)

    def _update_effects(self) -> None:
        alive_p: list[Particle] = []
        for p in self.particles:
            p.update()
            if p.life > 0 and p.radius > 0.2:
                alive_p.append(p)
        self.particles = alive_p

        alive_s: list[Smoke] = []
        for s in self.smoke:
            s.update()
            if s.life > 0:
                alive_s.append(s)
        self.smoke = alive_s

        # Food pellets persist until eaten.

    def _bg_gradient(self, surf: pygame.Surface) -> None:
        half = self.h // 2
        for y in range(self.h):
            if y < half:
                t = y / max(1, half)
                c = lerp_color(BG_TOP, BG_MID, t)
            else:
                t = (y - half) / max(1, self.h - half)
                c = lerp_color(BG_MID, BG_BOTTOM, t)
            pygame.draw.line(surf, c, (0, y), (self.w, y))

    def _draw_background(self, surf: pygame.Surface) -> None:
        self._bg_gradient(surf)

        # subtle ambient haze to blend background with center panel tone
        haze = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        pygame.draw.ellipse(haze, (24, 78, 88, 54), pygame.Rect(-140, self.h * 0.15, self.w + 280, self.h * 0.8))
        surf.blit(haze, (0, 0))

        # stars twinkle
        for st in self.stars:
            bright = 95 + int(90 * (0.5 + 0.5 * math.sin(self.frame * 0.03 + st.phase)))
            pygame.draw.circle(surf, (bright, bright + 16, bright + 18), (int(st.x), int(st.y)), st.size)

        # tactical hex field (dominant background motif)
        hex_layer = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        size = 30
        hstep = int(size * 1.5)
        vstep = int(size * 1.73)
        for x in range(-size - hstep, self.w + size + hstep, hstep):
            for y in range(-size - vstep, self.h + size + vstep, vstep):
                oy = y + (vstep // 2 if (x // hstep) % 2 else 0)
                pts = []
                for k in range(6):
                    ang = math.radians(60 * k + 30)
                    pts.append((x + size * math.cos(ang), oy + size * math.sin(ang)))
                edge_col = (68, 132, 196, 58) if ((x // hstep + y // vstep) % 2 == 0) else (54, 168, 134, 58)
                fill_col = (28, 62, 102, 28) if ((x // hstep + y // vstep) % 2 == 0) else (24, 84, 72, 28)
                pygame.draw.polygon(hex_layer, edge_col, pts, 2)
                if (x + oy // 2) % 5 == 0:
                    pygame.draw.polygon(hex_layer, fill_col, pts)
        # secondary micro-hex layer for texture depth
        micro = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        msize = 12
        mh = int(msize * 1.5)
        mv = int(msize * 1.73)
        for x in range(-msize - mh, self.w + msize + mh, mh):
            for y in range(-msize - mv, self.h + msize + mv, mv):
                oy = y + (mv // 2 if (x // mh) % 2 else 0)
                pts = []
                for k in range(6):
                    ang = math.radians(60 * k + 30)
                    pts.append((x + msize * math.cos(ang), oy + msize * math.sin(ang)))
                pygame.draw.polygon(micro, (86, 188, 154, 18), pts, 1)
        surf.blit(hex_layer, (0, 0))
        surf.blit(micro, (0, 0))

        # light vignette for focus on center panel
        vignette = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        pygame.draw.rect(vignette, (0, 0, 0, 40), vignette.get_rect(), width=80, border_radius=18)
        surf.blit(vignette, (0, 0))

    def _draw_snakes(self, surf: pygame.Surface) -> None:
        player_skin = SKINS[self.skin_idx]
        player_color = player_skin[1]
        for idx, snake in enumerate(self.snakes):
            if snake.respawn_timer > 0:
                continue
            valid_points: list[tuple[float, float, int, tuple[int, int, int]]] = []
            for i, seg in enumerate(snake.body):
                t = i / max(1, len(snake.body) - 1)
                color = lerp_color(snake.primary, snake.secondary, t)
                radius = int(11 - t * 5)
                seg_alpha = int(snake.alpha * self._segment_alpha(seg))
                if seg_alpha <= 3:
                    continue
                valid_points.append((seg.x, seg.y, max(2, radius), color))

            # draw connected circular body: thick links + circles
            for i in range(1, len(valid_points)):
                x1, y1, r1, c1 = valid_points[i - 1]
                x2, y2, r2, c2 = valid_points[i]
                line_w = max(2, int((r1 + r2) * 0.95))
                lc = lerp_color(c1, c2, 0.5)
                pygame.draw.line(surf, lc, (int(x1), int(y1)), (int(x2), int(y2)), line_w)
            for x, y, r, c in valid_points:
                pygame.draw.circle(surf, c, (int(x), int(y)), r)

            # face details on head
            head = snake.head()
            direction = snake.velocity.normalize() if snake.velocity.length_squared() > 0.001 else pygame.Vector2(1, 0)
            side = pygame.Vector2(-direction.y, direction.x)
            head_radius = 11
            eye_base = head + direction * 3.2
            l_eye = eye_base + side * 3.1
            r_eye = eye_base - side * 3.1
            head_alpha = int(snake.alpha * self._segment_alpha(head))
            if head_alpha > 12:
                # subtle snout highlight so head reads as a face, not a blob
                snout_glow = pygame.Surface((head_radius * 2 + 6, head_radius * 2 + 6), pygame.SRCALPHA)
                pygame.draw.circle(
                    snout_glow,
                    (min(255, snake.primary[0] + 28), min(255, snake.primary[1] + 28), min(255, snake.primary[2] + 28), min(120, head_alpha)),
                    (head_radius + 3, head_radius + 3),
                    head_radius - 1,
                )
                surf.blit(snout_glow, (int(head.x - head_radius - 3), int(head.y - head_radius - 3)))
                pygame.draw.circle(surf, (248, 248, 248), (int(l_eye.x), int(l_eye.y)), 3)
                pygame.draw.circle(surf, (248, 248, 248), (int(r_eye.x), int(r_eye.y)), 3)
            pupil_shift = direction * 0.9
            if head_alpha > 12:
                pygame.draw.circle(surf, (18, 18, 18), (int(l_eye.x + pupil_shift.x), int(l_eye.y + pupil_shift.y)), 1)
                pygame.draw.circle(surf, (18, 18, 18), (int(r_eye.x + pupil_shift.x), int(r_eye.y + pupil_shift.y)), 1)
                # tiny nostrils near snout tip
                snout = head + direction * (head_radius - 1)
                pygame.draw.circle(surf, (26, 26, 26), (int(snout.x + side.x * 1.2), int(snout.y + side.y * 1.2)), 1)
                pygame.draw.circle(surf, (26, 26, 26), (int(snout.x - side.x * 1.2), int(snout.y - side.y * 1.2)), 1)

            # occasional tongue flick
            flick = ((self.frame + idx * 17) % 85) < 6
            if flick and head_alpha > 12:
                snout = head + direction * head_radius
                tongue_tip = snout + direction * 9
                fork = side * 2
                pygame.draw.line(surf, (230, 70, 70), snout, tongue_tip, 2)
                pygame.draw.line(surf, (230, 70, 70), tongue_tip, tongue_tip + fork, 1)
                pygame.draw.line(surf, (230, 70, 70), tongue_tip, tongue_tip - fork, 1)

            # selected player snake glow
            if idx == 0:
                pulse = 0.5 + 0.5 * math.sin(self.frame * 0.08)
                glow_radius = int(28 + pulse * 7)
                glow = pygame.Surface((glow_radius * 2, glow_radius * 2), pygame.SRCALPHA)
                pygame.draw.circle(glow, (player_color[0], player_color[1], player_color[2], 55), (glow_radius, glow_radius), glow_radius)
                surf.blit(glow, (snake.head().x - glow_radius, snake.head().y - glow_radius))

    def _draw_effects(self, surf: pygame.Surface) -> None:
        # dropped food bait
        for f in self.food_pellets:
            pulse = 0.5 + 0.5 * math.sin((self.frame + f.pos.x * 0.01) * 0.2)
            r = 5 + int(pulse * 2)
            glow = pygame.Surface((30, 30), pygame.SRCALPHA)
            pygame.draw.circle(glow, (255, 186, 84, 92), (15, 15), 10)
            surf.blit(glow, (f.pos.x - 15, f.pos.y - 15))
            pygame.draw.circle(surf, (255, 194, 96), (int(f.pos.x), int(f.pos.y)), r)
            pygame.draw.circle(surf, (164, 88, 32), (int(f.pos.x), int(f.pos.y)), max(2, r - 3))

        for s in self.smoke:
            alpha = int(120 * (s.life / s.max_life))
            r = int(s.radius)
            puff = pygame.Surface((r * 2 + 2, r * 2 + 2), pygame.SRCALPHA)
            pygame.draw.circle(puff, (56, 82, 104, alpha), (r + 1, r + 1), r)
            surf.blit(puff, (s.pos.x - r, s.pos.y - r))

        for p in self.particles:
            alpha = int(255 * (p.life / p.max_life))
            r = max(1, int(p.radius))
            spark = pygame.Surface((r * 2 + 2, r * 2 + 2), pygame.SRCALPHA)
            pygame.draw.circle(spark, (p.color[0], p.color[1], p.color[2], alpha), (r + 1, r + 1), r)
            surf.blit(spark, (p.pos.x - r, p.pos.y - r))

    def _card_rect(self) -> pygame.Rect:
        return pygame.Rect(self.w // 2 - 285, self.h // 2 - 215, 570, 430)

    def _music_center(self) -> pygame.Vector2:
        return pygame.Vector2(92, 84)

    def _skin_center(self) -> pygame.Vector2:
        return pygame.Vector2(self.w - 92, 84)

    def _input_rect(self) -> pygame.Rect:
        card = self._card_rect()
        width = card.width - 120
        return pygame.Rect(card.centerx - width // 2, card.y + 198, width, 54)

    def _deploy_rect(self) -> pygame.Rect:
        card = self._card_rect()
        return pygame.Rect(card.centerx - 130, card.y + 360, 260, 50)

    def _name_is_valid(self) -> bool:
        text = self.name_text.strip()
        if len(text) < 3:
            return False
        return all(ch.isalnum() or ch in ("_", "-", " ") for ch in text)

    def _suggestion_rects(self, input_rect: pygame.Rect, card: pygame.Rect) -> list[pygame.Rect]:
        """Centered bounded layout, supports 4+2 style row wrapping."""
        gap = 8
        min_w = 120
        max_w = 142
        widths = []
        for text in self.suggestions:
            tw = self.small.size(text)[0]
            widths.append(max(min_w, min(max_w, tw + 26)))

        # Match reference: first row 4 pills, second row remaining centered.
        first_count = 4 if len(widths) > 4 else len(widths)
        first_total = sum(widths[:first_count]) + gap * max(0, first_count - 1)
        second_total = sum(widths[first_count:]) + gap * max(0, len(widths[first_count:]) - 1)
        y1 = input_rect.bottom + 18
        y2 = y1 + 48

        rects: list[pygame.Rect] = []
        x = card.centerx - first_total // 2
        for w in widths[:first_count]:
            rects.append(pygame.Rect(x, y1, w, 34))
            x += w + gap
        if len(widths) > first_count:
            x = card.centerx - second_total // 2
            for w in widths[first_count:]:
                rects.append(pygame.Rect(x, y2, w, 34))
                x += w + gap
        return rects

    def _draw_mascot(self, surf: pygame.Surface, anchor: tuple[int, int], look_at: tuple[int, int]) -> None:
        x, y = anchor
        typing = len(self.name_text) > 0
        ready = len(self.name_text) >= 3
        pulse = 0.5 + 0.5 * math.sin(self.frame * 0.1)
        look = pygame.Vector2(look_at[0] - x, look_at[1] - y)
        if look.length_squared() > 0.001:
            look = look.normalize() * 1.4
        else:
            look = pygame.Vector2(0, 0)

        # comic snake mascot body (thicker outlines + expressive pose)
        tone = (104, 132, 210) if not ready else (94, 238, 98)
        outline = (22, 28, 46)
        for i in range(9):
            sx = x - 44 + i * 10
            sy = y + 20 + int(math.sin(self.frame * 0.06 + i * 0.7) * 5)
            r = 10 if i < 2 else 8
            pygame.draw.circle(surf, outline, (sx, sy), r + 2)
            pygame.draw.circle(surf, tone, (sx, sy), r)
        head_x = x + 46
        head_y = y + 4
        pygame.draw.circle(surf, outline, (head_x, head_y), 24)
        pygame.draw.circle(surf, tone, (head_x, head_y), 22)
        # comic highlight
        pygame.draw.circle(surf, (255, 255, 255, 80), (head_x - 7, head_y - 8), 6)

        eye_bright = 240 if typing else 214
        le = (head_x - 7, head_y - 7)
        re = (head_x + 8, head_y - 7)
        pygame.draw.circle(surf, outline, le, 7)
        pygame.draw.circle(surf, outline, re, 7)
        pygame.draw.circle(surf, (eye_bright, eye_bright, eye_bright), le, 6)
        pygame.draw.circle(surf, (eye_bright, eye_bright, eye_bright), re, 6)
        pygame.draw.circle(surf, (20, 22, 28), (int(le[0] + look.x), int(le[1] + look.y)), 3)
        pygame.draw.circle(surf, (20, 22, 28), (int(re[0] + look.x), int(re[1] + look.y)), 3)
        # tiny white spark for extra life
        pygame.draw.circle(surf, (255, 255, 255), (le[0] - 2, le[1] - 2), 1)
        pygame.draw.circle(surf, (255, 255, 255), (re[0] - 2, re[1] - 2), 1)

        if ready:
            pygame.draw.arc(surf, outline, pygame.Rect(head_x - 11, head_y + 4, 22, 13), math.radians(12), math.radians(168), 3)
            pygame.draw.arc(surf, (255, 250, 234), pygame.Rect(head_x - 10, head_y + 5, 20, 11), math.radians(12), math.radians(168), 2)
        else:
            pygame.draw.line(surf, outline, (head_x - 8, head_y + 10), (head_x + 8, head_y + 10), 3)
            pygame.draw.line(surf, (225, 232, 242), (head_x - 7, head_y + 10), (head_x + 7, head_y + 10), 2)
        # blush dots
        pygame.draw.circle(surf, (255, 120, 150, 120), (head_x - 14, head_y + 2), 3)
        pygame.draw.circle(surf, (255, 120, 150, 120), (head_x + 14, head_y + 2), 3)

        # antenna glow
        antenna_alpha = int(140 + (90 * pulse if typing else 20))
        antenna = pygame.Surface((30, 30), pygame.SRCALPHA)
        pygame.draw.circle(antenna, (255, 209, 102, antenna_alpha), (15, 15), 8)
        surf.blit(antenna, (head_x - 15, head_y - 52))
        pygame.draw.line(surf, outline, (head_x, head_y - 24), (head_x, head_y - 40), 4)
        pygame.draw.line(surf, (255, 244, 198), (head_x, head_y - 24), (head_x, head_y - 40), 2)

        # arms
        arm_y = head_y + 14
        if ready:
            pygame.draw.line(surf, outline, (head_x - 16, arm_y), (head_x - 30, arm_y - 14), 6)
            pygame.draw.line(surf, outline, (head_x + 16, arm_y), (head_x + 30, arm_y - 14), 6)
            pygame.draw.line(surf, (130, 156, 198), (head_x - 16, arm_y), (head_x - 30, arm_y - 14), 4)
            pygame.draw.line(surf, (130, 156, 198), (head_x + 16, arm_y), (head_x + 30, arm_y - 14), 4)
        else:
            pygame.draw.line(surf, outline, (head_x - 16, arm_y), (head_x - 28, arm_y + 2), 6)
            pygame.draw.line(surf, outline, (head_x + 16, arm_y), (head_x + 28, arm_y + 2), 6)
            pygame.draw.line(surf, (130, 156, 198), (head_x - 16, arm_y), (head_x - 28, arm_y + 2), 4)
            pygame.draw.line(surf, (130, 156, 198), (head_x + 16, arm_y), (head_x + 28, arm_y + 2), 4)

    def _draw_ui(self, surf: pygame.Surface) -> None:
        intro_t = min(1.0, (pygame.time.get_ticks() - self.ui_intro_ms) / 600.0)
        ease = 1 - (1 - intro_t) ** 3
        dy = int((1 - ease) * 40)

        mouse = pygame.Vector2(pygame.mouse.get_pos())
        card = self._card_rect().move(0, dy)

        # blur simulation
        blur = pygame.Surface((card.width + 46, card.height + 46), pygame.SRCALPHA)
        for i in range(9):
            alpha = 28 - i * 2
            rr = pygame.Rect(22 - i, 22 - i, card.width + i * 2, card.height + i * 2)
            draw_round_rect(blur, rr, (8, 12, 24, alpha), 34 + i)
        surf.blit(blur, (card.x - 23, card.y - 23))

        # New center panel style (matching reference)
        panel = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
        for y in range(card.height):
            t = y / max(1, card.height - 1)
            col = lerp_color((17, 34, 56), (10, 24, 42), t)
            pygame.draw.line(panel, (col[0], col[1], col[2], 242), (0, y), (card.width, y))
        # Draw borders inset to avoid clipped/sharp corner artifacts.
        outer_border = panel.get_rect().inflate(-4, -4)
        inner_border = panel.get_rect().inflate(-12, -12)
        draw_round_rect(panel, outer_border, (52, 232, 145, 255), 34, 2)
        draw_round_rect(panel, inner_border, (38, 190, 120, 56), 30, 1)
        # snake-scale texture
        texture = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
        for y in range(44, card.height - 20, 20):
            offset = 10 if (y // 24) % 2 else 0
            for x in range(18 + offset, card.width - 14, 22):
                pygame.draw.arc(texture, (84, 220, 156, 20), pygame.Rect(x, y, 14, 10), math.pi, math.tau, 1)
        panel.blit(texture, (0, 0))

        # top slime drips
        for x in (72, 182, card.width - 210, card.width - 96):
            pygame.draw.rect(panel, (40, 186, 132, 160), pygame.Rect(x, 2, 8, 22), border_radius=4)
            pygame.draw.circle(panel, (40, 186, 132, 170), (x + 4, 24), 4)

        # Hard clip entire panel to rounded shape to prevent any square-corner bleed.
        mask = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=34)
        panel.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(panel, card.topleft)

        # snake eye badges (blink + occasional tongue flick)
        for idx, ex in enumerate((card.x + 58, card.right - 58)):
            flip = 1 if idx == 0 else -1
            cy = card.y + 66
            glow_green = (42, 176, 108)
            body_green = (28, 148, 84)
            edge_green = (18, 112, 64)
            # glow + egg-like head
            glow = pygame.Surface((52, 48), pygame.SRCALPHA)
            pygame.draw.ellipse(glow, (glow_green[0], glow_green[1], glow_green[2], 86), pygame.Rect(4, 5, 44, 34))
            surf.blit(glow, (ex - 26, cy - 24))
            head_rect = pygame.Rect(ex - 20, cy - 16, 40, 32)
            pygame.draw.ellipse(surf, body_green, head_rect)
            pygame.draw.ellipse(surf, edge_green, head_rect, 2)
            # texture spots + highlight so head is not flat color
            pygame.draw.ellipse(surf, (22, 126, 72), pygame.Rect(ex - 10, cy - 8, 10, 7))
            pygame.draw.ellipse(surf, (22, 126, 72), pygame.Rect(ex + 2, cy + 1, 9, 6))
            pygame.draw.ellipse(surf, (62, 188, 122, 170), pygame.Rect(ex - 12, cy - 11, 16, 9))
            # eye blink pattern
            blink = ((self.frame + idx * 53) % 120) < 8
            if blink:
                pygame.draw.line(surf, (255, 219, 77), (ex - 4, cy - 4), (ex + 4, cy - 4), 2)
            else:
                pygame.draw.ellipse(surf, (255, 219, 77), pygame.Rect(ex - 4, cy - 8, 8, 9))
                pygame.draw.rect(surf, (12, 16, 22), pygame.Rect(ex - 1, cy - 8, 2, 8), border_radius=1)
            # tiny nose (two nostrils)
            nose_dir = -1 if idx == 0 else 1  # left snake -> left side, right snake -> right side
            nose_x = ex + 12 * nose_dir
            nose_y = cy + 2
            pygame.draw.circle(surf, (14, 64, 42), (nose_x - nose_dir, nose_y), 1)
            pygame.draw.circle(surf, (14, 64, 42), (nose_x + nose_dir, nose_y + 1), 1)
            if ((self.frame + idx * 47) % 120) < 10:
                # Start tongue at the head edge, not from inside.
                x1, y1 = ex - 19 * flip, cy + 4
                x2, y2 = ex - 31 * flip, cy + 8
                pygame.draw.line(surf, (245, 106, 156), (x1, y1), (x2, y2), 2)
                pygame.draw.line(surf, (245, 106, 156), (x2, y2), (x2 - 3 * flip, y2 - 2), 1)
                pygame.draw.line(surf, (245, 106, 156), (x2, y2), (x2 - 3 * flip, y2 + 2), 1)

        # title split-color
        t_left = self.title_font.render("PYTHON", True, (240, 246, 255))
        t_right = self.title_font.render("ARENA", True, (76, 236, 151))
        gap = 10
        total_w = t_left.get_width() + gap + t_right.get_width()
        start_x = card.centerx - total_w // 2
        ty = card.y + 42
        surf.blit(t_left, (start_x, ty))
        surf.blit(t_right, (start_x + t_left.get_width() + gap, ty))

        subtitle = self.body_font.render("L O B B Y   D E P L O Y M E N T", True, (0, 221, 160))
        surf.blit(subtitle, subtitle.get_rect(center=(card.centerx, card.y + 110)))
        pygame.draw.line(surf, (40, 92, 112), (card.x + 38, card.y + 138), (card.right - 38, card.y + 138), 1)

        # status text reacts
        status = "IDLE"
        if len(self.name_text) > 0:
            status = "TYPING"
        if len(self.name_text) >= 3:
            status = "READY"
        s_color = (241, 195, 72) if status == "IDLE" else ((122, 184, 255) if status == "TYPING" else (106, 236, 166))
        # Lift status badge a bit to leave a clean line for validation errors.
        status_rect = pygame.Rect(card.centerx - 68, card.y + 138, 136, 34)
        draw_round_rect(surf, status_rect, (16, 30, 52, 240), 16)
        draw_round_rect(surf, status_rect, (52, 120, 146), 16, 1)
        status_surf = self.h2.render(status, True, s_color)
        surf.blit(status_surf, status_surf.get_rect(center=status_rect.center))
        pygame.draw.circle(surf, s_color, (status_rect.left + 18, status_rect.centery), 6)

        input_rect = self._input_rect().move(0, dy)
        # no mascot in this style

        # input
        draw_round_rect(surf, input_rect, (24, 43, 70, 244), 26)
        border = (72, 214, 152) if self.input_focus else (64, 110, 160)
        draw_round_rect(surf, input_rect, border, 26, 2)
        prefix = self.h2.render("\u21AA", True, (72, 214, 152))
        surf.blit(prefix, (input_rect.x + 22, input_rect.y + 14))
        if self.name_text:
            text = self.h2.render(self.name_text, True, (226, 232, 240))
            surf.blit(text, (input_rect.x + 64, input_rect.y + 13))
        else:
            ph = self.body_font.render("Enter deploy name...", True, (100, 116, 139))
            surf.blit(ph, (input_rect.x + 64, input_rect.y + 17))
        count_color = (106, 236, 166) if self._name_is_valid() else (118, 136, 160)
        count = self.small.render(f"{len(self.name_text)}/16", True, count_color)
        surf.blit(count, (input_rect.right - 56, input_rect.y + 16))
        if self.input_focus and (self.frame // 30) % 2 == 0:
            tw = self.h2.size(self.name_text)[0]
            pygame.draw.rect(surf, (230, 230, 230), pygame.Rect(input_rect.x + 66 + tw, input_rect.y + 11, 2, 30))
        if self.error_message:
            # Place error in the gap between status badge and input.
            err = self.small.render(self.error_message, True, (255, 86, 86))
            err_shadow = self.small.render(self.error_message, True, (40, 8, 8))
            err_x = input_rect.x + 8
            err_y = status_rect.bottom + 4
            max_err_y = input_rect.y - err.get_height() - 2
            if err_y > max_err_y:
                err_y = max_err_y
            surf.blit(err_shadow, (err_x + 1, err_y + 1))
            surf.blit(err, (err_x, err_y))

        # suggestion pills (centered and kept inside card)
        self.hovered_suggestion = -1
        sug_icons = ["~", "*", "+", "#", "@", "%"]
        for i, (sug, pill) in enumerate(zip(self.suggestions, self._suggestion_rects(input_rect, card))):
            hov = pill.collidepoint(mouse)
            if hov:
                self.hovered_suggestion = i
            lift = -2 if hov else 0
            pill = pill.move(0, lift)
            fill = (24, 48, 80, 238) if not hov else (34, 68, 104, 248)
            draw_round_rect(surf, pill, fill, 12)
            draw_round_rect(surf, pill, (68, 122, 180) if not hov else (72, 214, 152), 12, 1)
            icon_txt = self.small.render(sug_icons[i % len(sug_icons)], True, (72, 214, 152))
            label = self.small.render(sug, True, (208, 220, 236))
            surf.blit(icon_txt, (pill.x + 10, pill.y + 8))
            surf.blit(label, (pill.x + 32, pill.y + 8))

        # top-left music control (restored)
        m = self._music_center() + pygame.Vector2(0, dy)
        m_hover = mouse.distance_to(m) <= 40
        pygame.draw.circle(surf, (22, 44, 64, 236), m, 40)
        pygame.draw.circle(surf, (72, 214, 152), m, 40, 3)
        inner = pygame.Surface((66, 66), pygame.SRCALPHA)
        pygame.draw.circle(inner, (255, 255, 255, 230), (33, 33), 26)
        pygame.draw.circle(inner, (236, 241, 255, 255), (33, 33), 26, 2)
        surf.blit(inner, (m.x - 33, m.y - 33))
        if self.music_on:
            pygame.draw.circle(surf, (72, 214, 152, 130), m, 46, 2)
            if self.music_icon_on is not None:
                icon = pygame.transform.smoothscale(self.music_icon_on, (48, 48))
                shadow = icon.copy()
                shadow.fill((0, 0, 0, 90), special_flags=pygame.BLEND_RGBA_MULT)
                surf.blit(shadow, (m.x - 23, m.y - 23))
                surf.blit(icon, (m.x - 24, m.y - 24))
        else:
            if self.music_icon_off is not None:
                icon = pygame.transform.smoothscale(self.music_icon_off, (48, 48))
                shadow = icon.copy()
                shadow.fill((0, 0, 0, 90), special_flags=pygame.BLEND_RGBA_MULT)
                surf.blit(shadow, (m.x - 23, m.y - 23))
                surf.blit(icon, (m.x - 24, m.y - 24))
        if m_hover:
            tip = self.small.render("Toggle Music", True, (226, 232, 240))
            surf.blit(tip, (m.x - 44, m.y + 50))

        # top-right skin control (restored)
        s = self._skin_center() + pygame.Vector2(0, dy)
        pygame.draw.circle(surf, (22, 44, 64, 236), s, 40)
        pygame.draw.circle(surf, (72, 214, 152), s, 40, 3)
        inner_s = pygame.Surface((66, 66), pygame.SRCALPHA)
        pygame.draw.circle(inner_s, (255, 255, 255, 230), (33, 33), 26)
        pygame.draw.circle(inner_s, (236, 241, 255, 255), (33, 33), 26, 2)
        surf.blit(inner_s, (s.x - 33, s.y - 33))
        pygame.draw.circle(surf, (72, 214, 152, 140), s, 46, 2)
        if self.skin_icon is not None:
            icon = pygame.transform.smoothscale(self.skin_icon, (50, 50))
            shadow = icon.copy()
            shadow.fill((0, 0, 0, 90), special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(shadow, (s.x - 24, s.y - 24))
            surf.blit(icon, icon.get_rect(center=(s.x + 1, s.y)))

        # deploy button frame + reactive button
        btn = self._deploy_rect().move(0, dy)
        enabled = self._name_is_valid()
        hover = btn.collidepoint(mouse)
        # outer frame to keep the button visually contained in card
        frame_rect = btn.inflate(12, 8)
        frame_radius = max(18, frame_rect.height // 2)
        frame_layer = pygame.Surface((frame_rect.width, frame_rect.height), pygame.SRCALPHA)
        draw_round_rect(frame_layer, frame_layer.get_rect(), (18, 34, 58, 214), frame_radius)
        draw_round_rect(frame_layer, frame_layer.get_rect(), (52, 86, 130, 140), frame_radius, 1)
        surf.blit(frame_layer, frame_rect.topleft)

        base_scale = 1.0
        scale = 1.03 if (hover and enabled) else base_scale
        bw = int(btn.width * scale)
        bh = int(btn.height * scale)
        bdraw = pygame.Rect(0, 0, bw, bh)
        bdraw.center = btn.center
        if self.invalid_feedback_frames > 0:
            bdraw.x += random.randint(-3, 3)

        top = (60, 194, 138) if enabled else (66, 82, 104)
        bottom = (28, 122, 86) if enabled else (52, 68, 90)
        grad = pygame.Surface((bdraw.width, bdraw.height), pygame.SRCALPHA)
        for y in range(bdraw.height):
            t = y / max(1, bdraw.height - 1)
            c = lerp_color(top, bottom, t)
            pygame.draw.line(grad, c, (0, y), (bdraw.width, y))
        # Clip button fill to rounded shape so no sharp side artifacts appear.
        clip_mask = pygame.Surface((bdraw.width, bdraw.height), pygame.SRCALPHA)
        pygame.draw.rect(clip_mask, (255, 255, 255, 255), clip_mask.get_rect(), border_radius=max(16, bdraw.height // 2))
        grad.blit(clip_mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(grad, bdraw.topleft)
        btn_radius = max(16, bdraw.height // 2)
        draw_round_rect(surf, bdraw, (38, 72, 118), btn_radius, 1)
        if enabled:
            halo = bdraw.inflate(8, 8)
            halo_s = pygame.Surface((halo.width, halo.height), pygame.SRCALPHA)
            draw_round_rect(halo_s, halo_s.get_rect(), (72, 214, 152, 90), max(18, halo.height // 2), 2)
            surf.blit(halo_s, halo.topleft)
        if hover and enabled:
            ring = bdraw.inflate(6, 6)
            draw_round_rect(surf, ring, (72, 214, 152, 120), max(18, ring.height // 2), 2)
        label_txt = "DEPLOY"
        label_shadow = self.h2.render(label_txt, True, (10, 18, 30))
        label = self.h2.render(label_txt, True, (246, 252, 255))
        lrect = label.get_rect(center=bdraw.center)
        surf.blit(label_shadow, (lrect.x + 1, lrect.y + 2))
        surf.blit(label, lrect)
    def _handle_click(self, pos: tuple[int, int]) -> bool:
        m = self._music_center()
        s = self._skin_center()
        input_rect = self._input_rect()
        btn = self._deploy_rect()
        card = self._card_rect()

        # account for intro animation offset during clicks
        intro_t = min(1.0, (pygame.time.get_ticks() - self.ui_intro_ms) / 600.0)
        ease = 1 - (1 - intro_t) ** 3
        dy = int((1 - ease) * 40)
        p = (pos[0], pos[1] - dy)
        handled_ui = False

        self.input_focus = input_rect.collidepoint(p)
        if pygame.Vector2(p).distance_to(m) <= 40:
            self.music_on = self.music.toggle()
            handled_ui = True
        if pygame.Vector2(p).distance_to(s) <= 40:
            if p[0] < s.x:
                self.skin_idx = (self.skin_idx - 1) % len(SKINS)
            else:
                self.skin_idx = (self.skin_idx + 1) % len(SKINS)
            handled_ui = True

        # suggestions
        for sug, pill in zip(self.suggestions, self._suggestion_rects(input_rect, card)):
            if pill.collidepoint(p):
                self.name_text = sug[:16]
                self.error_message = ""
                handled_ui = True

        if btn.collidepoint(p) and self._name_is_valid():
            self._attempt_deploy()
            handled_ui = True
        elif btn.collidepoint(p):
            self.invalid_feedback_frames = 14
            self.music.play_error()
            handled_ui = True

        if not card.collidepoint(p):
            self.input_focus = False
        elif input_rect.collidepoint(p):
            handled_ui = True

        return handled_ui

    def _events(self) -> None:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.VIDEORESIZE:
                self.w = max(980, e.w)
                self.h = max(620, e.h)
                self.screen = pygame.display.set_mode((self.w, self.h), pygame.RESIZABLE)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                handled = self._handle_click(e.pos)
                if not handled:
                    self._throw_food(e.pos[0], e.pos[1])
            elif e.type == pygame.KEYDOWN and self.input_focus:
                if e.key == pygame.K_BACKSPACE:
                    self.name_text = self.name_text[:-1]
                    self.error_message = ""
                elif e.key == pygame.K_RETURN and self._name_is_valid():
                    self._attempt_deploy()
                elif e.key == pygame.K_RETURN:
                    self.invalid_feedback_frames = 14
                    self.music.play_error()
                elif len(self.name_text) < 16 and e.unicode and e.unicode.isprintable():
                    self.name_text += e.unicode
                    self.error_message = ""

    def _attempt_deploy(self) -> None:
        candidate = self.name_text.strip()
        if not self._name_is_valid():
            self.error_message = "Enter at least 3 valid characters."
            self.invalid_feedback_frames = 14
            self.music.play_error()
            return
        if self.username_validator is not None:
            ok, msg = self.username_validator(candidate)
            if not ok:
                self.error_message = msg or "Username taken. Please choose another one."
                self.invalid_feedback_frames = 14
                self.music.play_error()
                return
        self.error_message = ""
        self.result_name = candidate
        self.running = False

    def update(self) -> None:
        self.frame += 1
        self._update_snakes()
        self._update_effects()
        if self.invalid_feedback_frames > 0:
            self.invalid_feedback_frames -= 1

    def draw(self) -> None:
        frame = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._draw_background(frame)
        self._draw_snakes(frame)
        self._draw_effects(frame)
        # Draw UI last so snakes pass under the center card.
        self._draw_ui(frame)
        self.screen.blit(frame, (0, 0))
        pygame.display.flip()

    def run(self, *, keep_pygame: bool = False) -> tuple[str | None, int]:
        while self.running:
            self._events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        if not keep_pygame:
            pygame.quit()
        return self.result_name, self.skin_idx


class PygameLobbyScene:
    """Simple same-window lobby scene shown after pre-lobby deploy."""

    def __init__(
        self,
        *,
        screen: pygame.Surface,
        clock: pygame.time.Clock,
        username: str,
        server_ip: str,
        server_port: int,
    ) -> None:
        self.screen = screen
        self.clock = clock
        self.username = username
        self.server_ip = server_ip
        self.server_port = server_port
        self.w, self.h = self.screen.get_size()
        self.running = True
        self.return_to_prelobby = False
        self.quit_app = False
        self.frame = 0

        self.font_title = pygame.font.SysFont("arial black", 34, bold=True)
        self.font_body = pygame.font.SysFont("bahnschrift", 20, bold=True)
        self.font_small = pygame.font.SysFont("arial", 16)
        self.font_tiny = pygame.font.SysFont("arial", 14)

        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.incoming_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.online_users: list[str] = []
        self.selected_index = -1
        self.logs: list[str] = []
        self.chat_text = ""
        self.input_focus = False
        self.pending_invite_from: str | None = None
        self.start_game = False
        self.start_game_opponent: str | None = None
        self._username_retry_count = 0
        self._username_retry_max = 12
        self._next_username_retry_ms = 0

        self._connect()

    def _append_log(self, text: str) -> None:
        self.logs.append(text)
        if len(self.logs) > 16:
            self.logs = self.logs[-16:]

    def _connect(self) -> None:
        try:
            self.connection = ClientConnection(server_ip=self.server_ip, server_port=self.server_port)
        except Exception as error:
            self._append_log(f"[ERROR] Connect failed: {error}")
            return
        self._append_log(f"[SYSTEM] Connected to {self.server_ip}:{self.server_port}")
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receiver_thread.start()
        try:
            self.connection.send_message(make_username_message(self.username))
            self._append_log(f"[SYSTEM] Username submitted: {self.username}")
        except OSError as error:
            self._append_log(f"[ERROR] Username submit failed: {error}")

    def _retry_username_if_due(self) -> None:
        if self.connection is None:
            return
        if self._next_username_retry_ms <= 0:
            return
        now = pygame.time.get_ticks()
        if now < self._next_username_retry_ms:
            return
        self._next_username_retry_ms = 0
        try:
            self.connection.send_message(make_username_message(self.username))
            self._append_log(f"[SYSTEM] Retrying username: {self.username}")
        except OSError as error:
            self._append_log(f"[ERROR] Username retry failed: {error}")

    def _disconnect(self) -> None:
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
            except Exception as error:
                self.incoming_queue.put(
                    {
                        "type": "socket_error",
                        "payload": {"message": str(error)},
                    }
                )
                break
            self.incoming_queue.put(msg)

    def _drain_queue(self) -> None:
        while True:
            try:
                msg = self.incoming_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_message(msg)

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        payload = message.get("payload", {})
        if msg_type == "socket_error":
            self._append_log(f"[SYSTEM] Connection closed: {payload.get('message', '')}")
            self._disconnect()
            return
        if msg_type == MessageType.CONNECT.value:
            self._append_log(f"[CONNECT] {payload}")
            return
        if msg_type == MessageType.ONLINE_USERS.value:
            self.online_users = list(payload.get("users", []))
            if self.selected_index >= len(self.online_users):
                self.selected_index = -1
            return
        if msg_type == MessageType.INVITATION.value:
            action = str(payload.get("action", "")).lower()
            from_user = str(payload.get("from_user", ""))
            to_user = str(payload.get("to_user", ""))
            if action == "send" and to_user.casefold() == self.username.casefold():
                self.pending_invite_from = from_user
                self._append_log(f"[INVITE] {from_user} invited you. Press A accept / D decline.")
            elif action == "match_started":
                game_id = payload.get("game_id", "unknown")
                self._append_log(f"[MATCH] Started game_id={game_id}")
                opponent = self._extract_opponent_from_match(payload)
                self.start_game_opponent = opponent
                self.start_game = True
                self.running = False
            else:
                self._append_log(f"[INVITE] {payload}")
            return
        if msg_type == MessageType.CHAT.value:
            sender = str(payload.get("sender", "SERVER"))
            text = str(payload.get("message", ""))
            self._append_log(f"[CHAT] {sender}: {text}")
            return
        if msg_type == MessageType.ERROR.value:
            error_message = str(payload.get("message", "Unknown error"))
            self._append_log(f"[ERROR] {error_message}")
            if "username already taken" in error_message.casefold():
                self._username_retry_count += 1
                if self._username_retry_count <= self._username_retry_max:
                    self._next_username_retry_ms = pygame.time.get_ticks() + 450
                else:
                    self._append_log("[ERROR] Username still unavailable. Try Back To PreLobby.")
            return
        self._append_log(f"[INCOMING] {message}")

    def _extract_opponent_from_match(self, payload: dict[str, Any]) -> str | None:
        players = payload.get("players")
        if isinstance(players, list):
            for player in players:
                p = str(player)
                if p.casefold() != self.username.casefold():
                    return p
        from_user = str(payload.get("from_user", ""))
        to_user = str(payload.get("to_user", ""))
        for candidate in (from_user, to_user):
            if candidate and candidate.casefold() != self.username.casefold():
                return candidate
        return None

    def _selected_opponent(self) -> str | None:
        if self.selected_index < 0 or self.selected_index >= len(self.online_users):
            return None
        selected = self.online_users[self.selected_index]
        if selected.casefold() == self.username.casefold():
            return None
        return selected

    def _send_invite(self) -> None:
        opponent = self._selected_opponent()
        if opponent is None or self.connection is None:
            return
        try:
            self.connection.send_message(
                make_invitation_message(
                    from_user=self.username,
                    to_user=opponent,
                    action="send",
                )
            )
            self._append_log(f"[SYSTEM] Invitation sent to {opponent}")
        except OSError as error:
            self._append_log(f"[ERROR] Invite failed: {error}")

    def _send_chat(self) -> None:
        text = self.chat_text.strip()
        if not text or self.connection is None:
            return
        try:
            self.connection.send_message(make_chat_message(sender=self.username, message=text))
            self.chat_text = ""
        except OSError as error:
            self._append_log(f"[ERROR] Chat failed: {error}")

    def _reply_invite(self, accept: bool) -> None:
        if self.pending_invite_from is None or self.connection is None:
            return
        action = "accept" if accept else "decline"
        try:
            self.connection.send_message(
                make_invitation_message(
                    from_user=self.pending_invite_from,
                    to_user=self.username,
                    action=action,
                )
            )
            self._append_log(f"[INVITE] You {action}ed {self.pending_invite_from}.")
        except OSError as error:
            self._append_log(f"[ERROR] Invite reply failed: {error}")
        self.pending_invite_from = None

    def _users_rect(self) -> pygame.Rect:
        return pygame.Rect(40, 120, 320, self.h - 240)

    def _logs_rect(self) -> pygame.Rect:
        return pygame.Rect(390, 120, self.w - 430, self.h - 240)

    def _invite_button_rect(self) -> pygame.Rect:
        return pygame.Rect(40, self.h - 104, 155, 44)

    def _disconnect_button_rect(self) -> pygame.Rect:
        return pygame.Rect(205, self.h - 104, 155, 44)

    def _invite_popup_rect(self) -> pygame.Rect:
        width = min(560, self.w - 120)
        height = 220
        return pygame.Rect((self.w - width) // 2, (self.h - height) // 2, width, height)

    def _accept_invite_button_rect(self) -> pygame.Rect:
        popup = self._invite_popup_rect()
        return pygame.Rect(popup.x + 48, popup.bottom - 72, 190, 44)

    def _decline_invite_button_rect(self) -> pygame.Rect:
        popup = self._invite_popup_rect()
        return pygame.Rect(popup.right - 48 - 190, popup.bottom - 72, 190, 44)

    def _chat_rect(self) -> pygame.Rect:
        return pygame.Rect(390, self.h - 104, self.w - 430, 44)

    def _handle_mouse(self, pos: tuple[int, int]) -> None:
        if self.pending_invite_from is not None:
            if self._accept_invite_button_rect().collidepoint(pos):
                self._reply_invite(True)
                return
            if self._decline_invite_button_rect().collidepoint(pos):
                self._reply_invite(False)
                return
            # While popup is shown, consume all clicks so it behaves as modal.
            return

        if self._invite_button_rect().collidepoint(pos):
            self._send_invite()
            return
        if self._disconnect_button_rect().collidepoint(pos):
            self.return_to_prelobby = True
            self.running = False
            return
        chat_rect = self._chat_rect()
        self.input_focus = chat_rect.collidepoint(pos)

        users_rect = self._users_rect()
        if users_rect.collidepoint(pos):
            row_h = 28
            idx = (pos[1] - (users_rect.y + 46)) // row_h
            if 0 <= idx < len(self.online_users):
                self.selected_index = int(idx)

    def _events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit_app = True
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                self.w = max(980, event.w)
                self.h = max(620, event.h)
                self.screen = pygame.display.set_mode((self.w, self.h), pygame.RESIZABLE)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_mouse(event.pos)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.return_to_prelobby = True
                    self.running = False
                elif event.key == pygame.K_a:
                    self._reply_invite(True)
                elif event.key == pygame.K_d:
                    self._reply_invite(False)
                elif self.input_focus:
                    if event.key == pygame.K_RETURN:
                        self._send_chat()
                    elif event.key == pygame.K_BACKSPACE:
                        self.chat_text = self.chat_text[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        if len(self.chat_text) < 120:
                            self.chat_text += event.unicode

    def _draw_button(self, rect: pygame.Rect, label: str, *, accent: tuple[int, int, int]) -> None:
        mouse = pygame.mouse.get_pos()
        hover = rect.collidepoint(mouse)
        fill = (24, 46, 72) if not hover else (34, 66, 98)
        pygame.draw.rect(self.screen, fill, rect, border_radius=14)
        pygame.draw.rect(self.screen, accent, rect, 2, border_radius=14)
        txt = self.font_small.render(label, True, (230, 238, 248))
        self.screen.blit(txt, txt.get_rect(center=rect.center))

    def _draw(self) -> None:
        self.frame += 1
        frame = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            c = lerp_color((9, 28, 44), (8, 20, 34), t)
            pygame.draw.line(frame, c, (0, y), (self.w, y))

        panel = pygame.Rect(24, 24, self.w - 48, self.h - 48)
        pygame.draw.rect(frame, (12, 30, 48, 226), panel, border_radius=22)
        pygame.draw.rect(frame, (68, 210, 152), panel, 2, border_radius=22)

        title = self.font_title.render("PYTHON ARENA LOBBY", True, (226, 244, 254))
        subtitle = self.font_small.render(
            f"Connected as {self.username}  |  {self.server_ip}:{self.server_port}",
            True,
            (146, 184, 204),
        )
        frame.blit(title, (44, 40))
        frame.blit(subtitle, (46, 82))

        users = self._users_rect()
        logs = self._logs_rect()
        pygame.draw.rect(frame, (18, 40, 62, 240), users, border_radius=14)
        pygame.draw.rect(frame, (64, 122, 180), users, 1, border_radius=14)
        pygame.draw.rect(frame, (16, 34, 56, 240), logs, border_radius=14)
        pygame.draw.rect(frame, (64, 122, 180), logs, 1, border_radius=14)
        frame.blit(self.font_body.render("Online Players", True, (96, 228, 168)), (users.x + 14, users.y + 12))
        frame.blit(self.font_body.render("Lobby Events", True, (96, 228, 168)), (logs.x + 14, logs.y + 12))

        row_y = users.y + 46
        row_h = 28
        for idx, user in enumerate(self.online_users):
            row = pygame.Rect(users.x + 10, row_y + idx * row_h, users.width - 20, row_h - 2)
            selected = idx == self.selected_index
            if selected:
                pygame.draw.rect(frame, (34, 84, 122), row, border_radius=8)
            label = f"{user} (You)" if user.casefold() == self.username.casefold() else user
            color = (236, 245, 255) if selected else (196, 218, 236)
            frame.blit(self.font_small.render(label, True, color), (row.x + 8, row.y + 5))

        log_y = logs.y + 48
        for line in self.logs[-14:]:
            frame.blit(self.font_tiny.render(line[:90], True, (202, 220, 236)), (logs.x + 12, log_y))
            log_y += 22

        self.screen.blit(frame, (0, 0))
        self._draw_button(self._invite_button_rect(), "Invite Selected", accent=(74, 210, 156))
        self._draw_button(self._disconnect_button_rect(), "Back To PreLobby", accent=(238, 128, 128))
        chat_rect = self._chat_rect()
        pygame.draw.rect(self.screen, (22, 42, 66), chat_rect, border_radius=12)
        pygame.draw.rect(
            self.screen,
            (72, 214, 152) if self.input_focus else (72, 118, 160),
            chat_rect,
            2,
            border_radius=12,
        )
        chat_hint = self.chat_text if self.chat_text else "Type message and press Enter..."
        chat_color = (235, 243, 252) if self.chat_text else (120, 146, 170)
        self.screen.blit(self.font_small.render(chat_hint, True, chat_color), (chat_rect.x + 12, chat_rect.y + 12))

        if self.pending_invite_from is not None:
            overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 145))
            self.screen.blit(overlay, (0, 0))
            popup = self._invite_popup_rect()
            pygame.draw.rect(self.screen, (18, 40, 62), popup, border_radius=18)
            pygame.draw.rect(self.screen, (82, 188, 242), popup, 2, border_radius=18)
            title = self.font_body.render("Incoming Invitation", True, (238, 246, 255))
            msg = self.font_small.render(
                f"{self.pending_invite_from} invited you to play.",
                True,
                (255, 202, 128),
            )
            hint = self.font_tiny.render("Choose Accept or Reject", True, (164, 190, 214))
            self.screen.blit(title, (popup.x + 28, popup.y + 24))
            self.screen.blit(msg, (popup.x + 28, popup.y + 72))
            self.screen.blit(hint, (popup.x + 28, popup.y + 100))
            self._draw_button(self._accept_invite_button_rect(), "Accept", accent=(90, 220, 140))
            self._draw_button(self._decline_invite_button_rect(), "Reject", accent=(236, 120, 120))
        pygame.display.flip()

    def run(self) -> tuple[bool, bool, bool, str | None]:
        while self.running:
            self._events()
            self._drain_queue()
            self._retry_username_if_due()
            self._draw()
            self.clock.tick(FPS)
        self._disconnect()
        return self.return_to_prelobby, self.quit_app, self.start_game, self.start_game_opponent


def run_prelobby_to_lobby(
    *,
    server_ip: str,
    server_port: int,
    username_validator: Callable[[str], tuple[bool, str]] | None = None,
) -> None:
    """Run pre-lobby then lobby in one Pygame window with scene switching."""

    seed_name = ""
    seed_error = ""
    resume_lobby_username: str | None = None
    shared_clock: pygame.time.Clock | None = None
    shared_screen: pygame.Surface | None = None
    while True:
        if resume_lobby_username is None:
            app = PreLobby(
                initial_username=seed_name,
                error_message=seed_error,
                username_validator=username_validator,
            )
            username, _skin_idx = app.run(keep_pygame=True)
            if not username:
                pygame.quit()
                return
            shared_screen = app.screen
            shared_clock = app.clock
        else:
            username = resume_lobby_username
            resume_lobby_username = None
            if pygame.get_init() and pygame.display.get_surface() is not None:
                shared_screen = pygame.display.get_surface()
            else:
                pygame.init()
                shared_screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
            shared_clock = pygame.time.Clock()

        lobby = PygameLobbyScene(
            screen=shared_screen,
            clock=shared_clock,
            username=username,
            server_ip=server_ip,
            server_port=server_port,
        )
        back_to_prelobby, quit_app, start_game, opponent = lobby.run()
        if quit_app:
            pygame.quit()
            return
        if start_game:
            from client.game_window import main as pygame_game_main

            # Stay inside pygame flow for gameplay handoff.
            requested_lobby_return = pygame_game_main(
                server_ip=server_ip,
                server_port=server_port,
                username=username,
                preferred_opponent=opponent,
                return_to_tk_lobby=False,
                keep_window_open_on_return=True,
            )
            if requested_lobby_return:
                resume_lobby_username = username
                continue
            pygame.quit()
            return
        if back_to_prelobby:
            seed_name = username
            seed_error = ""
            continue
        pygame.quit()
        return


def main() -> None:
    app = PreLobby()
    username, skin_idx = app.run()
    if username:
        print(f"Deploying user={username} skin={skin_idx}")


if __name__ == "__main__":
    main()
