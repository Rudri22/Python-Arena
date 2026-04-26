"""
PBI 1.7 + PBI 1.8 - Frontend/client network helpers.

This module centralizes socket communication so `client.py` stays focused
on application flow (parse args, connect, send messages, and read replies).
"""

from __future__ import annotations

import queue
import socket
import threading
from typing import Any

from shared.protocol import MessageType
from shared.utils import encode_message_for_socket, split_socket_buffer


# // Feature: Networking / P2P Chat
# // Purpose: Best-effort local IP detection used for P2P endpoint advertisement.
# // Trigger: Called by the networking / p2p chat flow when this helper is needed.
def _detect_local_ip(server_ip: str) -> str:
    """Best-effort local IP detection used for P2P endpoint advertisement."""

    # The server only coordinates the chat directory; private chat itself is
    # direct client-to-client TCP. This probes the network stack for the LAN IP
    # other clients can actually dial instead of blindly advertising localhost.
    loopback_ip = ""
    probe_targets: list[tuple[str, int]] = [
        (str(server_ip).strip(), 9),
        ("8.8.8.8", 80),
        ("1.1.1.1", 80),
    ]
    for host, port in probe_targets:
        if not host:
            continue
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((host, port))
            ip = str(probe.getsockname()[0]).strip()
            if ip and not ip.startswith("127."):
                return ip
            if ip:
                loopback_ip = ip
        except OSError:
            pass
        finally:
            probe.close()
    try:
        host_ip = str(socket.gethostbyname(socket.gethostname())).strip()
        if host_ip and not host_ip.startswith("127."):
            return host_ip
    except OSError:
        pass
    return loopback_ip or "127.0.0.1"


class _P2PChatNode:
    """Lightweight peer-chat transport over direct TCP connections.

    Each client opens a tiny listener socket on a random free port. The server
    shares those endpoints in ONLINE_USERS, then chat messages are sent straight
    from one client socket to the other without server relaying.
    """

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'init' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def __init__(self, advertise_host: str) -> None:
        self.advertise_host = advertise_host
        self.username = ""
        self._peers: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._running = True

        # Bind to every local interface and let the OS pick an unused port.
        # The chosen port is advertised during USERNAME_SUBMISSION.
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("0.0.0.0", 0))
        self._listener.listen(32)
        self._listener.settimeout(0.5)
        self.port = int(self._listener.getsockname()[1])

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'set username' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def set_username(self, username: str) -> None:
        with self._lock:
            self.username = str(username or "").strip()

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'set peer directory' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def set_peer_directory(self, peers: dict[str, Any]) -> None:
        # ONLINE_USERS may contain every connected player, including ourselves.
        # Keep only reachable peer endpoints so send_chat can be a simple lookup.
        parsed: dict[str, tuple[str, int]] = {}
        with self._lock:
            me_cf = self.username.casefold()
        for name_raw, endpoint_raw in peers.items():
            name = str(name_raw).strip()
            if not name or name.casefold() == me_cf or not isinstance(endpoint_raw, dict):
                continue
            host = str(endpoint_raw.get("host", "")).strip()
            try:
                port = int(endpoint_raw.get("port", 0))
            except (TypeError, ValueError):
                continue
            if host and 1 <= port <= 65535:
                parsed[name] = (host, port)
        with self._lock:
            self._peers = parsed

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'pop message' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def pop_message(self) -> dict[str, Any] | None:
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            return None

    # // Feature: Networking / P2P Chat
    # // Purpose: Sends chat to synchronize this feature across client/server.
    # // Trigger: Triggered when this system needs to open, close, or send network/audio data.
    def send_chat(
        self,
        *,
        sender: str,
        message: str,
        recipient: str | None = None,
        scope: str = "lobby",
        game_id: str | None = None,
    ) -> tuple[bool, str | None]:
        payload: dict[str, Any] = {
            "sender": str(sender),
            "message": str(message),
            "scope": str(scope),
        }
        recipient_text = str(recipient or "").strip()
        if recipient_text:
            payload["recipient"] = recipient_text
        if game_id:
            payload["game_id"] = str(game_id)
        outbound = {"type": MessageType.CHAT.value, "payload": payload}
        raw = encode_message_for_socket(outbound)

        with self._lock:
            peers = dict(self._peers)
            my_name_cf = self.username.casefold()

        targets: list[tuple[str, tuple[str, int]]] = []
        if recipient_text:
            # Private tab: dial exactly one peer using the endpoint advertised
            # through the server's online-user snapshot.
            target = next((n for n in peers if n.casefold() == recipient_text.casefold()), None)
            if target is None:
                return False, f"User '{recipient_text}' is not available for P2P chat."
            targets = [(target, peers[target])]
        else:
            # Lobby text is still P2P: the sender opens one short TCP connection
            # to each online peer instead of asking the server to broadcast it.
            targets = [
                (name, endpoint)
                for name, endpoint in peers.items()
                if name.casefold() != my_name_cf
            ]
            if not targets:
                return False, "No available peers for lobby chat."

        delivered = 0
        failures: list[str] = []
        for target_name, (host, port) in targets:
            try:
                # Keep P2P chat responsive: fail fast on unreachable peers so a
                # dead client does not freeze the lobby UI.
                peer_sock = socket.create_connection((host, port), timeout=0.35)
                try:
                    peer_sock.settimeout(0.35)
                    peer_sock.sendall(raw)
                finally:
                    peer_sock.close()
                delivered += 1
            except OSError:
                failures.append(target_name)

        if delivered > 0:
            return True, None
        if recipient_text:
            return False, f"Failed to deliver P2P message to '{recipient_text}'."
        if failures:
            return False, f"P2P lobby chat failed for: {', '.join(failures[:4])}"
        return False, "P2P lobby chat failed."

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'close' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def close(self) -> None:
        self._running = False
        try:
            self._listener.close()
        except OSError:
            pass

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'accept loop' step of the networking / p2p chat system.
    # // Trigger: Called continuously by the main loop/thread while the app is running.
    def _accept_loop(self) -> None:
        # This listener runs beside the normal server connection. Incoming peer
        # chats are short-lived connections, so each one can be decoded and
        # closed without affecting the main game socket.
        while self._running:
            try:
                conn, _addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._read_peer_conn, args=(conn,), daemon=True).start()

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'read peer conn' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def _read_peer_conn(self, conn: socket.socket) -> None:
        buffer = ""
        with conn:
            conn.settimeout(1.5)
            while self._running:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not data:
                    return
                buffer += data.decode("utf-8", errors="replace")
                # Peer chat uses the same newline-framed JSON format as the
                # client/server protocol, which keeps parsing consistent.
                messages, buffer = split_socket_buffer(buffer)
                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("type") != MessageType.CHAT.value:
                        continue
                    payload = msg.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    self._inbox.put(msg)


class ClientConnection:
    """
    Small wrapper around a TCP socket for protocol-based communication.

    We keep a text buffer so we can reconstruct full newline-delimited
    JSON messages even when `recv` returns partial chunks.
    """

    # // Feature: Networking / P2P Chat
    # // Purpose: Implements the 'init' step of the networking / p2p chat system.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def __init__(
        self,
        server_ip: str,
        server_port: int,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        self.server_ip = server_ip
        self.server_port = server_port

        # Main client/server channel: one long-lived TCP connection carries
        # username, lobby, invitation, movement, game-state, cheer, and emote
        # messages. The P2P chat listener below is separate.
        self.socket = socket.create_connection(
            (server_ip, server_port),
            timeout=connect_timeout_seconds,
        )
        try:
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        self._p2p_chat = _P2PChatNode(_detect_local_ip(server_ip))
        self.chat_host = self._p2p_chat.advertise_host
        self.chat_port = self._p2p_chat.port

        # Short read timeout lets us interleave server reads with peer-message
        # polling; otherwise private chat could wait behind a quiet game socket.
        self.socket.settimeout(0.2)
        self._buffer = ""
        # Keep extra decoded messages if one recv contains multiple lines.
        # This prevents message loss during bursts (online list + invitation).
        self._pending_messages: list[dict[str, Any]] = []

    # // Feature: Networking / P2P Chat
    # // Purpose: Send one shared-protocol message to the server.
    # // Trigger: Triggered when this system needs to open, close, or send network/audio data.
    def send_message(self, message: dict[str, Any]) -> None:
        """Send one shared-protocol message to the server."""

        if message.get("type") == MessageType.USERNAME.value:
            payload = message.get("payload")
            if isinstance(payload, dict):
                # Username registration doubles as P2P chat registration. The
                # server stores this endpoint and republishes it to other clients.
                if "chat_host" not in payload:
                    payload["chat_host"] = self.chat_host
                if "chat_port" not in payload:
                    payload["chat_port"] = self.chat_port
                username = str(payload.get("username", "")).strip()
                if username:
                    self._p2p_chat.set_username(username)

        self.socket.sendall(encode_message_for_socket(message))

    # // Feature: Networking / P2P Chat
    # // Purpose: Send a chat message directly to peers (not through the server).
    # // Trigger: Triggered when this system needs to open, close, or send network/audio data.
    def send_chat_message(
        self,
        *,
        sender: str,
        message: str,
        recipient: str | None = None,
        scope: str = "lobby",
        game_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Send a chat message directly to peers (not through the server)."""

        return self._p2p_chat.send_chat(
            sender=sender,
            message=message,
            recipient=recipient,
            scope=scope,
            game_id=game_id,
        )

    # // Feature: Networking / P2P Chat
    # // Purpose: Receive and return the next complete protocol message.
    # // Trigger: Triggered by incoming server/peer messages in the networking loop.
    def receive_message(self) -> dict[str, Any]:
        """
        Receive and return the next complete protocol message.

        This blocks until at least one full line-delimited JSON message is
        available from the socket stream or the P2P chat inbox.
        """

        while True:
            # Return already-decoded queued messages first.
            if self._pending_messages:
                return self._pending_messages.pop(0)

            p2p_message = self._p2p_chat.pop_message()
            if p2p_message is not None:
                # From the UI's point of view, direct peer messages look just
                # like server messages, so lobby/game screens can handle one queue.
                return p2p_message

            messages, remainder = split_socket_buffer(self._buffer)
            if messages:
                self._buffer = remainder
                for decoded in messages:
                    self._handle_online_users_for_peer_directory(decoded)
                # Keep any additional messages for next receive calls.
                self._pending_messages.extend(messages[1:])
                return messages[0]

            try:
                data = self.socket.recv(4096)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("Server closed the connection.")

            self._buffer += data.decode("utf-8", errors="replace")

    # // Feature: Networking / P2P Chat
    # // Purpose: Close the underlying socket.
    # // Trigger: Called by the networking / p2p chat flow when this helper is needed.
    def close(self) -> None:
        """Close the underlying socket."""

        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Socket may already be closed or half-closed.
            pass
        self.socket.close()
        self._p2p_chat.close()

    # // Feature: Networking / P2P Chat
    # // Purpose: Handles online users for peer directory and applies the related game action.
    # // Trigger: Triggered by user input events (keyboard/mouse) or UI interactions.
    def _handle_online_users_for_peer_directory(self, message: dict[str, Any]) -> None:
        if message.get("type") != MessageType.ONLINE_USERS.value:
            return
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return
        peers = payload.get("chat_peers")
        if isinstance(peers, dict):
            # The server is acting as a directory only. After this update, actual
            # chat delivery is direct TCP between clients.
            self._p2p_chat.set_peer_directory(peers)
