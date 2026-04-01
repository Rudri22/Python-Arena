"""
PBI 1.7 + PBI 2.8 + PBI 2.9 + PBI 2.10 + PBI 2.11 + PBI 2.12 - Frontend/client UI helpers.

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


def show_online_players(users: list[str]) -> None:
    """
    PBI 2.10: Display a formatted online players list on frontend.

    Keeping this in UI helpers keeps client flow logic simple and readable.
    """

    print("\n" + "-" * 40)
    print("Online Players")
    print("-" * 40)

    if not users:
        print("No players online.")
    else:
        for index, username in enumerate(users, start=1):
            print(f"{index}. {username}")

    print("-" * 40)


def prompt_opponent_selection(users: list[str], current_username: str) -> str | None:
    """
    PBI 2.11: Let user choose another online player to play against.

    Returns selected opponent username, or `None` when no valid target
    is available / user cancels.
    """

    selectable_users = [name for name in users if name != current_username]
    if not selectable_users:
        show_system("No other players are online to invite right now.")
        return None

    print("\n" + "=" * 50)
    print("Select Opponent")
    print("=" * 50)
    for index, username in enumerate(selectable_users, start=1):
        print(f"{index}. {username}")
    print("Type number to invite, or 'c' to cancel.")
    print("=" * 50)

    while True:
        choice = input("Opponent> ").strip().lower()
        if choice == "c":
            return None

        if not choice.isdigit():
            show_system("Enter a valid number or 'c'.")
            continue

        index = int(choice)
        if not 1 <= index <= len(selectable_users):
            show_system("Selection out of range.")
            continue

        return selectable_users[index - 1]


def show_invitation_notice(from_user: str) -> None:
    """Display incoming invitation notification on frontend."""

    print("\n" + "*" * 50)
    print(f"Invitation: {from_user} wants to play with you.")
    print("*" * 50)


def show_waiting_for_opponent_screen(current_username: str) -> None:
    """
    PBI 2.12: Display a waiting screen when no opponent is available.

    This gives a clear UX state instead of a plain one-line error.
    """

    print("\n" + "~" * 50)
    print("Waiting For Opponent")
    print("~" * 50)
    print(f"Player: {current_username}")
    print("No other players are online right now.")
    print("Tips:")
    print("- Ask another player to join the server.")
    print("- Use /players to refresh the online list view.")
    print("- Use /play again when someone appears.")
    print("~" * 50)


def validate_username(username: str) -> tuple[bool, str]:
    """
    PBI 2.9: Validate username and return a user-facing result message.

    Returning `(is_valid, message)` keeps validation reusable for both
    interactive screen input and command-line `--username` mode.
    """

    if not (2 <= len(username) <= 16):
        return False, "Username must be between 2 and 16 characters."

    if not all(ch.isalnum() or ch in {"_", "-"} for ch in username):
        return False, "Username can only contain letters, numbers, '_' or '-'."

    return True, f"Username '{username}' is valid."


def show_username_validation_result(is_valid: bool, message: str) -> None:
    """Display validation result clearly on the frontend terminal UI."""

    prefix = "[USERNAME][OK]" if is_valid else "[USERNAME][INVALID]"
    print(f"{prefix} {message}")


def prompt_username(default_username: str = "Player") -> str:
    """
    PBI 2.8: Render a simple username input screen in the terminal.

    The function keeps asking until a valid username is entered, so the
    client always continues with a non-empty, safe display name.
    """

    # Light text "screen" so startup looks intentional and user-friendly.
    print("\n" + "=" * 50)
    print("                PYTHON-ARENA LOGIN")
    print("=" * 50)
    print("Enter your username to continue.")
    print("Rules: 2-16 chars, letters/numbers/_/- only.")
    print(f"Press Enter to use default: {default_username}")
    print("=" * 50)

    while True:
        raw = input("Username> ").strip()
        username = raw or default_username

        is_valid, result_message = validate_username(username)
        show_username_validation_result(is_valid, result_message)

        if not is_valid:
            continue

        return username
