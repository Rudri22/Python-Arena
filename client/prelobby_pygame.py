from __future__ import annotations

import json
import math
import queue
import random
import threading
import tempfile
import wave
from array import array
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pygame

from client.network import ClientConnection
from client.snake_skins import (
    draw_segmented_snake,
    draw_snake_eyes,
    draw_snake_hat,
    draw_snake_tail,
)
from shared.protocol import (
    EYE_STYLES,
    HATS,
    MessageType,
    SKIN_COLORS,
    SnakeSkin,
    TAILS,
    make_invitation_message,
    make_username_message,
    sanitize_skin,
    skin_to_dict,
)
from client.controls_config import (
    DEFAULT_DIRECTION_BINDINGS,
    DIRECTION_ACTIONS,
    display_key_name,
    load_direction_bindings,
    normalize_key_name,
    save_direction_bindings,
)

_SKIN_PREFS_PATH = Path(__file__).resolve().parent / "assets" / "skin_prefs.json"
_SOUNDS_DIR = Path(__file__).resolve().parents[1] / "assets" / "sounds"


def _load_skin_prefs() -> SnakeSkin:
    try:
        if _SKIN_PREFS_PATH.exists():
            data = json.loads(_SKIN_PREFS_PATH.read_text(encoding="utf-8"))
            from shared.protocol import sanitize_skin
            skin = sanitize_skin(data)
            # Texture customization is intentionally disabled in UI.
            return dataclass_replace(skin, pattern="solid")
    except Exception:
        pass
    return SnakeSkin()


def _save_skin_prefs(skin: SnakeSkin) -> None:
    try:
        from shared.protocol import skin_to_dict
        _SKIN_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SKIN_PREFS_PATH.write_text(json.dumps(skin_to_dict(skin), indent=2), encoding="utf-8")
    except Exception:
        pass


"""
Python-Arena Prelobby + Lobby (single-file feature map)
========================================================

This file contains TWO major runtime scenes:

1) `PreLobby` (pygame animated deployment scene)
   - Background rendering: sky/grid/terrain/fx
   - Ambient AI snakes and food interaction
   - Username + deploy UI flow
   - Deploy transition animation into lobby

2) `PygameLobbyScene` (same-window multiplayer lobby)
   - Server connection + protocol message handling
   - Online players / invitations / private+public chat
   - Leaderboard + spectator entry points
   - Left "Your Snake" visual card and skin controls

How to read this file quickly:
- Utility/data classes first (`MusicController`, `Star`, `Particle`, etc.)
- `PreLobby` scene (visual intro + deploy validation)
- `PygameLobbyScene` scene (networked lobby UI and logic)
- Entry helpers (`run_prelobby_to_lobby`, `main`)
"""

# Frontend feature names and ownership map.
# Keep these IDs stable so backend and QA references stay aligned.
PRELOBBY_FEATURES: dict[str, dict[str, str]] = {
    "PRE_01_MUSIC_TOGGLE": {
        "ui": "Top-left music icon button (music on/off).",
        "frontend_owner": "PreLobby._music_center + PreLobby._handle_click + MusicController.toggle",
        "backend_owner": "None (local-only audio toggle).",
    },
    "PRE_02_STATUS_BADGE": {
        "ui": "Status badge: IDLE / TYPING / READY.",
        "frontend_owner": "PreLobby._draw_ui (status derivation from name input state)",
        "backend_owner": "None (local-only state feedback).",
    },
    "PRE_03_DEPLOY_NAME_INPUT": {
        "ui": "Deploy name text box with cursor/editing and 0/16 counter.",
        "frontend_owner": "PreLobby._events + PreLobby._draw_ui",
        "backend_owner": "Validated and registered in lobby via USERNAME message (PygameLobbyScene._connect/_retry_username_if_due).",
    },
    "PRE_04_SUGGESTED_NAMES": {
        "ui": "Suggested-name pills under the input.",
        "frontend_owner": "PreLobby.suggestions + PreLobby._suggestion_rects + PreLobby._handle_click",
        "backend_owner": "None (local-only autofill helpers).",
    },
    "PRE_05_DEPLOY_BUTTON": {
        "ui": "DEPLOY action button.",
        "frontend_owner": "PreLobby._deploy_rect + PreLobby._handle_click + PreLobby._attempt_deploy",
        "backend_owner": "Triggers lobby entry, then USERNAME submission to server in PygameLobbyScene.",
    },
    "PRE_06_DEPLOY_TRANSITION": {
        "ui": "Eye-close/blackout transition from pre-lobby into lobby.",
        "frontend_owner": "PreLobby._deploy_close_progress + PreLobby.update + PreLobby.draw",
        "backend_owner": "None (local scene transition).",
    },
    "LOB_01_USERNAME_SUBMISSION": {
        "ui": "Post-deploy username handoff to multiplayer lobby.",
        "frontend_owner": "PygameLobbyScene._connect + make_username_message",
        "backend_owner": "server/client_handler.py -> handle_incoming_message(MessageType.USERNAME)",
    },
    "LOB_02_ONLINE_AND_LEADERBOARD_SYNC": {
        "ui": "Online list + leaderboard data refresh.",
        "frontend_owner": "PygameLobbyScene._handle_message for ONLINE_USERS payload updates",
        "backend_owner": "server/client_handler.py -> broadcast_online_users + server/state.py snapshots",
    },
}

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
    """Handles generated loop music + short SFX (eat/error) for pre-lobby feedback."""
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
            lobby_file = _SOUNDS_DIR / "preolobby-lobby.mpeg"
            if lobby_file.exists():
                pygame.mixer.music.load(str(lobby_file))
            else:
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
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.pause()
            self.playing = False
        else:
            pygame.mixer.music.unpause()
            if not pygame.mixer.music.get_busy():
                pygame.mixer.music.play(-1)
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
    """
    Animated pre-lobby deployment scene.

    Feature map (PreLobby):
    - Asset/music init: `__init__`, `_load_asset_icon`, `_load_skin_icon`
    - Snake simulation:
      `_spawn_snakes`, `_respawn_snake`, `_update_snakes`, `_segment_alpha`
    - Interaction effects:
      `_throw_food`, `_trigger_scatter`, `_spawn_explosion`, `_spawn_crumb_burst`
    - Ambient rendering:
      `_bg_gradient`, `_draw_background`, `_draw_snakes`, `_draw_effects`
    - UI and input:
      `_draw_ui`, `_handle_click`, `_events`, `_attempt_deploy`
    - Deploy transition:
      `_deploy_close_progress`, `update`, `draw`, `run`
    """
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
        pygame.key.set_repeat(350, 35)
        self.clock = pygame.time.Clock()
        self.w = WIDTH
        self.h = HEIGHT
        self.frame = 0
        self.running = True
        self.result_name: str | None = None
        self.deploy_transition_active = False
        self.deploy_transition_start_ms = 0
        self.deploy_transition_duration_ms = 820
        self.deploy_target_name: str | None = None

        self.music = MusicController()
        self.music_on = self.music.toggle()

        self.skin_idx = 0
        self.name_text = initial_username[:16]
        self.name_cursor = len(self.name_text)
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
        self.music_icon_on = self._load_asset_icon("music.png")
        self.music_icon_off = self._load_asset_icon("no-music.png")

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

        # giant background eye spanning left-to-right with neutral grey shade
        close_progress = self._deploy_close_progress()
        # Keep center eye lane fully idle until DEPLOY is clicked.
        total_close = close_progress

        eye_layer = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        cx, cy = self.w // 2, int(self.h * 0.50)

        # stretched shell/lid shape
        shell_w = int(self.w * 1.25)
        shell_h_open = int(self.h * 0.34)
        shell_h = shell_h_open
        shell_bottom = cy + shell_h_open // 2
        shell_rect = pygame.Rect(cx - shell_w // 2, shell_bottom - shell_h, shell_w, shell_h)
        glow = pygame.Surface((shell_w + 220, shell_h + 180), pygame.SRCALPHA)
        pygame.draw.ellipse(glow, (96, 104, 114, 28), glow.get_rect().inflate(-50, -30))
        eye_layer.blit(glow, (cx - glow.get_width() // 2, cy - glow.get_height() // 2))
        pygame.draw.ellipse(eye_layer, (46, 54, 64, 118), shell_rect)
        pygame.draw.ellipse(eye_layer, (28, 34, 42, 150), shell_rect, 3)

        # subtle skin-like texture (grey, not green)
        if shell_w > 60 and shell_h > 10:
            for ox, oy, rw, rh in (
                (-0.22, -0.12, 0.12, 0.14),
                (0.08, 0.05, 0.10, 0.12),
                (0.24, -0.06, 0.11, 0.13),
            ):
                spot = pygame.Rect(
                    int(cx + shell_w * ox),
                    int(cy + shell_h * oy),
                    int(shell_w * rw),
                    int(shell_h * rh),
                )
                pygame.draw.ellipse(eye_layer, (62, 70, 82, 48), spot)

        # top/bottom eyelid shades for a cleaner, cinematic snake-eye lane
        top_lid = pygame.Rect(
            cx - shell_w // 2,
            shell_rect.top - int(shell_h_open * 0.34),
            shell_w,
            int(shell_h_open * 0.62),
        )
        bot_lid = pygame.Rect(
            cx - shell_w // 2,
            shell_bottom - int(shell_h_open * 0.28),
            shell_w,
            int(shell_h_open * 0.62),
        )
        pygame.draw.ellipse(eye_layer, (14, 18, 24, 168), top_lid)
        pygame.draw.ellipse(eye_layer, (10, 14, 20, 154), bot_lid)

        # Keep iris/pupil fixed; shade will close over it
        # Vertical iris (flipped orientation): tall eye, narrow width.
        eye_w = max(14, int(shell_w * 0.085))
        eye_h_open = int(shell_h_open * 0.88)
        eye_h = eye_h_open
        eye_bottom = cy + eye_h_open // 2
        eye_rect = pygame.Rect(cx - eye_w // 2, eye_bottom - eye_h, eye_w, eye_h)
        # Middle eye style to match side-eye reference (yellow iris + black vertical slit).
        pygame.draw.ellipse(eye_layer, (214, 178, 62, 176), eye_rect)
        eye_inner = eye_rect.inflate(-max(2, eye_w // 6), -max(4, eye_h // 12))
        pygame.draw.ellipse(eye_layer, (255, 219, 77, 196), eye_inner)
        # natural eyelid crop: only middle band of the eye remains visible at idle.
        lid_cut = max(1, int(eye_h * 0.20))
        top_cover = pygame.Rect(eye_rect.x - 2, eye_rect.y - 2, eye_rect.width + 4, lid_cut)
        bot_cover = pygame.Rect(eye_rect.x - 2, eye_rect.bottom - lid_cut + 2, eye_rect.width + 4, lid_cut)
        pygame.draw.ellipse(eye_layer, (10, 14, 20, 170), top_cover)
        pygame.draw.ellipse(eye_layer, (8, 12, 18, 170), bot_cover)

        # sharp snake slit: pointed vertical oval (not rectangular).
        slit_w = max(2, int(eye_w * 0.24))
        slit_h = max(1, int(eye_h * 0.86))
        slit_top = eye_bottom - slit_h
        slit_mid = slit_top + slit_h // 2
        sx_l = cx - slit_w // 2
        sx_r = cx + slit_w // 2
        slit_pts = [
            (cx, slit_top),
            (sx_r, slit_mid - max(1, slit_h // 5)),
            (sx_r - 1, slit_mid + max(1, slit_h // 5)),
            (cx, eye_bottom),
            (sx_l + 1, slit_mid + max(1, slit_h // 5)),
            (sx_l, slit_mid - max(1, slit_h // 5)),
        ]
        pygame.draw.polygon(eye_layer, (8, 10, 14, 236), slit_pts)

        # no green highlight over the middle eye (keep center clear)

        # Closing shade comes down from top and hides the eye (without shrinking pupil itself).
        if total_close > 0.0:
            cover_h = max(1, int(shell_h_open * total_close))
            shade = pygame.Surface((shell_rect.width, shell_rect.height), pygame.SRCALPHA)
            pygame.draw.rect(shade, (8, 12, 18, 232), pygame.Rect(0, 0, shell_rect.width, cover_h))
            mask = pygame.Surface((shell_rect.width, shell_rect.height), pygame.SRCALPHA)
            pygame.draw.ellipse(mask, (255, 255, 255, 255), mask.get_rect())
            shade.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            eye_layer.blit(shade, shell_rect.topleft)
        surf.blit(eye_layer, (0, 0))

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

    # Feature: PRE_01_MUSIC_TOGGLE (music button anchor point).
    def _music_center(self) -> pygame.Vector2:
        return pygame.Vector2(92, 84)

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

    # Features: PRE_01_MUSIC_TOGGLE, PRE_02_STATUS_BADGE, PRE_03_DEPLOY_NAME_INPUT,
    # PRE_04_SUGGESTED_NAMES, PRE_05_DEPLOY_BUTTON.
    def _draw_ui(self, surf: pygame.Surface, bg_under: pygame.Surface | None = None) -> None:
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

        # snake eye badges (blink + deploy close transition)
        close_progress = self._deploy_close_progress()
        for idx, ex in enumerate((card.x + 36, card.right - 36)):
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
            # eye blink pattern + deploy transition close
            blink = ((self.frame + idx * 53) % 120) < 8
            if close_progress > 0.0:
                openness = max(1, int((1.0 - close_progress) * 9))
                if openness <= 2:
                    pygame.draw.line(surf, (255, 219, 77), (ex - 5, cy - 4), (ex + 5, cy - 4), 2)
                else:
                    eye_y = cy - 8 + (9 - openness) // 2
                    pygame.draw.ellipse(surf, (255, 219, 77), pygame.Rect(ex - 4, eye_y, 8, openness))
                    if openness >= 5:
                        pygame.draw.rect(surf, (12, 16, 22), pygame.Rect(ex - 1, eye_y, 2, openness - 1), border_radius=1)
            elif blink:
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
            tw = self.h2.size(self.name_text[: self.name_cursor])[0]
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
        for i, (sug, pill) in enumerate(zip(self.suggestions, self._suggestion_rects(input_rect, card))):
            hov = pill.collidepoint(mouse)
            if hov:
                self.hovered_suggestion = i
            lift = -2 if hov else 0
            pill = pill.move(0, lift)
            fill = (24, 48, 80, 238) if not hov else (34, 68, 104, 248)
            draw_round_rect(surf, pill, fill, 12)
            draw_round_rect(surf, pill, (68, 122, 180) if not hov else (72, 214, 152), 12, 1)
            label = self.small.render(sug, True, (208, 220, 236))
            surf.blit(label, (pill.x + 12, pill.y + 8))

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

        # top-right snake icon removed by request

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

        # Deploy transition: reveal the real scene behind the middle card while eye closes.
        if close_progress > 0.0:
            fade_alpha = min(255, max(0, int(255 * close_progress)))
            fade_rect = card.inflate(26, 20)
            reveal_rect = fade_rect.clip(surf.get_rect())
            if reveal_rect.width > 0 and reveal_rect.height > 0 and bg_under is not None:
                reveal = bg_under.subsurface(reveal_rect).copy()
                reveal.set_alpha(fade_alpha)
                surf.blit(reveal, reveal_rect.topleft)
    # Features: PRE_01_MUSIC_TOGGLE, PRE_03_DEPLOY_NAME_INPUT, PRE_04_SUGGESTED_NAMES, PRE_05_DEPLOY_BUTTON.
    def _handle_click(self, pos: tuple[int, int]) -> bool:
        m = self._music_center()
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
        if self.input_focus:
            self.name_cursor = self._cursor_index_from_x(self.h2, self.name_text, input_rect.x + 64, p[0])
        if pygame.Vector2(p).distance_to(m) <= 40:
            self.music_on = self.music.toggle()
            handled_ui = True

        # suggestions
        for sug, pill in zip(self.suggestions, self._suggestion_rects(input_rect, card)):
            if pill.collidepoint(p):
                self.name_text = sug[:16]
                self.name_cursor = len(self.name_text)
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

    # Features: PRE_03_DEPLOY_NAME_INPUT, PRE_05_DEPLOY_BUTTON (mouse/keyboard deploy flow).
    def _events(self) -> None:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.VIDEORESIZE:
                self.w = max(980, e.w)
                self.h = max(620, e.h)
                self.screen = pygame.display.set_mode((self.w, self.h), pygame.RESIZABLE)
            elif self.deploy_transition_active:
                # Freeze interactions while eye-close transition plays.
                continue
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                handled = self._handle_click(e.pos)
                if not handled:
                    self._throw_food(e.pos[0], e.pos[1])
            elif e.type == pygame.KEYDOWN and self.input_focus:
                if e.key == pygame.K_BACKSPACE:
                    if self.name_cursor > 0:
                        self.name_text = self.name_text[: self.name_cursor - 1] + self.name_text[self.name_cursor :]
                        self.name_cursor -= 1
                    self.error_message = ""
                elif e.key == pygame.K_DELETE:
                    if self.name_cursor < len(self.name_text):
                        self.name_text = self.name_text[: self.name_cursor] + self.name_text[self.name_cursor + 1 :]
                    self.error_message = ""
                elif e.key == pygame.K_LEFT:
                    self.name_cursor = max(0, self.name_cursor - 1)
                elif e.key == pygame.K_RIGHT:
                    self.name_cursor = min(len(self.name_text), self.name_cursor + 1)
                elif e.key == pygame.K_HOME:
                    self.name_cursor = 0
                elif e.key == pygame.K_END:
                    self.name_cursor = len(self.name_text)
                elif e.key == pygame.K_RETURN and self._name_is_valid():
                    self._attempt_deploy()
                elif e.key == pygame.K_RETURN:
                    self.invalid_feedback_frames = 14
                    self.music.play_error()
                elif len(self.name_text) < 16 and e.unicode and e.unicode.isprintable():
                    self.name_text = (
                        self.name_text[: self.name_cursor]
                        + e.unicode
                        + self.name_text[self.name_cursor :]
                    )
                    self.name_cursor = min(len(self.name_text), self.name_cursor + 1)
                    self.error_message = ""

    def _cursor_index_from_x(
        self,
        font: pygame.font.Font,
        text: str,
        text_left_x: int,
        mouse_x: int,
    ) -> int:
        rel_x = max(0, mouse_x - text_left_x)
        if not text:
            return 0
        for idx in range(len(text) + 1):
            if font.size(text[:idx])[0] >= rel_x:
                return idx
        return len(text)

    # Feature: PRE_05_DEPLOY_BUTTON (validation + deploy trigger).
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
        self.deploy_target_name = candidate
        self.deploy_transition_active = True
        self.deploy_transition_start_ms = pygame.time.get_ticks()

    # Feature: PRE_06_DEPLOY_TRANSITION.
    def _deploy_close_progress(self) -> float:
        if not self.deploy_transition_active:
            return 0.0
        elapsed = pygame.time.get_ticks() - self.deploy_transition_start_ms
        return min(1.0, max(0.0, elapsed / max(1, self.deploy_transition_duration_ms)))

    # Feature: PRE_06_DEPLOY_TRANSITION (ends scene when close animation completes).
    def update(self) -> None:
        self.frame += 1
        self._update_snakes()
        self._update_effects()
        if self.deploy_transition_active and self._deploy_close_progress() >= 1.0:
            self.result_name = self.deploy_target_name
            self.running = False
        if self.invalid_feedback_frames > 0:
            self.invalid_feedback_frames -= 1

    # Features: PRE_02_STATUS_BADGE, PRE_06_DEPLOY_TRANSITION (render step).
    def draw(self) -> None:
        frame = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._draw_background(frame)
        self._draw_snakes(frame)
        self._draw_effects(frame)
        # Draw UI last so snakes pass under the center card.
        under_ui = frame.copy()
        self._draw_ui(frame, under_ui)
        close_progress = self._deploy_close_progress()
        if close_progress > 0.0:
            alpha = min(255, max(0, int(235 * (close_progress**0.9))))
            blackout = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            blackout.fill((0, 0, 0, alpha))
            frame.blit(blackout, (0, 0))
        self.screen.blit(frame, (0, 0))
        pygame.display.flip()

    # Features: PRE_03_DEPLOY_NAME_INPUT, PRE_05_DEPLOY_BUTTON, PRE_06_DEPLOY_TRANSITION (main loop).
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
    """
    Networked lobby scene shown in the SAME window after pre-lobby deploy.

    Feature map (Lobby):
    - Connection lifecycle:
      `_connect`, `_disconnect`, `_receiver_loop`, `_drain_queue`, `_handle_message`
    - Invitations:
      `_send_invite`, `_reply_invite`, `_cancel_outgoing_invite`, invite rect helpers
    - Chat (public + private tabs/dropdown):
      `_send_chat`, `_set_private_target`, `_chat_target_*`, `_current_chat_bubbles`
    - Online players + spectator:
      `_player_row_rect`, `_player_eye_rect`, `_start_spectate`
    - Scroll systems:
      `_players_scroll*`, `_incoming_invites_scroll*`, `_chat_scroll*`
    - Layout helpers:
      `_left_panel_rect`, `_logs_rect`, `_users_rect`, `_right_rect*`, etc.
    - Drawing:
      `_draw_button`, `_ranked_players`, `_draw`
    - Runtime loop:
      `_events`, `run`
    """

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
        pygame.key.set_repeat(350, 35)
        self.username = username
        self.server_ip = server_ip
        self.server_port = server_port
        self.w, self.h = self.screen.get_size()
        self.running = True
        self.return_to_prelobby = False
        self.quit_app = False
        self.frame = 0

        # Standard lobby typography
        self.font_title = pygame.font.SysFont("arial black", 34, bold=True)
        self.font_body = pygame.font.SysFont("bahnschrift", 20, bold=True)
        self.font_small = pygame.font.SysFont("arial", 16)
        self.font_tiny = pygame.font.SysFont("arial", 14)
        # Snake-style fonts only for specific hero elements
        self.font_orbitron = pygame.font.SysFont("papyrus", 20, bold=True)  # used by CHANGE SKIN
        self.font_header_sub = pygame.font.SysFont("arial", 18, bold=True)
        self.font_snake_title = pygame.font.SysFont("papyrus", 46, bold=True)
        self.font_snake_panel = pygame.font.SysFont("papyrus", 36, bold=True)
        self.font_cinzel = pygame.font.SysFont("Cinzel Decorative", 15, bold=True)
        self.cup_icon = self._load_asset_icon("cup.gif")
        self.rank1_icon = self._load_asset_icon("rank1.png")
        self.rank2_icon = self._load_asset_icon("rank2.png")
        self.rank3_icon = self._load_asset_icon("rank3.png")
        self.change_skin_icon = self._load_asset_icon("change_skin_snake.png")
        # Quick-cycle palette keys for the prev/next dots on the left panel.
        self.skin_color_keys: list[str] = list(SKIN_COLORS.keys())
        self.current_skin: SnakeSkin = _load_skin_prefs()
        self.pending_skin: SnakeSkin = SnakeSkin()
        self.skin_modal_open: bool = False
        self.skin_modal_tab: str = "color"
        self._preview_cache: tuple[SnakeSkin | None, pygame.Surface | None] = (None, None)
        self.match_skins: dict[str, dict[str, str]] = {}

        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.incoming_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.online_users: list[str] = []
        self.online_name_by_cf: dict[str, str] = {}
        self.user_session_by_cf: dict[str, int] = {}
        self.idle_users: set[str] = set()
        self.active_matches: list[dict[str, Any]] = []
        self.active_players_count = 0
        self.selected_index = -1
        self.logs: list[str] = []
        self.chat_bubbles: list[tuple[str, str, bool]] = []
        self.private_chat_threads: dict[str, list[tuple[str, str, bool]]] = {}
        self.private_chat_tabs: list[str] = []
        self.chat_mode = "public"
        self.active_private_target: str | None = None
        self.chat_text = ""
        self.chat_cursor = 0
        self.input_focus = False
        self.player_popup_target: str | None = None
        self.pending_invite_from: str | None = None
        self.pending_invites: list[str] = []
        self.invite_name_text = ""
        self.invite_name_cursor = 0
        self.invite_name_focus = False
        self.invite_status_by_user: dict[str, str] = {}
        self.outgoing_pending_invites: list[str] = []
        self.pending_invite_request_target: str | None = None
        self.wins_by_user_cf: dict[str, int] = {}
        self.join_order_cf: dict[str, int] = {}
        self._next_join_order = 1
        self.leaderboard_scroll = 0
        self.players_scroll = 0
        self.players_expanded = False
        self.players_expand_t = 0.0
        self.players_expand_speed = 0.2
        self.pending_invites_scroll = 0
        self.pending_invites_dragging = False
        self.pending_invites_drag_offset_y = 0
        self.players_dragging = False
        self.players_drag_offset_y = 0
        self.chat_scroll = 0
        self.chat_dragging = False
        self.chat_drag_offset_y = 0
        self.chat_target_open = False
        self.chat_target_scroll = 0
        self._last_invite_target: str | None = None
        self.start_game = False
        self.start_game_opponent: str | None = None
        self.start_game_as_spectator = False
        self.pending_lobby_spectate_target: str | None = None
        self.pending_lobby_spectate_game_id: str | None = None
        self._online_shrink_candidate: set[str] = set()
        self._online_shrink_started_ms = 0
        self._username_retry_count = 0
        self._username_retry_max = 12
        self._next_username_retry_ms = 0
        self.quick_match_waiting = False
        self._next_quick_match_ping_ms = 0
        self.controls_popup_open = False
        self.controls_waiting_action: str | None = None
        self.controls_saved_bindings = load_direction_bindings()
        self.controls_bindings = dict(self.controls_saved_bindings)
        self.controls_feedback = ""
        self.controls_feedback_until_ms = 0
        self.controls_key_tile = self._load_input_prompt_asset("Tiles (White)/tile_0051.png")
        self.controls_listen_tile = self._load_input_prompt_asset("Tiles (White)/tile_0066.png")
        self._bg_cache: pygame.Surface | None = None
        self._bg_cache_size: tuple[int, int] = (0, 0)

        self._connect()

        # Keep lobby music running; start it here when entering lobby directly
        # (e.g. returning from a game), since prelobby won't have started it.
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            if not pygame.mixer.music.get_busy():
                lobby_file = _SOUNDS_DIR / "preolobby-lobby.mpeg"
                if lobby_file.exists():
                    pygame.mixer.music.load(str(lobby_file))
                    pygame.mixer.music.play(-1)
        except Exception:
            pass

    def _load_asset_icon(self, filename: str) -> pygame.Surface | None:
        icon_path = Path(__file__).resolve().parent / "assets" / filename
        if not icon_path.exists():
            return None
        try:
            return pygame.image.load(str(icon_path)).convert_alpha()
        except pygame.error:
            return None

    def _load_input_prompt_asset(self, relative_path: str) -> pygame.Surface | None:
        asset_path = Path(__file__).resolve().parent / "assets" / "input_prompts" / relative_path
        if not asset_path.exists():
            return None
        try:
            return pygame.image.load(str(asset_path)).convert_alpha()
        except pygame.error:
            return None

    def _draw_skin_wardrobe_icon(self, surf: pygame.Surface, center: tuple[int, int]) -> None:
        """Draw a snake + clothing badge icon for skin selection."""
        cx, cy = center
        if self.skin_icon is not None:
            icon = pygame.transform.smoothscale(self.skin_icon, (44, 44))
            shadow = icon.copy()
            shadow.fill((0, 0, 0, 85), special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(shadow, (cx - 21, cy - 21))
            surf.blit(icon, icon.get_rect(center=(cx, cy - 1)))
        else:
            pygame.draw.arc(surf, (42, 182, 98), pygame.Rect(cx - 13, cy - 12, 22, 22), 0.2, 5.7, 4)
            pygame.draw.circle(surf, (42, 182, 98), (cx + 8, cy - 2), 5)
            pygame.draw.circle(surf, (16, 26, 20), (cx + 9, cy - 3), 1)

        badge = pygame.Surface((22, 22), pygame.SRCALPHA)
        pygame.draw.circle(badge, (18, 44, 66, 235), (11, 11), 10)
        pygame.draw.circle(badge, (108, 214, 168, 220), (11, 11), 10, 2)
        pygame.draw.arc(badge, (240, 246, 255), pygame.Rect(6, 4, 10, 8), 3.2, 6.0, 1)
        pygame.draw.line(badge, (240, 246, 255), (11, 8), (11, 10), 1)
        pygame.draw.line(badge, (240, 246, 255), (7, 12), (15, 12), 1)
        shirt_pts = [(8, 12), (6, 14), (7, 16), (9, 15), (9, 18), (13, 18), (13, 15), (15, 16), (16, 14), (14, 12)]
        pygame.draw.polygon(badge, (255, 211, 94), shirt_pts)
        surf.blit(badge, (cx + 10, cy + 8))

    def _prepare_icon_transparent_bg(self, icon: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
        """Scale icon and clear matte backgrounds (white/gray/flat corner color)."""
        out = pygame.transform.smoothscale(icon, size).convert_alpha()
        w, h = out.get_size()
        if w <= 0 or h <= 0:
            return out

        corner_samples = [
            out.get_at((0, 0)),
            out.get_at((w - 1, 0)),
            out.get_at((0, h - 1)),
            out.get_at((w - 1, h - 1)),
        ]

        def is_similar(a: pygame.Color, b: pygame.Color, tol: int = 58) -> bool:
            return abs(int(a.r) - int(b.r)) + abs(int(a.g) - int(b.g)) + abs(int(a.b) - int(b.b)) <= tol

        # Remove connected matte from the icon boundaries.
        stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
        seen: set[tuple[int, int]] = set()
        while stack:
            x, y = stack.pop()
            if (x, y) in seen or x < 0 or y < 0 or x >= w or y >= h:
                continue
            seen.add((x, y))
            px = out.get_at((x, y))
            if px.a == 0:
                continue
            if any(is_similar(px, sample) for sample in corner_samples):
                out.set_at((x, y), pygame.Color(px.r, px.g, px.b, 0))
                stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])

        # Extra cleanup for common white/gray matte leftovers.
        for y in range(h):
            for x in range(w):
                r, g, b, a = out.get_at((x, y))
                if a == 0:
                    continue
                if (r >= 235 and g >= 235 and b >= 235) or (abs(r - g) < 10 and abs(g - b) < 10 and r >= 210):
                    out.set_at((x, y), pygame.Color(r, g, b, 0))
        return out

    def _append_log(self, text: str) -> None:
        self.logs.append(text)
        if len(self.logs) > 16:
            self.logs = self.logs[-16:]

    def _append_chat_bubble(self, sender: str, message: str) -> None:
        is_self = sender.casefold() == self.username.casefold()
        self.chat_bubbles.append((sender, message, is_self))
        if len(self.chat_bubbles) > 40:
            self.chat_bubbles = self.chat_bubbles[-40:]

    def _remember_private_tab(self, username: str) -> None:
        if not username or username.casefold() == self.username.casefold():
            return
        if username in self.private_chat_tabs:
            self.private_chat_tabs.remove(username)
        self.private_chat_tabs.append(username)
        if len(self.private_chat_tabs) > 10:
            self.private_chat_tabs = self.private_chat_tabs[-10:]

    def _append_private_chat_bubble(self, partner: str, sender: str, message: str) -> None:
        if not partner or partner.casefold() == self.username.casefold():
            return
        is_self = sender.casefold() == self.username.casefold()
        thread = self.private_chat_threads.setdefault(partner, [])
        thread.append((sender, message, is_self))
        if len(thread) > 40:
            self.private_chat_threads[partner] = thread[-40:]
        self._remember_private_tab(partner)
        if self.active_private_target is None:
            self.active_private_target = partner

    def _set_private_target(self, username: str | None) -> None:
        if username is None:
            return
        cleaned = username.strip()
        if not cleaned or cleaned.casefold() == self.username.casefold():
            return
        self._remember_private_tab(cleaned)
        self.active_private_target = cleaned

    def _is_chat_enabled(self) -> bool:
        if self.chat_mode == "public":
            return True
        return self.active_private_target is not None

    def _current_chat_bubbles(self) -> list[tuple[str, str, bool]]:
        if self.chat_mode == "private" and self.active_private_target is not None:
            return self.private_chat_threads.get(self.active_private_target, [])
        return self.chat_bubbles

    # Feature: LOB_01_USERNAME_SUBMISSION (send username + skin + chat endpoint).
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
            self.connection.send_message(
                make_username_message(
                    self.username,
                    skin_to_dict(self.current_skin),
                    chat_host=self.connection.chat_host,
                    chat_port=self.connection.chat_port,
                )
            )
            self._append_log(f"[SYSTEM] Username submitted: {self.username}")
        except OSError as error:
            self._append_log(f"[ERROR] Username submit failed: {error}")

    # Feature: LOB_01_USERNAME_SUBMISSION (retry path when username collision happens).
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
            self.connection.send_message(
                make_username_message(
                    self.username,
                    skin_to_dict(self.current_skin),
                    chat_host=self.connection.chat_host,
                    chat_port=self.connection.chat_port,
                )
            )
            self._append_log(f"[SYSTEM] Retrying username: {self.username}")
        except OSError as error:
            self._append_log(f"[ERROR] Username retry failed: {error}")

    def _retry_quick_match_if_due(self) -> None:
        quick_label, _, _ = self._quick_action_state()
        if quick_label != "QUICK MATCH":
            self.quick_match_waiting = False
            self._next_quick_match_ping_ms = 0
            return
        if not self.quick_match_waiting:
            return
        if self.connection is None:
            self.quick_match_waiting = False
            return
        now = pygame.time.get_ticks()
        if now < self._next_quick_match_ping_ms:
            return
        self._next_quick_match_ping_ms = now + 1200
        try:
            self.connection.send_message(
                make_invitation_message(
                    from_user=self.username,
                    to_user=self.username,
                    action="quick_match",
                )
            )
        except OSError as error:
            self.quick_match_waiting = False
            self._append_log(f"[ERROR] Quick Match retry failed: {error}")

    def _disconnect(self) -> None:
        if self.connection is not None:
            if self.start_game:
                # Tell the server to keep the match alive during the
                # lobby → game_window reconnect window (8-second grace period).
                try:
                    self.connection.send_message(
                        make_invitation_message(
                            from_user=self.username,
                            to_user=self.username,
                            action="handoff",
                        )
                    )
                except OSError:
                    pass
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

    # Feature: LOB_02_ONLINE_AND_LEADERBOARD_SYNC (consumes ONLINE_USERS snapshots).
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
            new_online_users = list(payload.get("users", []))
            idle_payload = payload.get("idle_users", [])
            new_idle_users = {str(u) for u in idle_payload if isinstance(u, str)}
            active_matches_payload = payload.get("active_matches", [])
            new_active_matches: list[dict[str, Any]] = []
            if isinstance(active_matches_payload, list):
                for item in active_matches_payload:
                    if not isinstance(item, dict):
                        continue
                    gid = str(item.get("game_id", "")).strip()
                    players = item.get("players", [])
                    if not gid or not isinstance(players, list) or len(players) < 2:
                        continue
                    p1 = str(players[0]).strip()
                    p2 = str(players[1]).strip()
                    if not p1 or not p2:
                        continue
                    new_active_matches.append({"game_id": gid, "players": [p1, p2]})
            self.active_matches = new_active_matches
            try:
                new_active_players_count = int(payload.get("active_players", 0))
            except (TypeError, ValueError):
                new_active_players_count = 0

            # Debounce transient roster shrink during match start handoff so
            # leaderboard/player list does not flicker (disappear/reappear).
            current_online_cf = set(self.online_name_by_cf.keys())
            incoming_online_cf = {str(u).casefold() for u in new_online_users}
            if len(incoming_online_cf) < len(current_online_cf) and new_active_players_count >= 2:
                missing_cf = current_online_cf - incoming_online_cf
                now_ms = pygame.time.get_ticks()
                if missing_cf != self._online_shrink_candidate:
                    self._online_shrink_candidate = set(missing_cf)
                    self._online_shrink_started_ms = now_ms
                if now_ms - self._online_shrink_started_ms < 1400:
                    # Keep existing visual roster while still consuming latest
                    # idle/active counters for button-state correctness.
                    self.idle_users = new_idle_users
                    self.active_players_count = new_active_players_count
                    return
            else:
                self._online_shrink_candidate = set()
                self._online_shrink_started_ms = 0

            new_name_by_cf = {u.casefold(): u for u in new_online_users}
            previous_name_by_cf = dict(self.online_name_by_cf)
            sessions_payload = payload.get("user_sessions", {})
            wins_payload = payload.get("wins", {})
            current_session_by_cf: dict[str, int] = {}
            current_wins_by_cf: dict[str, int] = {}
            if isinstance(sessions_payload, dict):
                for user in new_online_users:
                    raw = sessions_payload.get(user, 0)
                    try:
                        current_session_by_cf[user.casefold()] = int(raw)
                    except (TypeError, ValueError):
                        current_session_by_cf[user.casefold()] = 0
            if isinstance(wins_payload, dict):
                for user in new_online_users:
                    raw_win = wins_payload.get(user, self.wins_by_user_cf.get(user.casefold(), 0))
                    try:
                        current_wins_by_cf[user.casefold()] = int(raw_win)
                    except (TypeError, ValueError):
                        current_wins_by_cf[user.casefold()] = int(self.wins_by_user_cf.get(user.casefold(), 0))

            # Keep leaderboard sticky across lobby->game handoff churn:
            # once a user appears in this session, keep their leaderboard row.

            # New joiners OR same user with new session => reset wins.
            # Tie-break order comes from server session version so all clients
            # see identical ordering, including the user who rejoined.
            for cf in new_name_by_cf:
                old_session = self.user_session_by_cf.get(cf)
                new_session = current_session_by_cf.get(cf, old_session if old_session is not None else 0)
                if cf not in previous_name_by_cf or (old_session is not None and new_session != old_session):
                    self.wins_by_user_cf[cf] = int(current_wins_by_cf.get(cf, 0))

            # Update maps for currently online users, but do not drop prior
            # leaderboard users when payload temporarily shrinks.
            for cf in new_name_by_cf:
                self.wins_by_user_cf[cf] = int(current_wins_by_cf.get(cf, self.wins_by_user_cf.get(cf, 0)))
                self.user_session_by_cf[cf] = current_session_by_cf.get(cf, self.user_session_by_cf.get(cf, 0))
                if cf not in self.join_order_cf:
                    self.join_order_cf[cf] = self.user_session_by_cf.get(cf, 0)
                self.online_name_by_cf[cf] = new_name_by_cf[cf]
            self.online_users = new_online_users
            self.idle_users = new_idle_users
            self.active_players_count = new_active_players_count
            if self._quick_action_state()[0] != "QUICK MATCH":
                self.quick_match_waiting = False
                self._next_quick_match_ping_ms = 0
            lobby_users = self._lobby_online_users()
            if self.selected_index >= len(lobby_users):
                self.selected_index = -1
            max_players_scroll = max(0, len(lobby_users) - self._players_visible_count())
            self.players_scroll = min(self.players_scroll, max_players_scroll)
            online_casefold = {u.casefold() for u in self.online_users}
            self.pending_invites = [u for u in self.pending_invites if u.casefold() in online_casefold]
            max_pending_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
            self.pending_invites_scroll = min(self.pending_invites_scroll, max_pending_scroll)
            if self._should_block_incoming_invites():
                self.pending_invites.clear()
                self.pending_invite_from = None
                self.pending_invites_scroll = 0
            return
        if msg_type == MessageType.INVITATION.value:
            action = str(payload.get("action", "")).lower()
            from_user = str(payload.get("from_user", ""))
            to_user = str(payload.get("to_user", ""))
            if action == "send" and to_user.casefold() == self.username.casefold():
                if self._should_block_incoming_invites(from_user) and self.connection is not None:
                    try:
                        self.connection.send_message(
                            make_invitation_message(
                                from_user=from_user,
                                to_user=self.username,
                                action="decline",
                            )
                        )
                    except OSError:
                        pass
                    self.pending_invites = [name for name in self.pending_invites if name.casefold() != from_user.casefold()]
                    self.pending_invite_from = self.pending_invites[0] if self.pending_invites else None
                    return
                self.pending_invite_from = from_user
                if from_user and from_user not in self.pending_invites:
                    self.pending_invites.append(from_user)
                max_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
                self.pending_invites_scroll = min(self.pending_invites_scroll, max_scroll)
                self._append_log(f"[INVITE] {from_user} invited you.")
            elif action in {"accepted", "declined", "cancelled"}:
                self.quick_match_waiting = False
                if from_user.casefold() == self.username.casefold():
                    self._remove_outgoing_pending(to_user)
                elif to_user.casefold() == self.username.casefold():
                    self._remove_outgoing_pending(from_user)
                    if from_user in self.pending_invites:
                        self.pending_invites = [name for name in self.pending_invites if name != from_user]
                        max_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
                        self.pending_invites_scroll = min(self.pending_invites_scroll, max_scroll)
                        self.pending_invite_from = self.pending_invites[0] if self.pending_invites else None
                if not (to_user.casefold() == self.username.casefold() and self._should_block_incoming_invites(from_user)):
                    self._append_log(f"[INVITE] {payload}")
            elif action == "match_started":
                if not self._match_event_targets_self(payload):
                    self._append_log("[MATCH] Ignored unrelated match_started event.")
                    return
                self.quick_match_waiting = False
                self._next_quick_match_ping_ms = 0
                game_id = payload.get("game_id", "unknown")
                self._append_log(f"[MATCH] Started game_id={game_id}")
                skins_payload = payload.get("skins")
                if isinstance(skins_payload, dict):
                    self.match_skins = {str(k): dict(v) for k, v in skins_payload.items() if isinstance(v, dict)}
                opponent = self._extract_opponent_from_match(payload)
                if opponent:
                    self._remove_outgoing_pending(opponent)
                self.pending_lobby_spectate_target = None
                self.start_game_opponent = opponent
                self.start_game_as_spectator = False
                self.start_game = True
                self.running = False
            elif action == "spectate_joined" and to_user.casefold() == self.username.casefold():
                joined_game_id = str(payload.get("game_id", "")).strip() or None
                requested_game_id = self.pending_lobby_spectate_game_id
                if requested_game_id is not None and joined_game_id is not None and joined_game_id != requested_game_id:
                    self._append_log(
                        f"[MATCH] Spectate mismatch (joined {joined_game_id}, wanted {requested_game_id}). Retrying..."
                    )
                    if self.connection is not None:
                        try:
                            self.connection.send_message(
                                make_invitation_message(
                                    from_user=self.username,
                                    to_user=self.username,
                                    action="spectate_leave",
                                )
                            )
                        except OSError:
                            pass
                        target = self.pending_lobby_spectate_target
                        if target:
                            self._start_spectate(target, game_id=requested_game_id)
                    return
                skins_payload = payload.get("skins")
                if isinstance(skins_payload, dict):
                    self.match_skins = {str(k): dict(v) for k, v in skins_payload.items() if isinstance(v, dict)}
                target = self.pending_lobby_spectate_target
                self.pending_lobby_spectate_target = None
                self.pending_lobby_spectate_game_id = None
                self.start_game_opponent = target
                self.start_game_as_spectator = True
                self.start_game = True
                self.running = False
            elif action == "spectate_left" and to_user.casefold() == self.username.casefold():
                self.pending_lobby_spectate_target = None
                self.pending_lobby_spectate_game_id = None
            elif action == "quick_cancelled" and to_user.casefold() == self.username.casefold():
                self.quick_match_waiting = False
                self._next_quick_match_ping_ms = 0
                self._append_log("[MATCH] Quick Match cancelled.")
            elif action == "waiting" and to_user.casefold() == self.username.casefold():
                self.quick_match_waiting = True
                self._next_quick_match_ping_ms = pygame.time.get_ticks() + 1200
                self._append_log("[MATCH] Quick Match: waiting for another player...")
            else:
                self._append_log(f"[INVITE] {payload}")
            return
        if msg_type == MessageType.CHAT.value:
            sender = str(payload.get("sender", "SERVER"))
            text = str(payload.get("message", ""))
            if sender.casefold() == "server" and text.startswith("Invitation sent to "):
                target = text.removeprefix("Invitation sent to ").rstrip(".").strip()
                if target:
                    if self.pending_invite_request_target and target.casefold() == self.pending_invite_request_target.casefold():
                        if target not in self.outgoing_pending_invites:
                            self.outgoing_pending_invites.append(target)
                        self.invite_status_by_user[target] = "SENT"
                        self.pending_invite_request_target = None
            scope = str(payload.get("scope", "")).lower()
            if scope == "match":
                return
            recipient = str(payload.get("recipient", "")).strip()
            if recipient:
                # Private messages we already render locally on send should not
                # be added again when server echoes them back to sender.
                if sender.casefold() == self.username.casefold():
                    return
                partner = sender if sender.casefold() != self.username.casefold() else recipient
                self._append_private_chat_bubble(partner, sender, text)
            else:
                self._append_chat_bubble(sender, text)
            self.chat_scroll = min(self.chat_scroll, self._chat_max_scroll())
            return
        if msg_type == MessageType.GAME_OVER.value:
            # Win totals are authoritative from ONLINE_USERS payload (`wins`).
            # Keep GAME_OVER side effects UI-neutral to avoid double counting.
            return
        if msg_type == MessageType.ERROR.value:
            error_message = str(payload.get("message", "Unknown error"))
            self._append_log(f"[ERROR] {error_message}")
            low_error = error_message.casefold()
            if "spectate" in low_error or "spectator" in low_error:
                self.pending_lobby_spectate_target = None
            if "quick match" in error_message.casefold() or "invitation action failed" in error_message.casefold():
                self.quick_match_waiting = False
            if "username already taken" in error_message.casefold():
                self._username_retry_count += 1
                if self._username_retry_count <= self._username_retry_max:
                    self._next_username_retry_ms = pygame.time.get_ticks() + 450
                else:
                    self._append_log("[ERROR] Username still unavailable. Try Back To PreLobby.")
            if self._last_invite_target is not None and (
                "busy" in error_message.casefold() or "offline" in error_message.casefold()
            ):
                self.invite_status_by_user[self._last_invite_target] = "BUSY"
                self._remove_outgoing_pending(self._last_invite_target)
                self.pending_invite_request_target = None
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

    def _match_event_targets_self(self, payload: dict[str, Any]) -> bool:
        me = self.username.casefold()
        from_user = str(payload.get("from_user", "")).casefold()
        to_user = str(payload.get("to_user", "")).casefold()
        if from_user == me or to_user == me:
            return True
        players = payload.get("players")
        if isinstance(players, list):
            for player in players:
                if str(player).casefold() == me:
                    return True
        return False

    def _selected_opponent(self) -> str | None:
        lobby_users = self._lobby_online_users()
        if self.selected_index < 0 or self.selected_index >= len(lobby_users):
            return None
        selected = lobby_users[self.selected_index]
        if selected.casefold() == self.username.casefold():
            return None
        return selected

    def _send_invite(self, explicit_target: str | None = None) -> None:
        if self.quick_match_waiting:
            self._append_log("[MATCH] Cancel Quick Match before sending invites.")
            return
        if self._has_outgoing_pending():
            return
        opponent = explicit_target or self._selected_opponent()
        if opponent is None:
            opponent = self.invite_name_text.strip() or None
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
            self._last_invite_target = opponent
            self.pending_invite_request_target = opponent
            self._append_log(f"[SYSTEM] Invitation sent to {opponent}")
        except OSError as error:
            self._append_log(f"[ERROR] Invite failed: {error}")

    def _remove_outgoing_pending(self, username: str) -> None:
        self.outgoing_pending_invites = [u for u in self.outgoing_pending_invites if u.casefold() != username.casefold()]
        self.invite_status_by_user.pop(username, None)
        if self.pending_invite_request_target and self.pending_invite_request_target.casefold() == username.casefold():
            self.pending_invite_request_target = None

    def _cancel_outgoing_invite(self, username: str) -> None:
        if self.connection is None:
            return
        try:
            self.connection.send_message(
                make_invitation_message(
                    from_user=self.username,
                    to_user=username,
                    action="cancel",
                )
            )
            self._append_log(f"[INVITE] Cancel requested for {username}.")
        except OSError as error:
            self._append_log(f"[ERROR] Invite cancel failed: {error}")
        self._remove_outgoing_pending(username)

    def _typed_invite_target_valid(self) -> bool:
        target = self.invite_name_text.strip()
        if not target:
            return False
        if target.casefold() == self.username.casefold():
            return False
        online_casefold = {u.casefold() for u in self.online_users}
        return target.casefold() in online_casefold

    def _has_outgoing_pending(self) -> bool:
        return len(self.outgoing_pending_invites) > 0 or self.pending_invite_request_target is not None

    def _send_chat(self) -> None:
        text = self.chat_text.strip()
        if not text or self.connection is None or not self._is_chat_enabled():
            return
        try:
            if self.chat_mode == "private" and self.active_private_target is not None:
                ok, error_text = self.connection.send_chat_message(
                    sender=self.username,
                    message=text,
                    recipient=self.active_private_target,
                    scope="lobby",
                )
                if ok:
                    self._append_private_chat_bubble(self.active_private_target, self.username, text)
                elif error_text:
                    self._append_log(f"[ERROR] {error_text}")
            else:
                ok, error_text = self.connection.send_chat_message(
                    sender=self.username,
                    message=text,
                    scope="lobby",
                )
                if ok:
                    self._append_chat_bubble(self.username, text)
                elif error_text:
                    self._append_log(f"[ERROR] {error_text}")
            self.chat_text = ""
            self.chat_cursor = 0
        except OSError as error:
            self._append_log(f"[ERROR] Chat failed: {error}")

    def _chat_panel_rect(self) -> pygame.Rect:
        center = self._logs_rect()
        top_panel = self._center_top_rect()
        chat_top = top_panel.bottom + 16
        return pygame.Rect(
            center.x,
            chat_top,
            center.width,
            max(120, center.bottom - chat_top - 56),
        )

    def _visible_pending_invites(self, max_items: int | None = None) -> list[str]:
        if max_items is None:
            max_items = self._incoming_invites_visible_count()
        start = max(0, self.pending_invites_scroll)
        return self.pending_invites[start : start + max_items]

    def _incoming_invites_visible_count(self) -> int:
        return 2

    def _chat_visible_count(self) -> int:
        chat_area = self._chat_panel_rect()
        bubble_area = pygame.Rect(chat_area.x + 8, chat_area.y + 8, chat_area.width - 16, chat_area.height - 16)
        return max(2, bubble_area.height // 42)

    def _chat_max_scroll(self) -> int:
        return max(0, len(self._current_chat_bubbles()) - self._chat_visible_count())

    def _chat_scrollbar_parts(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        chat_area = self._chat_panel_rect()
        bubble_area = pygame.Rect(chat_area.x + 8, chat_area.y + 8, chat_area.width - 16, chat_area.height - 16)
        bar = pygame.Rect(bubble_area.right - 16, bubble_area.y + 4, 14, bubble_area.height - 8)
        up = pygame.Rect(bar.x, bar.y, bar.width, 14)
        down = pygame.Rect(bar.x, bar.bottom - 14, bar.width, 14)
        track = pygame.Rect(bar.x, up.bottom + 2, bar.width, max(8, down.y - (up.bottom + 2)))
        return up, down, track

    def _chat_scroll_thumb_rect(self) -> pygame.Rect | None:
        total = len(self._current_chat_bubbles())
        visible = self._chat_visible_count()
        if total <= visible:
            return None
        _, _, track = self._chat_scrollbar_parts()
        max_scroll = max(1, total - visible)
        thumb_h = max(18, int(track.height * (visible / max(visible, total))))
        # Invert direction so moving thumb down goes toward recent messages.
        top_ratio = 1.0 - (self.chat_scroll / max_scroll)
        thumb_y = track.y + int((track.height - thumb_h) * top_ratio)
        return pygame.Rect(track.x + 3, thumb_y, track.width - 6, thumb_h)

    def _set_chat_scroll_from_thumb_y(self, thumb_y: int, thumb_h: int) -> None:
        _, _, track = self._chat_scrollbar_parts()
        travel = max(1, track.height - thumb_h)
        clamped_y = min(max(track.y, thumb_y), track.y + travel)
        ratio = (clamped_y - track.y) / travel
        self.chat_scroll = int(round((1.0 - ratio) * self._chat_max_scroll()))

    def _chat_tabs_layout(self, chat_area: pygame.Rect) -> tuple[pygame.Rect, list[tuple[str, pygame.Rect]]]:
        tab_y = chat_area.y - 27
        public_tab = pygame.Rect(chat_area.x + 8, tab_y, 98, 24)
        private_tabs: list[tuple[str, pygame.Rect]] = []
        start_x = public_tab.right + 8
        for partner in self.private_chat_tabs[-6:]:
            tab_w = min(120, max(74, self.font_tiny.size(partner)[0] + 24))
            rect = pygame.Rect(start_x, tab_y, tab_w, 24)
            private_tabs.append((partner, rect))
            start_x += tab_w + 6
        return public_tab, private_tabs

    def _chat_target_options(self) -> list[str]:
        opts = ["Lobby Chat"]
        for partner in self.private_chat_tabs[-10:]:
            opts.append(partner)
        return opts

    def _chat_target_rect(self) -> pygame.Rect:
        send = self._chat_send_rect()
        return pygame.Rect(send.x - 156, send.y, 148, send.height)

    def _chat_target_label(self) -> str:
        if self.chat_mode == "private" and self.active_private_target:
            return self.active_private_target
        return "Lobby Chat"

    def _set_chat_target(self, label: str) -> None:
        if label == "Lobby Chat":
            self.chat_mode = "public"
            self.active_private_target = None
        else:
            self._set_private_target(label)
            self.chat_mode = "private"

    def _chat_target_dropdown_rect(self) -> pygame.Rect:
        t = self._chat_target_rect()
        visible = self._chat_target_visible_count()
        return pygame.Rect(t.x, t.y - (visible * 26) - 6, t.width, visible * 26)

    def _chat_target_visible_count(self) -> int:
        return min(6, len(self._chat_target_options()))

    def _chat_target_option_rect(self, visible_index: int) -> pygame.Rect:
        dd = self._chat_target_dropdown_rect()
        return pygame.Rect(dd.x, dd.y + visible_index * 26, dd.width, 24)

    def _player_popup_rect(self) -> pygame.Rect:
        width = 280
        height = 150
        return pygame.Rect((self.w - width) // 2, (self.h - height) // 2, width, height)

    def _player_popup_invite_rect(self) -> pygame.Rect:
        popup = self._player_popup_rect()
        return pygame.Rect(popup.x + 16, popup.y + 84, 78, 32)

    def _player_popup_chat_rect(self) -> pygame.Rect:
        popup = self._player_popup_rect()
        return pygame.Rect(popup.x + 102, popup.y + 84, 78, 32)

    def _player_popup_close_rect(self) -> pygame.Rect:
        popup = self._player_popup_rect()
        return pygame.Rect(popup.x + 188, popup.y + 84, 78, 32)

    def _reply_invite(self, accept: bool, from_user: str | None = None) -> None:
        inviter = from_user or self.pending_invite_from or (self.pending_invites[0] if self.pending_invites else None)
        if inviter is None or self.connection is None:
            return
        action = "accept" if accept else "decline"
        try:
            self.connection.send_message(
                make_invitation_message(
                    from_user=inviter,
                    to_user=self.username,
                    action=action,
                )
            )
            self._append_log(f"[INVITE] You {action}ed {inviter}.")
        except OSError as error:
            self._append_log(f"[ERROR] Invite reply failed: {error}")
        self.pending_invites = [name for name in self.pending_invites if name != inviter]
        max_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
        self.pending_invites_scroll = min(self.pending_invites_scroll, max_scroll)
        self.pending_invite_from = self.pending_invites[0] if self.pending_invites else None

    def _users_rect(self) -> pygame.Rect:
        # Right top panel: leaderboard
        width = 320
        height = min(320, self.h - 300)
        return pygame.Rect(self.w - width - 32, 120, width, height)

    def _left_panel_rect(self) -> pygame.Rect:
        return pygame.Rect(32, 120, 330, self.h - 180)

    def _skin_change_button_rect(self) -> pygame.Rect:
        left = self._left_panel_rect()
        return pygame.Rect(left.right - 136, left.y + 10, 122, 28)

    def _skin_change_action_rect(self) -> pygame.Rect:
        left = self._left_panel_rect()
        return pygame.Rect(left.x + 22, left.y + 258, left.width - 44, 86)

    def _skin_prev_rect(self) -> pygame.Rect:
        left = self._left_panel_rect()
        return pygame.Rect(left.x + 20, left.y + 266, 30, 30)

    def _skin_next_rect(self) -> pygame.Rect:
        left = self._left_panel_rect()
        return pygame.Rect(left.right - 50, left.y + 266, 30, 30)

    def _skin_dot_rect(self, idx: int) -> pygame.Rect:
        left = self._left_panel_rect()
        total = len(self.skin_color_keys)
        spacing = 10
        r = 5
        total_w = total * (r * 2) + (total - 1) * spacing
        start_x = left.centerx - total_w // 2
        cx = start_x + idx * ((r * 2) + spacing) + r
        cy = left.y + 282
        return pygame.Rect(cx - r, cy - r, r * 2, r * 2)

    def _logs_rect(self) -> pygame.Rect:
        # Center column container
        left_w = 330
        right_w = 320
        x = 32 + left_w + 20
        width = max(340, self.w - (x + right_w + 52))
        return pygame.Rect(x, 120, width, self.h - 180)

    def _right_rect(self) -> pygame.Rect:
        # Right lower panel: online players/friends (swapped with invitations)
        # Collapsed state is centered in the expansion range so chains can
        # feel like they are pulling the panel open from fixed anchors.
        expanded = self._right_rect_expanded()
        collapsed_h = 56
        collapsed_y = expanded.y + max(0, (expanded.height - collapsed_h) // 2)
        t = max(0.0, min(1.0, self.players_expand_t))
        y = int(round(collapsed_y + (expanded.y - collapsed_y) * t))
        h = int(round(collapsed_h + (expanded.height - collapsed_h) * t))
        return pygame.Rect(expanded.x, y, expanded.width, h)

    def _right_rect_expanded(self) -> pygame.Rect:
        lb = self._users_rect()
        # Lower and slightly narrower so chains are clearly visible around it.
        base_w = max(250, lb.width - 56)
        x = lb.x + (lb.width - base_w) // 2
        y = lb.bottom + 52
        h = min(210, max(130, self.h - y - 72))
        return pygame.Rect(x, y, base_w, h)

    def _online_header_rect(self) -> pygame.Rect:
        right = self._right_rect()
        return pygame.Rect(right.x + 8, right.y + 8, right.width - 16, 30)

    def _players_row_height(self) -> int:
        return 38

    def _players_visible_count(self) -> int:
        list_area = self._players_list_area_rect()
        rows_h = max(0, list_area.height)
        return max(1, rows_h // self._players_row_height())

    def _players_list_area_rect(self) -> pygame.Rect:
        players = self._right_rect()
        return pygame.Rect(players.x + 10, players.y + 44, players.width - 20, max(0, players.height - 52))

    def _lobby_online_users(self) -> list[str]:
        return list(self.online_users)

    def _players_scrollbar_parts(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        area = self._players_list_area_rect()
        bar = pygame.Rect(area.right - 16, area.y + 4, 14, max(20, area.height - 8))
        up = pygame.Rect(bar.x, bar.y, bar.width, 14)
        down = pygame.Rect(bar.x, bar.bottom - 14, bar.width, 14)
        track = pygame.Rect(bar.x, up.bottom + 2, bar.width, max(8, down.y - (up.bottom + 2)))
        return up, down, track

    def _players_scroll_thumb_rect(self) -> pygame.Rect | None:
        total = len(self._lobby_online_users())
        visible = self._players_visible_count()
        if total <= visible or not self.players_expanded:
            return None
        _, _, track = self._players_scrollbar_parts()
        max_scroll = max(1, total - visible)
        thumb_h = max(18, int(track.height * (visible / max(visible, total))))
        top_ratio = self.players_scroll / max_scroll
        thumb_y = track.y + int((track.height - thumb_h) * top_ratio)
        return pygame.Rect(track.x + 3, thumb_y, track.width - 6, thumb_h)

    def _set_players_scroll_from_thumb_y(self, thumb_y: int, thumb_h: int) -> None:
        _, _, track = self._players_scrollbar_parts()
        travel = max(1, track.height - thumb_h)
        clamped_y = min(max(track.y, thumb_y), track.y + travel)
        ratio = (clamped_y - track.y) / travel
        max_scroll = max(0, len(self._lobby_online_users()) - self._players_visible_count())
        self.players_scroll = int(round(ratio * max_scroll))

    def _update_ui_animations(self) -> None:
        target = 1.0 if self.players_expanded else 0.0
        self.players_expand_t += (target - self.players_expand_t) * self.players_expand_speed
        if abs(target - self.players_expand_t) < 0.002:
            self.players_expand_t = target

    def _center_top_rect(self) -> pygame.Rect:
        # Center top panel: invitations (swapped with online players)
        logs = self._logs_rect()
        return pygame.Rect(logs.x, logs.y, logs.width, min(340, logs.height - 180))

    def _invite_button_rect(self) -> pygame.Rect:
        players = self._right_rect()
        return pygame.Rect(players.x + 10, players.bottom - 44, players.width - 20, 34)

    def _quick_match_rect(self) -> pygame.Rect:
        left_panel = self._left_panel_rect()
        w = 244
        return pygame.Rect(left_panel.centerx - (w // 2), left_panel.bottom - 154, w, 40)

    def _controls_button_rect(self) -> pygame.Rect:
        left_panel = self._left_panel_rect()
        w = 244
        return pygame.Rect(left_panel.centerx - (w // 2), left_panel.bottom - 106, w, 40)

    def _quick_match_cancel_rect(self) -> pygame.Rect:
        pop_w, pop_h = 420, 82
        pop = pygame.Rect((self.w - pop_w) // 2, self.h - pop_h - 22, pop_w, pop_h)
        return pygame.Rect(pop.right - 120, pop.centery - 16, 96, 32)

    def _disconnect_button_rect(self) -> pygame.Rect:
        # Left panel bottom action area (Quit Game behavior)
        left_panel = self._left_panel_rect()
        w = 244
        return pygame.Rect(left_panel.centerx - (w // 2), left_panel.bottom - 58, w, 40)

    def _controls_popup_rect(self) -> pygame.Rect:
        width = min(640, self.w - 100)
        height = min(440, self.h - 90)
        return pygame.Rect((self.w - width) // 2, (self.h - height) // 2, width, height)

    def _controls_actions(self) -> tuple[tuple[str, str], ...]:
        return (("up", "Up"), ("left", "Left"), ("down", "Down"), ("right", "Right"))

    def _controls_bind_rect(self, action: str) -> pygame.Rect:
        popup = self._controls_popup_rect()
        action_index = {name: idx for idx, (name, _) in enumerate(self._controls_actions())}
        idx = action_index[action]
        row_h = 58
        start_y = popup.y + 118
        return pygame.Rect(popup.x + popup.width - 252, start_y + idx * row_h, 190, 40)

    def _controls_close_button_rect(self) -> pygame.Rect:
        popup = self._controls_popup_rect()
        return pygame.Rect(popup.right - 144, popup.bottom - 58, 112, 34)

    def _controls_save_button_rect(self) -> pygame.Rect:
        popup = self._controls_popup_rect()
        return pygame.Rect(popup.x + 32, popup.bottom - 58, 148, 34)

    def _controls_reset_button_rect(self) -> pygame.Rect:
        popup = self._controls_popup_rect()
        return pygame.Rect(popup.x + 194, popup.bottom - 58, 166, 34)

    def _controls_keycap_label(self, key_name: str, waiting: bool) -> str:
        if waiting:
            return "..."
        normalized = normalize_key_name(key_name)
        compact = {
            "up": "UP",
            "down": "DN",
            "left": "LT",
            "right": "RT",
            "space": "SP",
            "return": "EN",
            "escape": "ES",
            "comma": ",",
            "period": ".",
            "slash": "/",
        }
        if normalized in compact:
            return compact[normalized]
        if len(normalized) == 1:
            return normalized.upper()
        pretty = display_key_name(normalized).replace(" ", "")
        if len(pretty) <= 3:
            return pretty.upper()
        return pretty[:3].upper()

    def _show_controls_feedback(self, message: str, duration_ms: int = 1800) -> None:
        self.controls_feedback = message
        self.controls_feedback_until_ms = pygame.time.get_ticks() + duration_ms

    def _set_controls_binding(self, action: str, key_name: str) -> None:
        key_name = normalize_key_name(key_name)
        if not key_name:
            self._show_controls_feedback("Invalid key.")
            return
        if key_name == "escape":
            self._show_controls_feedback("Esc is reserved for cancel/back.")
            return
        current_key = self.controls_bindings.get(action)
        if current_key == key_name:
            self._show_controls_feedback("Key already assigned.")
            return

        conflict_action: str | None = None
        for other_action in DIRECTION_ACTIONS:
            if other_action == action:
                continue
            if self.controls_bindings.get(other_action) == key_name:
                conflict_action = other_action
                break

        if conflict_action is not None and current_key:
            self.controls_bindings[conflict_action] = current_key
            self._show_controls_feedback(
                f"Swapped {action.title()} with {conflict_action.title()}.",
                duration_ms=2200,
            )
        else:
            self._show_controls_feedback(f"{action.title()} set to {display_key_name(key_name)}.")

        self.controls_bindings[action] = key_name
        self.controls_waiting_action = None

    def _handle_controls_mouse(self, pos: tuple[int, int]) -> None:
        popup = self._controls_popup_rect()
        if self.controls_waiting_action is not None:
            if not popup.collidepoint(pos):
                self._show_controls_feedback("Press a key or Esc to cancel.")
            return

        if not popup.collidepoint(pos):
            self.controls_bindings = dict(self.controls_saved_bindings)
            self.controls_waiting_action = None
            self.controls_popup_open = False
            return

        if self._controls_close_button_rect().collidepoint(pos):
            self.controls_bindings = dict(self.controls_saved_bindings)
            self.controls_waiting_action = None
            self.controls_popup_open = False
            return
        if self._controls_save_button_rect().collidepoint(pos):
            save_direction_bindings(self.controls_bindings)
            self.controls_saved_bindings = dict(self.controls_bindings)
            self._show_controls_feedback("Controls saved.", duration_ms=1400)
            return
        if self._controls_reset_button_rect().collidepoint(pos):
            self.controls_bindings = dict(DEFAULT_DIRECTION_BINDINGS)
            self.controls_waiting_action = None
            self._show_controls_feedback("Controls reset to default.", duration_ms=2200)
            return

        for action, _label in self._controls_actions():
            if self._controls_bind_rect(action).collidepoint(pos):
                self.controls_waiting_action = action
                self._show_controls_feedback(f"Listening for {action.title()}...", duration_ms=1300)
                return

    def _invite_by_name_rect(self) -> pygame.Rect:
        invites = self._center_top_rect()
        return pygame.Rect(invites.x + 12, invites.y + 44, invites.width - 172, 34)

    def _invite_by_name_send_rect(self) -> pygame.Rect:
        invites = self._center_top_rect()
        return pygame.Rect(invites.right - 150, invites.y + 44, 138, 34)

    def _incoming_invites_area_rect(self) -> pygame.Rect:
        top = self._center_top_rect()
        # Fixed rows:
        # header/title, invite-by-name row, outgoing row, invitations title/pending row, invite list area
        area_y = top.y + 188
        return pygame.Rect(top.x + 8, area_y, top.width - 16, max(84, top.bottom - area_y - 10))

    def _incoming_invites_scrollbar_parts(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        area = self._incoming_invites_area_rect()
        bar = pygame.Rect(area.right - 18, area.y + 4, 14, area.height - 8)
        up = pygame.Rect(bar.x, bar.y, bar.width, 14)
        down = pygame.Rect(bar.x, bar.bottom - 14, bar.width, 14)
        track = pygame.Rect(bar.x, up.bottom + 2, bar.width, max(8, down.y - (up.bottom + 2)))
        return up, down, track

    def _incoming_invites_scroll_thumb_rect(self) -> pygame.Rect | None:
        if len(self.pending_invites) <= self._incoming_invites_visible_count():
            return None
        _, _, track = self._incoming_invites_scrollbar_parts()
        visible = self._incoming_invites_visible_count()
        max_scroll = max(1, len(self.pending_invites) - visible)
        thumb_h = max(18, int(track.height * (visible / max(visible, len(self.pending_invites)))))
        top_ratio = self.pending_invites_scroll / max_scroll
        thumb_y = track.y + int((track.height - thumb_h) * top_ratio)
        return pygame.Rect(track.x + 3, thumb_y, track.width - 6, thumb_h)

    def _set_pending_invites_scroll_from_thumb_y(self, thumb_y: int, thumb_h: int) -> None:
        _, _, track = self._incoming_invites_scrollbar_parts()
        travel = max(1, track.height - thumb_h)
        clamped_y = min(max(track.y, thumb_y), track.y + travel)
        ratio = (clamped_y - track.y) / travel
        max_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
        self.pending_invites_scroll = int(round(ratio * max_scroll))

    def _incoming_invite_card_rect(self, index: int) -> pygame.Rect:
        area = self._incoming_invites_area_rect()
        card_h = 66
        spacing = 8
        reserve_scrollbar = 24 if len(self.pending_invites) > self._incoming_invites_visible_count() else 8
        return pygame.Rect(area.x + 2, area.y + index * (card_h + spacing), area.width - reserve_scrollbar, card_h)

    def _player_row_rect(self, visible_index: int) -> pygame.Rect:
        area = self._players_list_area_rect()
        players_y = area.y
        row_h = self._players_row_height()
        reserve = 22 if len(self._lobby_online_users()) > self._players_visible_count() else 0
        return pygame.Rect(area.x, players_y + visible_index * row_h, area.width - reserve, row_h - 6)

    def _player_eye_rect(self, row: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(row.right - 24, row.y + 4, 18, 18)

    def _start_spectate(self, target_user: str, game_id: str | None = None) -> None:
        if not target_user or target_user.casefold() == self.username.casefold():
            return
        if self.connection is None:
            self._append_log("[ERROR] Not connected. Cannot spectate.")
            return
        if self.pending_lobby_spectate_target is not None:
            return
        try:
            self.connection.send_message(
                make_invitation_message(
                    from_user=self.username,
                    to_user=target_user,
                    action="spectate",
                    game_id=game_id,
                )
            )
            self.pending_lobby_spectate_target = target_user
            self.pending_lobby_spectate_game_id = game_id
            self._append_log(f"[MATCH] Requesting spectate for {target_user}...")
        except OSError as error:
            self._append_log(f"[ERROR] Spectate request failed: {error}")

    def _should_block_incoming_invites(self, from_user: str | None = None) -> bool:
        if from_user:
            idle_casefold = {u.casefold() for u in self.idle_users}
            if from_user.casefold() not in idle_casefold:
                return True
        quick_label, _, _ = self._quick_action_state()
        return quick_label == "SPECTATE" or self.pending_lobby_spectate_target is not None

    def _quick_action_state(self) -> tuple[str, str | None, str | None]:
        """
        Decide whether the primary left-panel action should be QUICK MATCH or SPECTATE.

        Requirement:
        - If two users are already in a live game and a third user is idle in lobby,
          show SPECTATE for that third user.
        - Otherwise keep QUICK MATCH.
        """

        idle_casefold = {u.casefold() for u in self.idle_users}
        non_idle_users = [
            user for user in self.online_users
            if user.casefold() != self.username.casefold() and user.casefold() not in idle_casefold
        ]
        online_casefold = {u.casefold() for u in self.online_users}
        self_is_idle = (
            self.username.casefold() in idle_casefold
            or self.username.casefold() not in online_casefold
        )

        # Exactly the "third player while two are in-game" scenario.
        # Rely primarily on server-reported active player count to avoid local
        # state drift causing accidental QUICK MATCH/INVITE behavior.
        if self_is_idle and self.active_players_count >= 2:
            if self.active_matches:
                # Use the most recently published running match to represent
                # the current live game in lobby quick-spectate.
                match = self.active_matches[-1]
                players = [str(p) for p in match.get("players", []) if str(p)]
                target = next((p for p in players if p.casefold() != self.username.casefold()), None)
                return "SPECTATE", target, str(match.get("game_id", "")) or None
            return "SPECTATE", None, None

        return "QUICK MATCH", None, None

    def _active_match_id_for_user(self, username: str) -> str | None:
        username_cf = username.casefold()
        for match in self.active_matches:
            players = [str(p) for p in match.get("players", []) if str(p)]
            if any(player.casefold() == username_cf for player in players):
                game_id = str(match.get("game_id", "")).strip()
                if game_id:
                    return game_id
        return None

    def _cursor_index_from_x(
        self,
        font: pygame.font.Font,
        text: str,
        text_left_x: int,
        mouse_x: int,
    ) -> int:
        rel_x = max(0, mouse_x - text_left_x)
        if not text:
            return 0
        for idx in range(len(text) + 1):
            if font.size(text[:idx])[0] >= rel_x:
                return idx
        return len(text)

    def _edit_text_field(
        self,
        *,
        text: str,
        cursor: int,
        event: pygame.event.Event,
        max_len: int,
    ) -> tuple[str, int]:
        cursor = max(0, min(cursor, len(text)))
        if event.key == pygame.K_BACKSPACE:
            if cursor > 0:
                text = text[: cursor - 1] + text[cursor:]
                cursor -= 1
            return text, cursor
        if event.key == pygame.K_DELETE:
            if cursor < len(text):
                text = text[:cursor] + text[cursor + 1 :]
            return text, cursor
        if event.key == pygame.K_LEFT:
            return text, max(0, cursor - 1)
        if event.key == pygame.K_RIGHT:
            return text, min(len(text), cursor + 1)
        if event.key == pygame.K_HOME:
            return text, 0
        if event.key == pygame.K_END:
            return text, len(text)
        if event.unicode and event.unicode.isprintable() and len(text) < max_len:
            text = text[:cursor] + event.unicode + text[cursor:]
            cursor += len(event.unicode)
            return text, cursor
        return text, cursor

    def _outgoing_pending_strip_rect(self) -> pygame.Rect:
        invites = self._center_top_rect()
        return pygame.Rect(invites.x + 12, invites.y + 96, invites.width - 24, 34)

    def _outgoing_pending_item_rects(self) -> list[tuple[str, bool, pygame.Rect, pygame.Rect]]:
        strip = self._outgoing_pending_strip_rect()
        items: list[tuple[str, bool, pygame.Rect, pygame.Rect]] = []
        display_targets = list(self.outgoing_pending_invites)
        if self.pending_invite_request_target and all(
            self.pending_invite_request_target.casefold() != u.casefold() for u in display_targets
        ):
            display_targets.append(self.pending_invite_request_target)
        x = strip.x + 8
        y = strip.y + 6
        for username in display_targets[-3:]:
            confirmed = any(username.casefold() == u.casefold() for u in self.outgoing_pending_invites)
            name_w = self.font_tiny.size(username[:14])[0]
            item_w = min(250, max(176, name_w + 126))
            max_w_here = (strip.right - 8) - x
            if max_w_here < 148:
                break
            item_w = min(item_w, max_w_here)
            item = pygame.Rect(x, y, item_w, strip.height - 12)
            cancel_rect = pygame.Rect(item.right - 62, item.y + 3, 56, item.height - 6)
            items.append((username, confirmed, item, cancel_rect))
            x += item_w + 8
        return items

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
        logs = self._logs_rect()
        return pygame.Rect(logs.x + 14, logs.bottom - 46, logs.width - 310, 34)

    def _chat_send_rect(self) -> pygame.Rect:
        chat = self._chat_rect()
        return pygame.Rect(chat.right + 164, chat.y, 120, chat.height)

    # ------------------------------------------------------------------
    # Skin customization: state helpers, modal geometry, handlers, draw.
    # ------------------------------------------------------------------

    def _send_skin_update(self) -> None:
        """Resend USERNAME with the current skin so the server updates its copy."""

        if self.connection is None:
            return
        try:
            self.connection.send_message(
                make_username_message(
                    self.username,
                    skin_to_dict(self.current_skin),
                    chat_host=self.connection.chat_host,
                    chat_port=self.connection.chat_port,
                )
            )
        except OSError as error:
            self._append_log(f"[ERROR] Skin update failed: {error}")

    def _set_skin_color(self, color_key: str) -> None:
        if color_key not in SKIN_COLORS:
            return
        self.current_skin = dataclass_replace(self.current_skin, color=color_key)
        self._send_skin_update()

    def _cycle_skin_color(self, step: int) -> None:
        keys = self.skin_color_keys
        try:
            idx = keys.index(self.current_skin.color)
        except ValueError:
            idx = 0
        self._set_skin_color(keys[(idx + step) % len(keys)])

    def _open_skin_modal(self) -> None:
        self.pending_skin = dataclass_replace(self.current_skin, pattern="solid")
        self.skin_modal_tab = "color"
        self.skin_modal_open = True

    def _close_skin_modal(self, commit: bool) -> None:
        if commit:
            self.current_skin = dataclass_replace(self.pending_skin, pattern="solid")
            self._send_skin_update()
            _save_skin_prefs(self.current_skin)
        self.skin_modal_open = False

    def _skin_modal_rect(self) -> pygame.Rect:
        w, h = 620, 460
        return pygame.Rect((self.w - w) // 2, (self.h - h) // 2, w, h)

    def _skin_modal_tab_rect(self, idx: int) -> pygame.Rect:
        modal = self._skin_modal_rect()
        tab_w = (modal.width - 40) // max(1, len(self._skin_modal_tabs()))
        x = modal.x + 20 + idx * tab_w
        return pygame.Rect(x, modal.y + 52, tab_w - 6, 30)

    def _skin_modal_tabs(self) -> list[str]:
        return ["color", "hat", "tail", "eyes"]

    def _skin_modal_active_options(self) -> list[str]:
        tab = self.skin_modal_tab
        if tab == "color":
            return list(SKIN_COLORS.keys())
        if tab == "hat":
            return list(HATS)
        if tab == "tail":
            return list(TAILS)
        if tab == "eyes":
            return list(EYE_STYLES)
        return []

    def _skin_modal_option_rect(self, idx: int) -> pygame.Rect:
        modal = self._skin_modal_rect()
        grid_x = modal.x + 20
        grid_y = modal.y + 96
        grid_w = modal.width * 60 // 100 - 20
        grid_h = modal.height - 96 - 60
        cols = 3 if self.skin_modal_tab == "color" else 2
        option_count = max(1, len(self._skin_modal_active_options()))
        rows_needed = max(1, math.ceil(option_count / cols))
        cell_w = grid_w // cols
        cell_h = max(52, min(74, grid_h // rows_needed))
        col = idx % cols
        row = idx // cols
        return pygame.Rect(grid_x + col * cell_w, grid_y + row * cell_h, cell_w - 8, cell_h - 8)

    def _skin_modal_preview_rect(self) -> pygame.Rect:
        modal = self._skin_modal_rect()
        x = modal.x + modal.width * 60 // 100 + 8
        return pygame.Rect(x, modal.y + 96, modal.right - x - 20, modal.height - 96 - 60)

    def _skin_modal_reset_rect(self) -> pygame.Rect:
        modal = self._skin_modal_rect()
        return pygame.Rect(modal.x + 20, modal.bottom - 46, 110, 30)

    def _skin_modal_close_rect(self) -> pygame.Rect:
        modal = self._skin_modal_rect()
        return pygame.Rect(modal.right - 130, modal.bottom - 46, 110, 30)

    def _handle_skin_modal_click(self, pos: tuple[int, int]) -> None:
        modal = self._skin_modal_rect()
        if not modal.collidepoint(pos):
            self._close_skin_modal(commit=True)
            return
        for i, tab in enumerate(self._skin_modal_tabs()):
            if self._skin_modal_tab_rect(i).collidepoint(pos):
                self.skin_modal_tab = tab
                return
        for idx, key in enumerate(self._skin_modal_active_options()):
            if self._skin_modal_option_rect(idx).collidepoint(pos):
                field = self.skin_modal_tab
                self.pending_skin = dataclass_replace(self.pending_skin, **{field: key})
                return
        if self._skin_modal_reset_rect().collidepoint(pos):
            self.pending_skin = SnakeSkin(pattern="solid")
            return
        if self._skin_modal_close_rect().collidepoint(pos):
            self._close_skin_modal(commit=True)
            return

    # Skin preview: draws a small S-curve snake with the skin applied.
    def _draw_skin_preview(self, surf: pygame.Surface, rect: pygame.Rect, skin: SnakeSkin) -> None:
        # Only re-render the snake when the skin actually changed (perf cache)
        cached_skin, cached_surf = self._preview_cache
        if cached_skin != skin or cached_surf is None or cached_surf.get_size() != (rect.width, rect.height):
            preview = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            preview.fill((12, 20, 32))
            pygame.draw.rect(preview, (60, 200, 148), preview.get_rect(), 2, border_radius=10)
            r = max(8, min(14, rect.height // 8))
            n = 9
            spread = rect.width - r * 4
            step = spread // max(1, n - 1)
            amp = min(int(rect.height * 0.28), 36)
            cx0 = r * 2
            cy0 = rect.height // 2 + 8
            centers: list[tuple[float, float]] = [
                (cx0 + i * step, cy0 + amp * math.sin(i * 1.0))
                for i in range(n)
            ]
            draw_segmented_snake(preview, centers, skin, r)
            label = self.font_tiny.render("PREVIEW", True, (148, 198, 228))
            preview.blit(label, (10, 8))
            self._preview_cache = (dataclass_replace(skin), preview)
            cached_surf = preview

        pygame.draw.rect(surf, (12, 20, 32), rect, border_radius=10)
        surf.blit(cached_surf, rect.topleft)

    def _draw_skin_modal(self, surf: pygame.Surface) -> None:
        overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        surf.blit(overlay, (0, 0))

        modal = self._skin_modal_rect()
        pygame.draw.rect(surf, (14, 24, 36, 245), modal, border_radius=18)
        pygame.draw.rect(surf, (72, 214, 152), modal, 2, border_radius=18)

        title = self.font_snake_panel.render("CUSTOMIZE SNAKE", True, (96, 248, 178))
        surf.blit(title, (modal.centerx - title.get_width() // 2, modal.y + 10))

        for i, tab in enumerate(self._skin_modal_tabs()):
            rect = self._skin_modal_tab_rect(i)
            active = tab == self.skin_modal_tab
            pygame.draw.rect(surf, (30, 60, 86) if active else (18, 30, 44), rect, border_radius=8)
            pygame.draw.rect(surf, (96, 214, 178) if active else (60, 110, 140), rect, 1, border_radius=8)
            label = self.font_tiny.render(tab.upper(), True, (220, 240, 248) if active else (150, 180, 200))
            surf.blit(label, label.get_rect(center=rect.center))

        options = self._skin_modal_active_options()
        current_value = getattr(self.pending_skin, self.skin_modal_tab)
        for idx, key in enumerate(options):
            rect = self._skin_modal_option_rect(idx)
            selected = key == current_value
            pygame.draw.rect(surf, (24, 38, 56) if not selected else (36, 66, 92), rect, border_radius=8)
            pygame.draw.rect(surf, (100, 226, 186) if selected else (58, 96, 126), rect, 2, border_radius=8)
            self._draw_skin_option_thumb(surf, rect, key)
            name = self.font_tiny.render(key.upper(), True, (224, 240, 248))
            surf.blit(name, (rect.x + 8, rect.bottom - 18))

        preview_rect = self._skin_modal_preview_rect()
        self._draw_skin_preview(surf, preview_rect, self.pending_skin)

        reset = self._skin_modal_reset_rect()
        close = self._skin_modal_close_rect()
        self._draw_button(reset, "RESET", accent=(236, 132, 132))
        self._draw_button(close, "APPLY", accent=(96, 216, 168), glow=True)

    def _draw_skin_option_thumb(self, surf: pygame.Surface, rect: pygame.Rect, key: str) -> None:
        tab = self.skin_modal_tab
        cx, cy = rect.centerx, rect.y + rect.height // 2 - 6
        if tab == "color":
            rgb = SKIN_COLORS[key]
            pygame.draw.circle(surf, rgb, (cx, cy), 18)
            pygame.draw.circle(surf, (18, 22, 30), (cx, cy), 18, 1)
        elif tab == "hat":
            body_clr = SKIN_COLORS.get(self.pending_skin.color, SKIN_COLORS["venom"])
            pygame.draw.circle(surf, body_clr, (cx, cy + 4), 14)
            if key != "none":
                draw_snake_hat(surf, cx, cy + 4, 14, key)
        elif tab == "tail":
            body_clr = SKIN_COLORS.get(self.pending_skin.color, SKIN_COLORS["venom"])
            pygame.draw.circle(surf, body_clr, (cx - 6, cy), 12)
            if key != "none":
                draw_snake_tail(surf, cx + 8, cy, 1.0, 0.0, key)
        elif tab == "eyes":
            body_clr = SKIN_COLORS.get(self.pending_skin.color, SKIN_COLORS["venom"])
            pygame.draw.circle(surf, body_clr, (cx, cy), 18)
            draw_snake_eyes(surf, cx, cy, 16, 1.0, 0.0, key, body_clr)

    def _handle_mouse(self, pos: tuple[int, int]) -> None:
        if self.skin_modal_open:
            self._handle_skin_modal_click(pos)
            return
        if self.controls_popup_open:
            self._handle_controls_mouse(pos)
            return
        if self.player_popup_target is not None:
            if self._player_popup_invite_rect().collidepoint(pos):
                if not self._has_outgoing_pending() and not self.quick_match_waiting:
                    self._send_invite(explicit_target=self.player_popup_target)
                self.player_popup_target = None
                return
            if self._player_popup_chat_rect().collidepoint(pos):
                self._set_private_target(self.player_popup_target)
                self.chat_mode = "private"
                self.player_popup_target = None
                return
            if self._player_popup_close_rect().collidepoint(pos):
                self.player_popup_target = None
                return
            if not self._player_popup_rect().collidepoint(pos):
                self.player_popup_target = None

        for username, _, _, cancel_rect in self._outgoing_pending_item_rects():
            if cancel_rect.collidepoint(pos):
                self._cancel_outgoing_invite(username)
                return

        if self.quick_match_waiting and self._quick_match_cancel_rect().collidepoint(pos):
            if self.connection is not None:
                try:
                    self.connection.send_message(
                        make_invitation_message(
                            from_user=self.username,
                            to_user=self.username,
                            action="quick_cancel",
                        )
                    )
                except OSError as error:
                    self._append_log(f"[ERROR] Quick Match cancel failed: {error}")
            self.quick_match_waiting = False
            self._next_quick_match_ping_ms = 0
            return

        if self._skin_change_button_rect().collidepoint(pos) or self._skin_change_action_rect().collidepoint(pos):
            self._open_skin_modal()
            return
        if self._skin_prev_rect().collidepoint(pos):
            self._cycle_skin_color(-1)
            return
        if self._skin_next_rect().collidepoint(pos):
            self._cycle_skin_color(1)
            return
        for idx, _key in enumerate(self.skin_color_keys):
            if self._skin_dot_rect(idx).collidepoint(pos):
                self._set_skin_color(self.skin_color_keys[idx])
                return

        if self._chat_max_scroll() > 0:
            up, down, _ = self._chat_scrollbar_parts()
            thumb = self._chat_scroll_thumb_rect()
            if thumb is not None and thumb.collidepoint(pos):
                self.chat_dragging = True
                self.chat_drag_offset_y = pos[1] - thumb.y
                return
            if up.collidepoint(pos):
                self.chat_scroll = min(self._chat_max_scroll(), self.chat_scroll + 1)
                return
            if down.collidepoint(pos):
                self.chat_scroll = max(0, self.chat_scroll - 1)
                return

        if self.players_expanded and len(self._lobby_online_users()) > self._players_visible_count():
            up, down, _ = self._players_scrollbar_parts()
            thumb = self._players_scroll_thumb_rect()
            if thumb is not None and thumb.collidepoint(pos):
                self.players_dragging = True
                self.players_drag_offset_y = pos[1] - thumb.y
                return
            if up.collidepoint(pos):
                self.players_scroll = max(0, self.players_scroll - 1)
                return
            if down.collidepoint(pos):
                max_scroll = max(0, len(self._lobby_online_users()) - self._players_visible_count())
                self.players_scroll = min(max_scroll, self.players_scroll + 1)
                return

        if len(self.pending_invites) > self._incoming_invites_visible_count():
            up, down, _ = self._incoming_invites_scrollbar_parts()
            thumb = self._incoming_invites_scroll_thumb_rect()
            if thumb is not None and thumb.collidepoint(pos):
                self.pending_invites_dragging = True
                self.pending_invites_drag_offset_y = pos[1] - thumb.y
                return
            if up.collidepoint(pos):
                self.pending_invites_scroll = max(0, self.pending_invites_scroll - 1)
                return
            if down.collidepoint(pos):
                max_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
                self.pending_invites_scroll = min(max_scroll, self.pending_invites_scroll + 1)
                return

        shown_invites = self._visible_pending_invites()
        for i, inviter in enumerate(shown_invites):
            card = self._incoming_invite_card_rect(i)
            accept_rect = pygame.Rect(card.right - 154, card.bottom - 28, 68, 22)
            reject_rect = pygame.Rect(card.right - 78, card.bottom - 28, 68, 22)
            if accept_rect.collidepoint(pos):
                self._reply_invite(True, from_user=inviter)
                return
            if reject_rect.collidepoint(pos):
                self._reply_invite(False, from_user=inviter)
                return

        if self._invite_by_name_send_rect().collidepoint(pos):
            if self._typed_invite_target_valid() and not self._has_outgoing_pending() and not self.quick_match_waiting:
                self._send_invite()
            return
        if self._chat_send_rect().collidepoint(pos):
            if self._is_chat_enabled():
                self._send_chat()
            return
        if self._quick_match_rect().collidepoint(pos):
            action_label, spectate_target, spectate_game_id = self._quick_action_state()
            if action_label == "SPECTATE":
                if spectate_target is None:
                    self._append_log("[MATCH] No active match available to spectate right now.")
                else:
                    self._append_log(f"[MATCH] Spectating {spectate_target}...")
                    self._start_spectate(spectate_target, game_id=spectate_game_id)
            else:
                if self.active_players_count >= 2:
                    # Safety guard: never send quick-match while lobby indicates
                    # active match occupancy.
                    self.quick_match_waiting = False
                    self._next_quick_match_ping_ms = 0
                    return
                if self.connection is not None:
                    try:
                        self.connection.send_message(
                            make_invitation_message(
                                from_user=self.username,
                                to_user=self.username,
                                action="quick_match",
                            )
                        )
                        # Immediate UX feedback; server responses can still refine/reset.
                        self.quick_match_waiting = True
                        self._next_quick_match_ping_ms = pygame.time.get_ticks() + 1200
                        self._append_log("[MATCH] Quick Match requested. Waiting for player...")
                    except OSError as error:
                        self.quick_match_waiting = False
                        self._next_quick_match_ping_ms = 0
                        self._append_log(f"[ERROR] Quick Match request failed: {error}")
                else:
                    self.quick_match_waiting = False
                    self._next_quick_match_ping_ms = 0
                    self._append_log("[ERROR] Not connected to server. Quick Match unavailable.")
            return
        if self._controls_button_rect().collidepoint(pos):
            self.controls_bindings = dict(self.controls_saved_bindings)
            self.controls_popup_open = True
            self.controls_waiting_action = None
            return
        if self._chat_target_rect().collidepoint(pos):
            self.chat_target_open = not self.chat_target_open
            return
        if self.chat_target_open:
            dd = self._chat_target_dropdown_rect()
            if dd.collidepoint(pos):
                options = self._chat_target_options()
                start = self.chat_target_scroll
                shown = options[start : start + self._chat_target_visible_count()]
                for i, opt in enumerate(shown):
                    if self._chat_target_option_rect(i).collidepoint(pos):
                        self._set_chat_target(opt)
                        self.chat_target_open = False
                        return
            self.chat_target_open = False
        if self._disconnect_button_rect().collidepoint(pos):
            self.return_to_prelobby = True
            self.running = False
            return

        chat_rect = self._chat_rect()
        self.input_focus = chat_rect.collidepoint(pos)
        self.invite_name_focus = self._invite_by_name_rect().collidepoint(pos)
        if self.input_focus:
            self.chat_cursor = self._cursor_index_from_x(self.font_small, self.chat_text, chat_rect.x + 12, pos[0])
        if self.invite_name_focus:
            invite_rect = self._invite_by_name_rect()
            self.invite_name_cursor = self._cursor_index_from_x(
                self.font_small,
                self.invite_name_text,
                invite_rect.x + 10,
                pos[0],
            )

        right = self._right_rect()
        if self._online_header_rect().collidepoint(pos):
            self.players_expanded = not self.players_expanded
            return
        if self.players_expand_t > 0.85 and right.collidepoint(pos):
            lobby_users = self._lobby_online_users()
            row_h = self._players_row_height()
            visible_idx = (pos[1] - (right.y + 44)) // row_h
            idx = self.players_scroll + visible_idx
            if 0 <= idx < len(lobby_users) and 0 <= visible_idx < self._players_visible_count():
                selected = lobby_users[int(idx)]
                self.selected_index = int(idx)
                if selected.casefold() != self.username.casefold():
                    self.invite_name_text = selected
                    self.invite_name_cursor = len(self.invite_name_text)
                    self.player_popup_target = selected

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
                if self.controls_popup_open:
                    self._handle_controls_mouse(event.pos)
                    continue
                self._handle_mouse(event.pos)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.pending_invites_dragging = False
                self.players_dragging = False
                self.chat_dragging = False
            elif event.type == pygame.MOUSEMOTION and self.chat_dragging:
                thumb = self._chat_scroll_thumb_rect()
                if thumb is not None:
                    self._set_chat_scroll_from_thumb_y(
                        event.pos[1] - self.chat_drag_offset_y,
                        thumb.height,
                    )
            elif event.type == pygame.MOUSEMOTION and self.pending_invites_dragging:
                thumb = self._incoming_invites_scroll_thumb_rect()
                if thumb is not None:
                    self._set_pending_invites_scroll_from_thumb_y(
                        event.pos[1] - self.pending_invites_drag_offset_y,
                        thumb.height,
                    )
            elif event.type == pygame.MOUSEMOTION and self.players_dragging:
                thumb = self._players_scroll_thumb_rect()
                if thumb is not None:
                    self._set_players_scroll_from_thumb_y(
                        event.pos[1] - self.players_drag_offset_y,
                        thumb.height,
                    )
            elif event.type == pygame.MOUSEWHEEL:
                if self.controls_popup_open:
                    continue
                mx, my = pygame.mouse.get_pos()
                users = self._users_rect()
                right = self._right_rect()
                if users.collidepoint((mx, my)):
                    ranked = self._ranked_players()
                    max_scroll = max(0, len(ranked) - 10)
                    self.leaderboard_scroll = min(max(0, self.leaderboard_scroll - event.y), max_scroll)
                elif self.players_expand_t > 0.85 and right.collidepoint((mx, my)):
                    max_scroll = max(0, len(self._lobby_online_users()) - self._players_visible_count())
                    self.players_scroll = min(max(0, self.players_scroll - event.y), max_scroll)
                elif self._incoming_invites_area_rect().collidepoint((mx, my)):
                    max_scroll = max(0, len(self.pending_invites) - self._incoming_invites_visible_count())
                    self.pending_invites_scroll = min(max(0, self.pending_invites_scroll - event.y), max_scroll)
                elif self._chat_panel_rect().collidepoint((mx, my)):
                    self.chat_scroll = min(max(0, self.chat_scroll + event.y), self._chat_max_scroll())
                elif self._chat_target_rect().collidepoint((mx, my)) or (
                    self.chat_target_open and self._chat_target_dropdown_rect().collidepoint((mx, my))
                ):
                    options = self._chat_target_options()
                    max_scroll = max(0, len(options) - self._chat_target_visible_count())
                    self.chat_target_scroll = min(max(0, self.chat_target_scroll - event.y), max_scroll)
            elif event.type == pygame.KEYDOWN:
                if self.controls_popup_open:
                    if self.controls_waiting_action is not None:
                        if event.key == pygame.K_ESCAPE:
                            self.controls_waiting_action = None
                            self._show_controls_feedback("Rebind cancelled.", duration_ms=1200)
                            continue
                        if event.key == pygame.K_UNKNOWN:
                            continue
                        key_name = normalize_key_name(pygame.key.name(event.key))
                        self._set_controls_binding(self.controls_waiting_action, key_name)
                        continue
                    if event.key == pygame.K_ESCAPE:
                        self.controls_bindings = dict(self.controls_saved_bindings)
                        self.controls_waiting_action = None
                        self.controls_popup_open = False
                        continue
                    continue
                if event.key == pygame.K_ESCAPE:
                    if self.skin_modal_open:
                        self._close_skin_modal(commit=False)
                    else:
                        self.return_to_prelobby = True
                        self.running = False
                elif self.invite_name_focus:
                    if event.key == pygame.K_RETURN:
                        if self._typed_invite_target_valid() and not self._has_outgoing_pending():
                            self._send_invite()
                    else:
                        self.invite_name_text, self.invite_name_cursor = self._edit_text_field(
                            text=self.invite_name_text,
                            cursor=self.invite_name_cursor,
                            event=event,
                            max_len=24,
                        )
                elif self.input_focus:
                    if event.key == pygame.K_RETURN and self._is_chat_enabled():
                        self._send_chat()
                    elif self._is_chat_enabled():
                        self.chat_text, self.chat_cursor = self._edit_text_field(
                            text=self.chat_text,
                            cursor=self.chat_cursor,
                            event=event,
                            max_len=120,
                        )
                elif event.key == pygame.K_a:
                    self._reply_invite(True)
                elif event.key == pygame.K_d:
                    self._reply_invite(False)

    def _draw_button(
        self,
        rect: pygame.Rect,
        label: str,
        *,
        accent: tuple[int, int, int],
        enabled: bool = True,
        glow: bool = False,
    ) -> None:
        mouse = pygame.mouse.get_pos()
        hover = enabled and rect.collidepoint(mouse)
        pressed = hover and pygame.mouse.get_pressed()[0]
        if enabled:
            if pressed:
                fill = (14, 34, 56)
                edge = tuple(max(0, c - 40) for c in accent)  # type: ignore[assignment]
            else:
                fill = (24, 46, 72) if not hover else (34, 66, 98)
                edge = accent
            text_color = (230, 238, 248)
        else:
            fill = (38, 52, 68)
            edge = (116, 130, 146)
            text_color = (160, 172, 186)

        if glow and enabled:
            halo = rect.inflate(8, 8)
            halo_surface = pygame.Surface((halo.width, halo.height), pygame.SRCALPHA)
            pygame.draw.rect(halo_surface, (accent[0], accent[1], accent[2], 95), halo_surface.get_rect(), 2, border_radius=14)
            self.screen.blit(halo_surface, halo.topleft)
        pygame.draw.rect(self.screen, fill, rect, border_radius=14)
        pygame.draw.rect(self.screen, edge, rect, 2, border_radius=14)
        txt = self.font_small.render(label, True, text_color)
        self.screen.blit(txt, txt.get_rect(center=rect.center))

    def _ranked_players(self) -> list[tuple[str, int]]:
        ranked = []
        for cf, user in self.online_name_by_cf.items():
            ranked.append((user, int(self.wins_by_user_cf.get(cf, 0)), int(self.join_order_cf.get(cf, 10**9))))
        ranked.sort(key=lambda item: (-item[1], item[2]))
        ranked = [(user, wins) for user, wins, _order in ranked]
        return ranked

    def _build_bg_cache(self) -> pygame.Surface:
        bg = pygame.Surface((self.w, self.h))
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            c = lerp_color((5, 16, 28), (4, 12, 22), t)
            pygame.draw.line(bg, c, (0, y), (self.w, y))
        circuit = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        for y in range(20, self.h, 38):
            pygame.draw.line(circuit, (42, 112, 110, 20), (14, y), (self.w - 16, y), 1)
            pygame.draw.circle(circuit, (66, 154, 146, 26), (14, y), 1)
            pygame.draw.circle(circuit, (66, 154, 146, 26), (self.w - 16, y), 1)
        for x in range(24, self.w, 52):
            pygame.draw.line(circuit, (34, 94, 98, 18), (x, 10), (x, self.h - 10), 1)
        for x in range(44, self.w, 120):
            for y in range(52, self.h, 120):
                pygame.draw.line(circuit, (44, 126, 122, 22), (x, y), (x + 18, y), 1)
                pygame.draw.line(circuit, (44, 126, 122, 22), (x + 18, y), (x + 18, y + 14), 1)
                pygame.draw.circle(circuit, (76, 178, 166, 30), (x + 18, y + 14), 1)
        bg.blit(circuit, (0, 0))
        return bg

    def _draw(self) -> None:
        self.frame += 1
        size = (self.w, self.h)
        if self._bg_cache is None or self._bg_cache_size != size:
            self._bg_cache = self._build_bg_cache()
            self._bg_cache_size = size
        frame = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        frame.blit(self._bg_cache, (0, 0))

        panel = pygame.Rect(24, 24, self.w - 48, self.h - 48)
        pygame.draw.rect(frame, (8, 20, 34, 238), panel, border_radius=22)
        pygame.draw.rect(frame, (34, 208, 150), panel, 2, border_radius=22)

        # Snake-vibe headline with a soft glow/outline
        title = self.font_snake_title.render("PYTHON ARENA", True, (92, 246, 174))
        title_glow = self.font_snake_title.render("PYTHON ARENA", True, (28, 104, 88))
        subtitle = self.font_header_sub.render("LOBBY DEPLOYMENT", True, (206, 232, 248))
        tx = panel.centerx - title.get_width() // 2
        title_y = 28
        frame.blit(title_glow, (tx + 2, title_y + 2))
        frame.blit(title, (tx, title_y))
        sx = panel.centerx - subtitle.get_width() // 2
        subtitle_y = title_y + title.get_height() + 4
        frame.blit(subtitle, (sx, subtitle_y))
        line_y = title_y + (title.get_height() // 2) + 2
        pygame.draw.line(frame, (72, 214, 152), (tx - 120, line_y), (tx - 24, line_y), 2)
        pygame.draw.line(frame, (72, 214, 152), (tx + title.get_width() + 24, line_y), (tx + title.get_width() + 120, line_y), 2)

        lb = self._users_rect()
        center = self._logs_rect()
        players = self._right_rect()
        invites_top = self._center_top_rect()
        left_panel = self._left_panel_rect()
        def draw_cyber_panel(rect: pygame.Rect, accent: tuple[int, int, int]) -> None:
            pygame.draw.rect(frame, (10, 24, 40, 238), rect, border_radius=14)
            glow = pygame.Surface((rect.width + 20, rect.height + 20), pygame.SRCALPHA)
            pygame.draw.rect(glow, (accent[0], accent[1], accent[2], 42), glow.get_rect(), border_radius=18)
            frame.blit(glow, (rect.x - 10, rect.y - 10))
            pygame.draw.rect(frame, accent, rect, 2, border_radius=14)

        draw_cyber_panel(left_panel, (76, 128, 168))
        draw_cyber_panel(lb, (54, 232, 168))
        draw_cyber_panel(invites_top, (42, 214, 186))
        # Left panel background: glass-like faded grid + subtle internal particles
        pygame.draw.rect(frame, (7, 20, 31, 214), left_panel, border_radius=14)
        pygame.draw.rect(frame, (56, 218, 212), left_panel, 2, border_radius=14)
        pygame.draw.rect(frame, (72, 232, 160, 148), left_panel.inflate(-8, -8), 1, border_radius=12)

        # tiny green particles drifting INSIDE the panel (leaderboard-like ambience)
        inner = left_panel.inflate(-18, -18)
        particles = 30
        for i in range(particles):
            px_seed = (i * 57) % max(1, inner.width)
            px_jitter = int(6 * math.sin(self.frame * 0.013 + i * 1.9))
            px = inner.x + px_seed + px_jitter
            py = inner.y + int(((self.frame * 0.65 + i * 31) % max(1, inner.height)))
            py = inner.bottom - (py - inner.y)  # drift upward
            glow = 56 + int(38 * (0.5 + 0.5 * math.sin(self.frame * 0.05 + i)))
            pygame.draw.circle(frame, (86, 236, 164, glow), (px, py), 1)
        # Left panel: YOUR SNAKE (centered, snake-vibe title)
        ys = self.font_snake_panel.render("YOUR SNAKE", True, (156, 226, 246))
        ys_glow = self.font_snake_panel.render("YOUR SNAKE", True, (42, 92, 108))
        ys_x = left_panel.centerx - ys.get_width() // 2
        frame.blit(ys_glow, (ys_x + 1, left_panel.y + 14))
        frame.blit(ys, (ys_x, left_panel.y + 12))

        # Hologram tint: 75% skin color (brightened) + 25% cyan base
        _skin_clr = SKIN_COLORS.get(self.current_skin.color, SKIN_COLORS["venom"])
        _skin_bright = tuple(min(255, c + 80) for c in _skin_clr)
        holo_rgb = (
            int(_skin_bright[0] * 0.75 + 80  * 0.25),
            int(_skin_bright[1] * 0.75 + 190 * 0.25),
            int(_skin_bright[2] * 0.75 + 255 * 0.25),
        )
        holo_rgb_hi = tuple(min(255, c + 55) for c in holo_rgb)
        holo_cx, holo_cy = left_panel.centerx, left_panel.y + 162
        # projector platform + hologram beam (volumetric cone, no flat spot)
        platform = pygame.Rect(holo_cx - 68, holo_cy + 42, 136, 24)
        pygame.draw.ellipse(frame, (14, 34, 52), platform)
        pygame.draw.ellipse(frame, (92, 156, 214), platform, 2)
        pygame.draw.ellipse(frame, (24, 62, 94, 120), platform.inflate(-14, -10))

        beam_h = 118
        beam = pygame.Surface((140, beam_h), pygame.SRCALPHA)
        for yb in range(beam_h):
            t = yb / max(1, beam_h - 1)
            half_w = int(58 - (t * 38))
            alpha = int(34 * (1.0 - t))
            col = (holo_rgb[0], holo_rgb[1], holo_rgb[2], alpha)
            pygame.draw.line(beam, col, (70 - half_w, beam_h - 1 - yb), (70 + half_w, beam_h - 1 - yb), 1)
            if yb % 8 == 0:
                pygame.draw.line(
                    beam,
                    (holo_rgb_hi[0], holo_rgb_hi[1], holo_rgb_hi[2], int(alpha * 0.9)),
                    (70 - half_w + 4, beam_h - 1 - yb),
                    (70 + half_w - 4, beam_h - 1 - yb),
                    1,
                )
        frame.blit(beam, (holo_cx - 70, holo_cy - 64))

        # floating holo particles
        for p in range(18):
            pt = (self.frame * 1.4 + p * 19) % 120
            px = int(holo_cx + math.sin((self.frame * 0.05) + p) * (10 + (p % 6) * 4))
            py = int(holo_cy + 40 - pt)
            pa = max(18, 120 - int(pt))
            pygame.draw.circle(frame, (holo_rgb[0], holo_rgb[1], holo_rgb[2], pa), (px, py), 1)

        # holographic snake coil (semi-transparent body + scanline accents)
        seg_count = 22
        phase = self.frame * 0.028
        for i in range(seg_count):
            t = i / max(1, seg_count - 1)
            # Smoother coil motion (slower, less angular jitter)
            ang = phase + (t * 6.6)
            rad = 34 - (t * 16)
            sway = math.sin(phase + t * 5.2) * 4.2
            x = int(holo_cx + math.cos(ang) * rad + sway)
            y = int(holo_cy + math.sin(ang * 0.92) * (10 + t * 7) + t * 26 - 14)
            r = max(4, int(10 - t * 4))
            s = pygame.Surface((r * 5, r * 5), pygame.SRCALPHA)
            pygame.draw.circle(s, (holo_rgb[0], holo_rgb[1], holo_rgb[2], 24), (int(r * 2.5), int(r * 2.5)), r + 3)
            pygame.draw.circle(s, (holo_rgb[0], holo_rgb[1], holo_rgb[2], 150), (int(r * 2.5), int(r * 2.5)), r)
            # scanline slit over each segment
            if i % 2 == 0:
                pygame.draw.line(s, (245, 252, 255, 112), (int(r * 1.2), int(r * 2.5)), (int(r * 3.8), int(r * 2.5)), 1)
            frame.blit(s, (x - int(r * 2.5), y - int(r * 2.5)))

        # keep this area visually clean (no selected-label text)

        # CHANGE SKIN medallion button (reference-style, reactive)
        skin_action = self._skin_change_action_rect()
        mouse_pos = pygame.mouse.get_pos()
        hover = skin_action.collidepoint(mouse_pos)
        pressed = hover and pygame.mouse.get_pressed()[0]

        pulse = 0.5 + 0.5 * math.sin(self.frame * 0.07)
        glow_alpha = 70 + int(60 * pulse) + (28 if hover else 0)

        head = self.font_orbitron.render("CHANGE SKIN", True, (236, 246, 252))
        head_x = skin_action.centerx - head.get_width() // 2
        frame.blit(head, (head_x, skin_action.y - 30))

        # Medallion
        med_cx = skin_action.centerx
        med_cy = skin_action.y + 48
        med_r = 40 + (2 if hover else 0) - (1 if pressed else 0)
        pygame.draw.circle(frame, (28, 42, 54), (med_cx, med_cy), med_r + 8)
        pygame.draw.circle(frame, (164, 176, 188), (med_cx, med_cy), med_r + 6)
        pygame.draw.circle(frame, (112, 122, 132), (med_cx, med_cy), med_r + 3, 2)
        pygame.draw.circle(frame, (26, 34, 44), (med_cx, med_cy), med_r)

        # User-provided snake icon (holographic cyan tint)
        if self.change_skin_icon is not None:
            icon_size = int(med_r * 1.72)
            base_icon = pygame.transform.smoothscale(self.change_skin_icon, (icon_size, icon_size))
            icon = base_icon.copy()
            # tint to skin-derived hologram palette
            icon.fill((holo_rgb[0], holo_rgb[1], holo_rgb[2], 255), special_flags=pygame.BLEND_RGBA_MULT)
            icon.fill((16, 48, 80, 0), special_flags=pygame.BLEND_RGBA_ADD)
            # soft glow silhouette behind icon
            glow_icon = pygame.transform.smoothscale(icon, (icon_size + 8, icon_size + 8))
            glow_icon.fill((86, 214, 248, 84), special_flags=pygame.BLEND_RGBA_MULT)
            frame.blit(glow_icon, (med_cx - glow_icon.get_width() // 2, med_cy - glow_icon.get_height() // 2))
            frame.blit(icon, (med_cx - icon.get_width() // 2, med_cy - icon.get_height() // 2))
        else:
            serp = pygame.Surface((med_r * 2 + 6, med_r * 2 + 6), pygame.SRCALPHA)
            pts = [
                (med_r + 2, 7),
                (med_r - 7, 13),
                (med_r - 4, 19),
                (med_r + 5, 24),
                (med_r + 9, 30),
                (med_r + 1, 36),
                (med_r - 8, 42),
            ]
            pygame.draw.lines(serp, (132, 242, 252, 250), False, pts, 5)
            pygame.draw.lines(serp, (70, 186, 202, 230), False, pts, 2)
            pygame.draw.circle(serp, (152, 246, 255, 255), (med_r + 2, 7), 4)
            pygame.draw.circle(serp, (14, 34, 44, 255), (med_r + 3, 6), 1)
            frame.blit(serp, (med_cx - serp.get_width() // 2, med_cy - serp.get_height() // 2))

        # removed extra sub-copy and decorative frame per request

        # Right column visual overhaul: premium cyber leaderboard + invitations
        neon_outer = (56, 218, 212)
        neon_inner = (72, 232, 160)
        panel_bg = (8, 24, 36, 240)
        row_bg = (20, 44, 68)
        col_label = (118, 204, 212)
        value_mint = (122, 252, 184)
        gold_title = (246, 203, 112)
        invites_area = self._incoming_invites_area_rect()
        invites_title_y = invites_area.y - 44
        invites_panel = pygame.Rect(invites_top.x, invites_top.y, invites_top.width, invites_top.height)

        def draw_neon_panel(
            rect: pygame.Rect,
            dim: bool = False,
            with_brackets: bool = True,
            with_inner_line: bool = True,
        ) -> None:
            bg = (7, 20, 31, 226) if dim else panel_bg
            pygame.draw.rect(frame, bg, rect, border_radius=14)
            glow = pygame.Surface((rect.width + 24, rect.height + 24), pygame.SRCALPHA)
            glow_col = (neon_outer[0], neon_outer[1], neon_outer[2], 36 if dim else 52)
            pygame.draw.rect(glow, glow_col, glow.get_rect(), 2, border_radius=18)
            frame.blit(glow, (rect.x - 12, rect.y - 12))
            pygame.draw.rect(frame, neon_outer, rect, 2, border_radius=14)
            if with_inner_line:
                inner = rect.inflate(-8, -8)
                pygame.draw.rect(frame, (*neon_inner, 190 if dim else 230), inner, 1, border_radius=12)
            if with_brackets:
                # corner tech brackets
                br = 14
                off = 7
                for x0, y0, sx, sy in (
                    (rect.left + off, rect.top + off, 1, 1),
                    (rect.right - off, rect.top + off, -1, 1),
                    (rect.left + off, rect.bottom - off, 1, -1),
                    (rect.right - off, rect.bottom - off, -1, -1),
                ):
                    pygame.draw.line(frame, neon_inner, (x0, y0), (x0 + sx * br, y0), 2)
                    pygame.draw.line(frame, neon_inner, (x0, y0), (x0, y0 + sy * br), 2)

        draw_neon_panel(lb, dim=False, with_brackets=False)
        draw_neon_panel(invites_panel, dim=True, with_brackets=False)
        draw_neon_panel(players, dim=False, with_brackets=False, with_inner_line=False)

        # Online panel restrained by serpent-chain links (dark metallic look)
        chain_layer = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        chain_col = (44, 118, 74, 220)
        chain_hi = (86, 178, 118, 210)
        chain_shadow = (16, 44, 30, 185)

        def draw_serpent_chain_between(a: tuple[int, int], b: tuple[int, int], bend: float = 0.0) -> None:
            x0, y0 = float(a[0]), float(a[1])
            x1, y1 = float(b[0]), float(b[1])
            vx, vy = (x1 - x0), (y1 - y0)
            dist = max(1.0, math.hypot(vx, vy))
            dx, dy = (vx / dist), (vy / dist)
            ang = math.degrees(math.atan2(dy, dx))
            nx, ny = (-dy, dx)
            step = 13.0
            links = max(4, int(dist / step))
            link_w = 16
            link_h = 10
            prev: tuple[int, int] | None = None
            for i in range(links):
                t = i / max(1, links - 1)
                # slight organic wobble to feel like snake-segment chain
                wobble = math.sin((self.frame * 0.03) + i * 0.8) * 1.5
                # bow the chain so corner chains don't look like center chain
                arc = (1.0 - abs((t * 2.0) - 1.0)) * bend
                cx = int(x0 + dx * (t * dist) + nx * (wobble + arc))
                cy = int(y0 + dy * (t * dist) + ny * (wobble + arc))
                if prev is not None:
                    pygame.draw.line(chain_layer, chain_shadow, (prev[0] + 1, prev[1] + 1), (cx + 1, cy + 1), 8)
                    pygame.draw.line(chain_layer, chain_col, prev, (cx, cy), 7)
                    pygame.draw.line(chain_layer, (102, 170, 128, 180), prev, (cx, cy), 2)

                # build one snake-segment "link"
                seg = pygame.Surface((link_w + 6, link_h + 6), pygame.SRCALPHA)
                body_rect = pygame.Rect(2, 2, link_w, link_h)
                pygame.draw.ellipse(seg, chain_shadow, body_rect.move(1, 1))
                pygame.draw.ellipse(seg, chain_col, body_rect)
                pygame.draw.ellipse(seg, (26, 70, 44, 188), body_rect.inflate(-4, -4))
                # dorsal highlight
                pygame.draw.ellipse(seg, chain_hi, pygame.Rect(body_rect.x + 2, body_rect.y + 1, link_w - 7, max(3, link_h - 5)))
                # tiny scale cuts
                for s in range(3):
                    sx = body_rect.x + 3 + s * 4
                    pygame.draw.arc(seg, (20, 52, 34, 180), pygame.Rect(sx, body_rect.y + 3, 5, 4), 0.2, 2.8, 1)

                seg_rot = pygame.transform.rotate(seg, -ang)
                seg_rect = seg_rot.get_rect(center=(cx, cy))
                chain_layer.blit(seg_rot, seg_rect.topleft)
                prev = (cx, cy)

        # Exactly 6 restraint chains:
        # 3 top anchors from GLOBAL LEADERBOARD lower green border
        # 3 bottom anchors from bottom green barrier of the main frame
        top_y = lb.bottom - 2
        bottom_y = panel.bottom - 2

        top_anchors = [
            (lb.left + 8, top_y),
            (lb.centerx, top_y),
            (lb.right - 8, top_y),
        ]
        bottom_anchors = [
            (players.left - 28, bottom_y),
            (players.centerx, bottom_y),
            (panel.right - 8, bottom_y),
        ]

        top_targets = [
            (players.left + 8, players.top + 2),
            (players.centerx, players.top + 2),
            (players.right - 8, players.top + 2),
        ]
        bottom_targets = [
            (players.left + 8, players.bottom - 2),
            (players.centerx, players.bottom - 2),
            (players.right - 8, players.bottom - 2),
        ]

        draw_serpent_chain_between(top_anchors[0], top_targets[0], bend=10.0)   # top-left bends left
        draw_serpent_chain_between(top_anchors[1], top_targets[1], bend=0.0)    # top-middle straight
        draw_serpent_chain_between(top_anchors[2], top_targets[2], bend=-10.0)  # top-right bends right
        draw_serpent_chain_between(bottom_anchors[0], bottom_targets[0], bend=-10.0)  # bottom-left bends left
        draw_serpent_chain_between(bottom_anchors[1], bottom_targets[1], bend=0.0)    # bottom-middle straight
        draw_serpent_chain_between(bottom_anchors[2], bottom_targets[2], bend=10.0)   # bottom-right bends right
        frame.blit(chain_layer, (0, 0))

        # Online panel header (drawn after panel so title is always visible)
        header = self._online_header_rect()
        pygame.draw.rect(frame, (10, 30, 48), header, border_radius=10)
        pygame.draw.rect(frame, (72, 232, 160), header, 1, border_radius=10)
        online_title = self.font_body.render(f"ONLINE PLAYERS - {len(self._lobby_online_users())}", True, (82, 232, 208))
        # center the title a bit more while leaving space for the chevron
        title_x = header.x + ((header.width - 34) - online_title.get_width()) // 2 + 8
        frame.blit(online_title, (title_x, players.y + 12))
        # Fixed-position chevron: same size/anchor in both states, only inverted.
        cx = players.right - 20
        cy = players.y + 22
        w = 6
        h = 4
        if self.players_expand_t > 0.2:
            # down
            pygame.draw.line(frame, (132, 232, 176), (cx - w, cy - h), (cx, cy + h), 2)
            pygame.draw.line(frame, (132, 232, 176), (cx, cy + h), (cx + w, cy - h), 2)
        else:
            # up
            pygame.draw.line(frame, (132, 232, 176), (cx - w, cy + h), (cx, cy - h), 2)
            pygame.draw.line(frame, (132, 232, 176), (cx, cy - h), (cx + w, cy + h), 2)

        # subtle circuit lines inside right column panels
        for panel_rect, alpha in ((lb, 22), (invites_panel, 16)):
            tex = pygame.Surface((panel_rect.width, panel_rect.height), pygame.SRCALPHA)
            for y in range(16, panel_rect.height, 34):
                pygame.draw.line(tex, (44, 116, 112, alpha), (10, y), (panel_rect.width - 14, y), 1)
                pygame.draw.circle(tex, (66, 154, 146, alpha + 8), (12, y), 1)
                pygame.draw.circle(tex, (66, 154, 146, alpha + 8), (panel_rect.width - 14, y), 1)
            for x in range(18, panel_rect.width, 46):
                pygame.draw.line(tex, (38, 102, 104, alpha), (x, 8), (x, panel_rect.height - 10), 1)
            frame.blit(tex, panel_rect.topleft)

        # optional subtle sweep + particles
        sweep_y = lb.y + 32 + ((self.frame * 2) % max(1, lb.height - 56))
        pygame.draw.line(frame, (90, 230, 220, 48), (lb.x + 10, sweep_y), (lb.right - 10, sweep_y), 1)
        for i in range(5):
            px = lb.x + 22 + ((self.frame * (i + 2) + i * 53) % max(40, lb.width - 44))
            py = lb.y + 40 + ((self.frame + i * 37) % max(40, lb.height - 56))
            pygame.draw.circle(frame, (96, 236, 186, 50), (px, py), 1)

        if self.cup_icon is not None:
            cup = self._prepare_icon_transparent_bg(self.cup_icon, (22, 22))
            frame.blit(cup, (lb.x + 12, lb.y + 10))
            leaderboard_x = lb.x + 42
        else:
            leaderboard_x = lb.x + 14
            frame.blit(self.font_body.render("ðŸ†", True, gold_title), (lb.x + 12, lb.y + 10))
            leaderboard_x = lb.x + 42
        frame.blit(self.font_body.render("GLOBAL LEADERBOARD", True, gold_title), (leaderboard_x, lb.y + 12))

        frame.blit(self.font_body.render("INVITATIONS", True, (86, 214, 188)), (invites_area.x + 2, invites_title_y))
        pending_label = f"PENDING INVITES ({len(self.pending_invites)})"
        frame.blit(self.font_tiny.render(pending_label, True, (170, 202, 216)), (invites_area.x + 10, invites_title_y + 24))
        if self.pending_invites:
            badge_center = (invites_area.right - 14, invites_title_y + 12)
            pygame.draw.circle(frame, (224, 78, 92), badge_center, 8)
            badge = self.font_tiny.render(str(len(self.pending_invites)), True, (255, 255, 255))
            frame.blit(badge, badge.get_rect(center=badge_center))

        ranked = self._ranked_players()
        row_x = lb.x + 10
        player_col_x = row_x + 38
        wins_value_x = lb.right - 100
        wins_header_x = wins_value_x - 8
        frame.blit(self.font_tiny.render("PLAYER", True, col_label), (player_col_x, lb.y + 42))
        frame.blit(self.font_tiny.render("WINS", True, col_label), (wins_header_x, lb.y + 42))
        shown_ranked = ranked[self.leaderboard_scroll : self.leaderboard_scroll + 10]
        y0 = lb.y + 62
        for i, (user, wins) in enumerate(shown_ranked):
            rank_no = self.leaderboard_scroll + i + 1
            row = pygame.Rect(row_x, y0 + i * 28, lb.width - 20, 24)
            pygame.draw.rect(frame, row_bg, row, border_radius=7)
            pygame.draw.rect(frame, (66, 124, 162), row, 1, border_radius=7)
            pygame.draw.rect(frame, (94, 244, 194, 20), row.inflate(-2, -2), 1, border_radius=6)
            icon: pygame.Surface | None = None
            if rank_no == 1:
                icon = self.rank1_icon
            elif rank_no == 2:
                icon = self.rank2_icon
            elif rank_no == 3:
                icon = self.rank3_icon

            name_x = row.x + 8
            if icon is not None:
                badge = self._prepare_icon_transparent_bg(icon, (18, 18))
                frame.blit(badge, (row.x + 6, row.y + 3))
                name_x = row.x + 28
            elif rank_no <= 3:
                badge_col = (240, 184, 74) if rank_no == 1 else ((200, 212, 226) if rank_no == 2 else (198, 132, 86))
                pygame.draw.circle(frame, badge_col, (row.x + 14, row.y + 12), 8)
                pygame.draw.circle(frame, (18, 40, 62), (row.x + 14, row.y + 12), 4)
                pygame.draw.circle(frame, (255, 248, 230), (row.x + 14, row.y + 12), 2)
                name_x = row.x + 28

            frame.blit(self.font_tiny.render(user[:12], True, (230, 240, 252)), (name_x + 14, row.y + 5))
            frame.blit(self.font_tiny.render(f"{wins}", True, value_mint), (wins_value_x, row.y + 5))

        invite_name_rect = self._invite_by_name_rect()
        pygame.draw.rect(frame, (22, 42, 66), invite_name_rect, border_radius=10)
        pygame.draw.rect(
            frame,
            (72, 214, 152) if self.invite_name_focus else (72, 118, 160),
            invite_name_rect,
            2,
            border_radius=10,
        )
        invite_hint = self.invite_name_text if self.invite_name_text else "Type player name to invite..."
        invite_color = (235, 243, 252) if self.invite_name_text else (120, 146, 170)
        invite_text_surf = self.font_small.render(invite_hint, True, invite_color)
        frame.blit(invite_text_surf, (invite_name_rect.x + 10, invite_name_rect.y + 9))
        if self.invite_name_focus and (self.frame // 25) % 2 == 0:
            if self.invite_name_text:
                text_w = self.font_small.size(self.invite_name_text[: self.invite_name_cursor])[0]
                cursor_x = min(invite_name_rect.right - 10, invite_name_rect.x + 10 + text_w + 1)
            else:
                cursor_x = invite_name_rect.x + 10
            pygame.draw.line(
                frame,
                (242, 248, 255),
                (cursor_x, invite_name_rect.y + 8),
                (cursor_x, invite_name_rect.bottom - 8),
                2,
            )
        frame.blit(
            self.font_tiny.render("Send invite by name", True, (168, 190, 214)),
            (invite_name_rect.x + 2, invite_name_rect.y - 16),
        )

        pending_strip = self._outgoing_pending_strip_rect()
        pygame.draw.rect(frame, (16, 36, 58), pending_strip, border_radius=10)
        pygame.draw.rect(frame, (62, 108, 148), pending_strip, 1, border_radius=10)
        frame.blit(
            self.font_tiny.render("Outgoing invites", True, (162, 194, 222)),
            (pending_strip.x + 8, pending_strip.y - 16),
        )
        for username, confirmed, item_rect, cancel_rect in self._outgoing_pending_item_rects():
            pygame.draw.rect(frame, (24, 56, 86), item_rect, border_radius=8)
            pygame.draw.rect(frame, (84, 140, 188), item_rect, 1, border_radius=8)
            # Reserve right side for cancel button and keep status text from colliding.
            left_x = item_rect.x + 8
            status_text = "pending" if confirmed else "sending"
            status_color = (248, 206, 124) if confirmed else (166, 210, 246)
            dots_w = 3 * 7
            # Keep dots always visible by anchoring them right before Cancel.
            dot_base_x = cancel_rect.x - 8 - dots_w
            max_name_w = max(34, dot_base_x - left_x - 58)
            name_txt = username
            while self.font_tiny.size(name_txt)[0] > max_name_w and len(name_txt) > 3:
                name_txt = name_txt[:-1]
            if name_txt != username:
                if len(name_txt) >= 2:
                    name_txt = name_txt[:-2] + ".."
            frame.blit(self.font_tiny.render(name_txt, True, (232, 242, 252)), (left_x, item_rect.y + 5))
            pending_x = left_x + self.font_tiny.size(name_txt)[0] + 8
            status_w = self.font_tiny.size(status_text)[0]
            # If needed, use shorter status so dots remain visible.
            if pending_x + status_w > dot_base_x - 4:
                status_text = "pend" if confirmed else "send"
                status_w = self.font_tiny.size(status_text)[0]
            if pending_x + status_w > dot_base_x - 4:
                status_text = ""
            if status_text:
                frame.blit(self.font_tiny.render(status_text, True, status_color), (pending_x, item_rect.y + 5))
            dot_center_y = item_rect.y + item_rect.height // 2 + 1
            for idx in range(3):
                phase = (self.frame * 0.25) + (idx * 0.9)
                y_offset = int(math.sin(phase) * 2)
                pygame.draw.circle(frame, status_color, (dot_base_x + idx * 7, dot_center_y + y_offset), 2)
            pygame.draw.rect(frame, (82, 44, 50), cancel_rect, border_radius=7)
            pygame.draw.rect(frame, (236, 132, 140), cancel_rect, 1, border_radius=7)
            cancel_txt = self.font_tiny.render("Cancel", True, (250, 236, 238))
            frame.blit(cancel_txt, cancel_txt.get_rect(center=cancel_rect.center))

        pygame.draw.rect(frame, (10, 30, 48), invites_area, border_radius=10)
        pygame.draw.rect(frame, (54, 118, 146), invites_area, 1, border_radius=10)
        shown_invites = self._visible_pending_invites()
        old_clip = frame.get_clip()
        frame.set_clip(invites_area.inflate(-2, -2))
        for i, inviter in enumerate(shown_invites):
            card = self._incoming_invite_card_rect(i)
            pygame.draw.rect(frame, (18, 44, 66), card, border_radius=10)
            pygame.draw.rect(frame, (72, 138, 168), card, 1, border_radius=10)
            pygame.draw.rect(frame, (92, 214, 174, 18), card.inflate(-2, -2), 1, border_radius=9)
            frame.blit(self.font_tiny.render(f"Invite from {inviter}", True, (236, 194, 110)), (card.x + 10, card.y + 10))
            accept_rect = pygame.Rect(card.right - 154, card.bottom - 28, 68, 22)
            reject_rect = pygame.Rect(card.right - 78, card.bottom - 28, 68, 22)
            pygame.draw.rect(frame, (24, 78, 62), accept_rect, border_radius=8)
            pygame.draw.rect(frame, (84, 220, 162), accept_rect, 1, border_radius=8)
            pygame.draw.rect(frame, (86, 34, 48), reject_rect, border_radius=8)
            pygame.draw.rect(frame, (236, 122, 132), reject_rect, 1, border_radius=8)
            frame.blit(self.font_tiny.render("Accept", True, (236, 248, 240)), (accept_rect.x + 12, accept_rect.y + 4))
            frame.blit(self.font_tiny.render("Reject", True, (255, 232, 236)), (reject_rect.x + 14, reject_rect.y + 4))
        frame.set_clip(old_clip)
        if len(self.pending_invites) > self._incoming_invites_visible_count():
            up, down, track = self._incoming_invites_scrollbar_parts()
            pygame.draw.rect(frame, (22, 44, 70), up, border_radius=4)
            pygame.draw.rect(frame, (22, 44, 70), down, border_radius=4)
            pygame.draw.rect(frame, (34, 60, 90), track, border_radius=6)
            pygame.draw.polygon(
                frame,
                (118, 156, 194),
                [(up.centerx, up.y + 4), (up.x + 4, up.bottom - 4), (up.right - 4, up.bottom - 4)],
            )
            pygame.draw.polygon(
                frame,
                (118, 156, 194),
                [(down.x + 4, down.y + 4), (down.right - 4, down.y + 4), (down.centerx, down.bottom - 4)],
            )
            thumb = self._incoming_invites_scroll_thumb_rect()
            if thumb is not None:
                pygame.draw.rect(frame, (122, 156, 192), thumb, border_radius=4)

        chat_area = self._chat_panel_rect()
        pygame.draw.rect(frame, (12, 28, 46), chat_area, border_radius=10)
        pygame.draw.rect(frame, (54, 102, 146), chat_area, 1, border_radius=10)

        show_chat_scrollbar = self._chat_max_scroll() > 0
        bubble_area = pygame.Rect(
            chat_area.x + 8,
            chat_area.y + 8,
            chat_area.width - (30 if show_chat_scrollbar else 16),
            chat_area.height - 16,
        )
        bubble_y = chat_area.bottom - 8
        visible_bubbles = self._current_chat_bubbles()
        visible_count = self._chat_visible_count()
        total = len(visible_bubbles)
        start = max(0, total - visible_count - self.chat_scroll)
        end = min(total, start + visible_count)
        for sender, msg, is_self in reversed(visible_bubbles[start:end]):
            bubble_w = min(bubble_area.width - 16, max(160, self.font_small.size(msg[:64])[0] + 44))
            bubble_h = 34
            bubble_y -= bubble_h + 8
            if bubble_y < bubble_area.y + 6:
                break
            # Keep all messages left-aligned by request.
            x = bubble_area.x + 4
            bubble = pygame.Rect(x, bubble_y, bubble_w, bubble_h)
            fill = (38, 84, 116) if is_self else (26, 58, 84)
            edge = (82, 218, 162) if is_self else (84, 132, 184)
            pygame.draw.rect(frame, fill, bubble, border_radius=10)
            pygame.draw.rect(frame, edge, bubble, 1, border_radius=10)
            prefix = "You" if is_self else sender
            frame.blit(self.font_tiny.render(f"{prefix}: {msg[:70]}", True, (234, 244, 252)), (bubble.x + 10, bubble.y + 10))

        if show_chat_scrollbar:
            up, down, track = self._chat_scrollbar_parts()
            pygame.draw.rect(frame, (22, 44, 70), up, border_radius=4)
            pygame.draw.rect(frame, (22, 44, 70), down, border_radius=4)
            pygame.draw.rect(frame, (34, 60, 90), track, border_radius=6)
            pygame.draw.polygon(
                frame,
                (118, 156, 194),
                [(up.centerx, up.y + 4), (up.x + 4, up.bottom - 4), (up.right - 4, up.bottom - 4)],
            )
            pygame.draw.polygon(
                frame,
                (118, 156, 194),
                [(down.x + 4, down.y + 4), (down.right - 4, down.y + 4), (down.centerx, down.bottom - 4)],
            )
            thumb = self._chat_scroll_thumb_rect()
            if thumb is not None:
                pygame.draw.rect(frame, (122, 156, 192), thumb, border_radius=4)

        if self.players_expand_t > 0.25:
            lobby_users = self._lobby_online_users()
            visible_players = self._players_visible_count()
            shown_players = lobby_users[self.players_scroll : self.players_scroll + visible_players]
            for i, user in enumerate(shown_players):
                idx = self.players_scroll + i
                row = self._player_row_rect(i)
                selected = idx == self.selected_index
                pygame.draw.rect(frame, (20, 52, 78), row, border_radius=8)
                pygame.draw.rect(frame, (84, 220, 176) if selected else (78, 128, 170), row, 1, border_radius=8)
                is_self = user.casefold() == self.username.casefold()
                in_match = user.casefold() not in {u.casefold() for u in self.idle_users}
                label = f"{user} (You)" if is_self else user
                short_label = (label[:16] + "...") if len(label) > 16 else label
                if in_match:
                    name_color = (255, 200, 80)
                else:
                    name_color = (232, 242, 252)
                frame.blit(self.font_small.render(short_label, True, name_color), (row.x + 10, row.y + 6))
            if len(lobby_users) > visible_players:
                up, down, track = self._players_scrollbar_parts()
                pygame.draw.rect(frame, (22, 44, 70), up, border_radius=4)
                pygame.draw.rect(frame, (22, 44, 70), down, border_radius=4)
                pygame.draw.rect(frame, (34, 60, 90), track, border_radius=6)
                pygame.draw.polygon(
                    frame,
                    (118, 156, 194),
                    [(up.centerx, up.y + 4), (up.x + 4, up.bottom - 4), (up.right - 4, up.bottom - 4)],
                )
                pygame.draw.polygon(
                    frame,
                    (118, 156, 194),
                    [(down.x + 4, down.y + 4), (down.right - 4, down.y + 4), (down.centerx, down.bottom - 4)],
                )
                pthumb = self._players_scroll_thumb_rect()
                if pthumb is not None:
                    pygame.draw.rect(frame, (122, 156, 192), pthumb, border_radius=4)

        self.screen.blit(frame, (0, 0))
        can_invite_by_name = self._typed_invite_target_valid() and not self._has_outgoing_pending() and not self.quick_match_waiting
        self._draw_button(
            self._invite_by_name_send_rect(),
            "INVITE",
            accent=(74, 210, 156),
            enabled=can_invite_by_name,
            glow=can_invite_by_name,
        )
        quick_action_label, _quick_action_target, _quick_action_game_id = self._quick_action_state()
        quick_action_accent = (72, 236, 160) if quick_action_label == "QUICK MATCH" else (92, 186, 246)
        self._draw_button(self._quick_match_rect(), quick_action_label, accent=quick_action_accent, glow=True)
        self._draw_button(self._controls_button_rect(), "CONTROLS", accent=(212, 224, 236), glow=True)
        self._draw_button(self._disconnect_button_rect(), "QUIT GAME", accent=(238, 96, 108), glow=True)
        chat_rect = self._chat_rect()
        pygame.draw.rect(self.screen, (22, 42, 66), chat_rect, border_radius=12)
        pygame.draw.rect(
            self.screen,
            (72, 214, 152) if self.input_focus and self._is_chat_enabled() else (86, 102, 120),
            chat_rect,
            2,
            border_radius=12,
        )
        if self._is_chat_enabled():
            chat_hint = self.chat_text if self.chat_text else "Type message and press Enter..."
            chat_color = (235, 243, 252) if self.chat_text else (120, 146, 170)
        else:
            chat_hint = "Private chat: select a player first."
            chat_color = (146, 122, 122)
        self.screen.blit(self.font_small.render(chat_hint, True, chat_color), (chat_rect.x + 12, chat_rect.y + 8))
        if self.input_focus and self._is_chat_enabled() and (self.frame // 25) % 2 == 0:
            text_w = self.font_small.size(self.chat_text[: self.chat_cursor])[0]
            cursor_x = min(chat_rect.right - 10, chat_rect.x + 12 + text_w + 1)
            pygame.draw.line(
                self.screen,
                (242, 248, 255),
                (cursor_x, chat_rect.y + 8),
                (cursor_x, chat_rect.bottom - 8),
                2,
            )
        target_rect = self._chat_target_rect()
        pygame.draw.rect(self.screen, (22, 42, 66), target_rect, border_radius=12)
        pygame.draw.rect(self.screen, (84, 132, 170), target_rect, 2, border_radius=12)
        target_label = self._chat_target_label()
        target_text = self.font_tiny.render(target_label[:15], True, (228, 240, 252))
        self.screen.blit(target_text, (target_rect.x + 10, target_rect.y + 9))
        pygame.draw.polygon(
            self.screen,
            (132, 232, 176),
            [
                (target_rect.right - 16, target_rect.y + 12),
                (target_rect.right - 8, target_rect.y + 12),
                (target_rect.right - 12, target_rect.y + 18),
            ],
        )
        if self.chat_target_open:
            dd = self._chat_target_dropdown_rect()
            pygame.draw.rect(self.screen, (14, 34, 54), dd, border_radius=8)
            pygame.draw.rect(self.screen, (84, 132, 170), dd, 1, border_radius=8)
            options = self._chat_target_options()
            start = self.chat_target_scroll
            shown = options[start : start + self._chat_target_visible_count()]
            for i, opt in enumerate(shown):
                r = self._chat_target_option_rect(i)
                active = opt == target_label
                pygame.draw.rect(self.screen, (28, 70, 98) if active else (20, 46, 70), r, border_radius=6)
                pygame.draw.rect(self.screen, (84, 212, 164) if active else (74, 114, 154), r, 1, border_radius=6)
                self.screen.blit(self.font_tiny.render(opt[:16], True, (232, 244, 252)), (r.x + 8, r.y + 6))
        self._draw_button(self._chat_send_rect(), "ENTER", accent=(72, 214, 152), enabled=self._is_chat_enabled(), glow=self._is_chat_enabled())

        if self.quick_match_waiting and not self.start_game:
            pop_w, pop_h = 420, 82
            pop = pygame.Rect((self.w - pop_w) // 2, self.h - pop_h - 22, pop_w, pop_h)
            pygame.draw.rect(self.screen, (14, 34, 54), pop, border_radius=14)
            pygame.draw.rect(self.screen, (86, 224, 168), pop, 2, border_radius=14)
            dots = [".", "..", "..."]
            dot_index = (self.frame // 20) % 3
            msg = f"Waiting for other player{dots[dot_index]}"
            txt = self.font_small.render(msg, True, (230, 246, 238))
            self.screen.blit(txt, txt.get_rect(center=(pop.centerx - 40, pop.centery)))
            self._draw_button(self._quick_match_cancel_rect(), "CANCEL", accent=(236, 112, 124), glow=True)

        if self.player_popup_target is not None:
            popup = self._player_popup_rect()
            shade = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 92))
            self.screen.blit(shade, (0, 0))
            pygame.draw.rect(self.screen, (18, 42, 66), popup, border_radius=12)
            pygame.draw.rect(self.screen, (92, 170, 220), popup, 2, border_radius=12)
            self.screen.blit(
                self.font_body.render(f"Player: {self.player_popup_target}", True, (238, 246, 255)),
                (popup.x + 16, popup.y + 16),
            )
            self.screen.blit(
                self.font_tiny.render("Choose action", True, (160, 192, 220)),
                (popup.x + 16, popup.y + 48),
            )
            can_popup_invite = not self._has_outgoing_pending() and not self.quick_match_waiting
            self._draw_button(
                self._player_popup_invite_rect(),
                "Invite",
                accent=(74, 210, 156),
                enabled=can_popup_invite,
                glow=can_popup_invite,
            )
            self._draw_button(self._player_popup_chat_rect(), "Chat", accent=(92, 186, 246))
            self._draw_button(self._player_popup_close_rect(), "Close", accent=(236, 132, 132))
        if self.skin_modal_open:
            self._draw_skin_modal(self.screen)

        if self.controls_popup_open:
            shade = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 152))
            self.screen.blit(shade, (0, 0))
            popup = self._controls_popup_rect()
            pygame.draw.rect(self.screen, (20, 40, 64), popup, border_radius=14)
            pygame.draw.rect(self.screen, (236, 246, 255), popup, 2, border_radius=14)

            title = self.font_body.render("CONTROLS", True, (242, 250, 255))
            self.screen.blit(title, (popup.x + 24, popup.y + 20))
            subtitle = self.font_tiny.render(
                "Click a bind, then press a key. Esc cancels rebinding.",
                True,
                (186, 210, 228),
            )
            self.screen.blit(subtitle, (popup.x + 24, popup.y + 52))

            for action, label in self._controls_actions():
                bind_rect = self._controls_bind_rect(action)
                label_surf = self.font_body.render(label, True, (230, 242, 252))
                self.screen.blit(label_surf, (popup.x + 36, bind_rect.y + 8))

                waiting = self.controls_waiting_action == action
                fill = (26, 52, 82) if not waiting else (56, 88, 124)
                edge = (182, 206, 226) if not waiting else (255, 244, 170)
                pygame.draw.rect(self.screen, fill, bind_rect, border_radius=10)
                pygame.draw.rect(self.screen, edge, bind_rect, 2, border_radius=10)
                keycap_label = self._controls_keycap_label(self.controls_bindings.get(action, action), waiting)
                keycap_w = 42 if len(keycap_label) <= 2 else 50
                keycap_h = 28
                keycap = pygame.Rect(0, 0, keycap_w, keycap_h)
                keycap.center = bind_rect.center
                pygame.draw.rect(self.screen, (18, 40, 62), keycap, border_radius=7)
                pygame.draw.rect(
                    self.screen,
                    (194, 216, 234) if not waiting else (255, 244, 170),
                    keycap,
                    2,
                    border_radius=7,
                )
                keycap_text = self.font_tiny.render(
                    keycap_label,
                    True,
                    (238, 246, 255) if not waiting else (255, 248, 196),
                )
                self.screen.blit(keycap_text, keycap_text.get_rect(center=keycap.center))

            now = pygame.time.get_ticks()
            if self.controls_feedback and now <= self.controls_feedback_until_ms:
                feedback = self.font_tiny.render(self.controls_feedback, True, (255, 236, 188))
                self.screen.blit(feedback, (popup.x + 24, popup.bottom - 84))

            self._draw_button(self._controls_save_button_rect(), "SAVE/APPLY", accent=(104, 224, 172), glow=True)
            self._draw_button(self._controls_reset_button_rect(), "RESET DEFAULT", accent=(236, 194, 112), glow=True)
            self._draw_button(self._controls_close_button_rect(), "CLOSE", accent=(232, 132, 132), glow=True)
        pygame.display.flip()

    def run(self) -> tuple[bool, bool, bool, str | None, dict[str, dict[str, str]], bool]:
        while self.running:
            self._events()
            self._drain_queue()
            self._retry_username_if_due()
            self._retry_quick_match_if_due()
            self._update_ui_animations()
            self._draw()
            self.clock.tick(FPS)
        self._disconnect()
        return (
            self.return_to_prelobby,
            self.quit_app,
            self.start_game,
            self.start_game_opponent,
            self.match_skins,
            self.start_game_as_spectator,
        )


# Features: PRE_05_DEPLOY_BUTTON + LOB_01_USERNAME_SUBMISSION handoff.
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
        back_to_prelobby, quit_app, start_game, opponent, match_skins, start_as_spectator = lobby.run()
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
                spectator_mode=start_as_spectator,
                return_to_tk_lobby=False,
                keep_window_open_on_return=True,
                initial_skin=skin_to_dict(lobby.current_skin),
                initial_match_skins=match_skins,
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




