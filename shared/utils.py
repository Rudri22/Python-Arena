"""
Small shared utilities for sending and receiving protocol messages.

These helpers are intentionally lightweight so both the client and server
can reuse them without pulling in extra dependencies.
"""

from __future__ import annotations

from typing import Any

from shared.protocol import parse_message, serialize_message


# // Feature: Socket Framing Utilities
# // Purpose: Encode one protocol message as newline-delimited JSON bytes.
# // Trigger: Called by the socket framing utilities flow when this helper is needed.
def encode_message_for_socket(message: dict[str, Any]) -> bytes:
    """
    Encode one protocol message as newline-delimited JSON bytes.

    Using a newline as the message separator keeps socket handling simple:
    every complete line is one complete protocol message. TCP is a stream,
    not a message queue, so we add this small delimiter ourselves.
    """

    return f"{serialize_message(message)}\n".encode("utf-8")


# // Feature: Socket Framing Utilities
# // Purpose: Extract all complete messages from a text buffer.
# // Trigger: Called by the socket framing utilities flow when this helper is needed.
def split_socket_buffer(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """
    Extract all complete messages from a text buffer.

    Socket reads do not always align perfectly with message boundaries.
    One `recv` can contain half a JSON message or several JSON messages at
    once, so we keep the unfinished tail and parse only complete lines.
    """

    complete_messages: list[dict[str, Any]] = []
    lines = buffer.split("\n")

    # Everything except the last chunk ended with "\n", so it is safe to
    # parse. The final chunk stays in the buffer until more bytes arrive.
    for line in lines[:-1]:
        line = line.strip()
        if line:
            complete_messages.append(parse_message(line))

    remainder = lines[-1]
    return complete_messages, remainder
