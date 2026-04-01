"""
PBI 1.7 - Frontend/client UI helpers.

These helpers keep print formatting in one place, so we can later replace
console output with a richer UI without changing client flow logic.
"""

from __future__ import annotations

from typing import Any


def show_system(message: str) -> None:
    """Display a system-level message to the player."""

    print(f"[SYSTEM] {message}")


def show_incoming(message: dict[str, Any]) -> None:
    """Pretty-print one incoming protocol message."""

    msg_type = message.get("type", "unknown")
    payload = message.get("payload", {})
    print(f"[INCOMING] type={msg_type} payload={payload}")
