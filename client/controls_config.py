from __future__ import annotations

import json
from pathlib import Path

# Canonical movement actions used across lobby + gameplay.
DIRECTION_ACTIONS: tuple[str, ...] = ("up", "left", "down", "right")

# Default controls requested for project behavior.
DEFAULT_DIRECTION_BINDINGS: dict[str, str] = {
    "up": "up",
    "left": "left",
    "down": "down",
    "right": "right",
}

_SETTINGS_PATH = Path(__file__).resolve().parent / "assets" / "controls_settings.json"

# Common alias normalization so UI, pygame key names, and persisted values stay aligned.
_ALIASES: dict[str, str] = {
    "esc": "escape",
    "return": "enter",
    "kp_enter": "enter",
    "spacebar": "space",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "del": "delete",
    "pgup": "page up",
    "pgdn": "page down",
}


def normalize_key_name(raw: str) -> str:
    key = str(raw or "").strip().lower().replace("_", " ")
    if not key:
        return ""
    key = _ALIASES.get(key, key)
    # Keep single printable characters as-is.
    if len(key) == 1:
        return key
    return " ".join(part for part in key.split() if part)


def display_key_name(key_name: str) -> str:
    key = normalize_key_name(key_name)
    if not key:
        return "-"
    pretty_map = {
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "space": "Space",
        "enter": "Enter",
        "escape": "Esc",
        "backspace": "Backspace",
        "delete": "Delete",
        "page up": "PgUp",
        "page down": "PgDn",
    }
    if key in pretty_map:
        return pretty_map[key]
    if len(key) == 1:
        return key.upper()
    return key.title()


def _sanitize_bindings(candidate: dict[str, str] | None) -> dict[str, str]:
    bindings = dict(DEFAULT_DIRECTION_BINDINGS)
    if not isinstance(candidate, dict):
        return bindings

    for action in DIRECTION_ACTIONS:
        value = normalize_key_name(candidate.get(action, bindings[action]))
        if value:
            bindings[action] = value

    # Ensure each movement action has a unique key.
    seen: set[str] = set()
    for action in DIRECTION_ACTIONS:
        key = bindings[action]
        if key in seen:
            bindings[action] = DEFAULT_DIRECTION_BINDINGS[action]
        seen.add(bindings[action])
    return bindings


def load_direction_bindings() -> dict[str, str]:
    try:
        if not _SETTINGS_PATH.exists():
            return dict(DEFAULT_DIRECTION_BINDINGS)
        payload = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        return _sanitize_bindings(payload.get("direction_bindings"))
    except Exception:
        return dict(DEFAULT_DIRECTION_BINDINGS)


def save_direction_bindings(bindings: dict[str, str]) -> None:
    sanitized = _sanitize_bindings(bindings)
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"direction_bindings": sanitized}
    _SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

