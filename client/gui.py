"""
GUI client for Python-Arena Sprint 1 + Sprint 2 flows.

This Tkinter app covers:
- username validation
- connect by IP/port
- online players list
- waiting state when no opponents are available
- opponent selection and invitation send
- incoming invitation notice
- basic chat send/receive
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from client.network import ClientConnection
from client.ui import validate_username
from shared.protocol import (
    MessageType,
    make_chat_message,
    make_invitation_message,
    make_username_message,
)


class ArenaGuiApp:
    """Main Tkinter application for the Python-Arena frontend GUI."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Python-Arena Lobby")
        self.root.geometry("900x620")
        self.root.minsize(820, 560)

        # Runtime networking state.
        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.running = False
        self.username = ""
        self.online_users: list[str] = []
        self.incoming_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        self._build_layout()
        self.root.after(100, self._drain_incoming_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        """Build all UI widgets for login, lobby, and chat."""

        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)

        # Top connection bar.
        top = ttk.Frame(self.root, padding=10)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        for idx in range(8):
            top.columnconfigure(idx, weight=1 if idx in {1, 3, 5} else 0)

        ttk.Label(top, text="Server IP").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.server_ip_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(top, textvariable=self.server_ip_var, width=16).grid(row=0, column=1, sticky="ew", padx=(0, 12))

        ttk.Label(top, text="Port").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.server_port_var = tk.StringVar(value="5000")
        ttk.Entry(top, textvariable=self.server_port_var, width=8).grid(row=0, column=3, sticky="ew", padx=(0, 12))

        ttk.Label(top, text="Username").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.username_var = tk.StringVar(value="Player")
        ttk.Entry(top, textvariable=self.username_var, width=16).grid(row=0, column=5, sticky="ew", padx=(0, 12))

        self.connect_button = ttk.Button(top, text="Connect", command=self._connect)
        self.connect_button.grid(row=0, column=6, padx=(0, 8))

        self.disconnect_button = ttk.Button(top, text="Disconnect", command=self._disconnect, state="disabled")
        self.disconnect_button.grid(row=0, column=7)

        # Left panel (online users + actions).
        left = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Online Players", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")

        self.players_listbox = tk.Listbox(left, height=18, exportselection=False)
        self.players_listbox.grid(row=1, column=0, sticky="nsew", pady=(6, 8))

        actions = ttk.Frame(left)
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        self.refresh_button = ttk.Button(actions, text="Refresh View", command=self._render_online_players, state="disabled")
        self.refresh_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.invite_button = ttk.Button(actions, text="Invite Selected", command=self._invite_selected_player, state="disabled")
        self.invite_button.grid(row=0, column=1, sticky="ew")

        # Waiting state card.
        self.waiting_var = tk.StringVar(value="Not connected.")
        waiting = ttk.LabelFrame(left, text="Waiting State", padding=10)
        waiting.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(waiting, textvariable=self.waiting_var, wraplength=240, justify="left").grid(row=0, column=0, sticky="w")

        # Right panel (logs + chat).
        right = ttk.Frame(self.root, padding=(0, 0, 10, 10))
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(right, text="Lobby Events", padding=8)
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        chat_bar = ttk.Frame(right, padding=(0, 10, 0, 0))
        chat_bar.grid(row=1, column=0, sticky="ew")
        chat_bar.columnconfigure(0, weight=1)

        self.chat_var = tk.StringVar()
        self.chat_entry = ttk.Entry(chat_bar, textvariable=self.chat_var)
        self.chat_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.chat_entry.bind("<Return>", lambda _event: self._send_chat())

        self.send_button = ttk.Button(chat_bar, text="Send Chat", command=self._send_chat, state="disabled")
        self.send_button.grid(row=0, column=1)

    def _append_log(self, text: str) -> None:
        """Append one line to the event log area."""

        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_connected_ui(self, connected: bool) -> None:
        """Enable/disable controls based on connection state."""

        self.connect_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        self.send_button.configure(state="normal" if connected else "disabled")
        self.invite_button.configure(state="normal" if connected else "disabled")
        self.refresh_button.configure(state="normal" if connected else "disabled")

    def _connect(self) -> None:
        """Validate input, connect to server, and start receive loop."""

        server_ip = self.server_ip_var.get().strip()
        username = self.username_var.get().strip()
        port_raw = self.server_port_var.get().strip()

        if not port_raw.isdigit():
            messagebox.showerror("Invalid Port", "Port must be a number.")
            return
        server_port = int(port_raw)

        is_valid, validation_message = validate_username(username)
        if not is_valid:
            messagebox.showerror("Invalid Username", validation_message)
            return

        try:
            self.connection = ClientConnection(server_ip=server_ip, server_port=server_port)
        except Exception as error:
            messagebox.showerror("Connection Failed", str(error))
            return

        self.running = True
        self.username = username
        self._set_connected_ui(True)
        self._append_log(f"[SYSTEM] Connected to {server_ip}:{server_port}")

        # Start socket receiver in background so GUI thread remains responsive.
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receiver_thread.start()

        # Send username registration after receiving connect ack.
        # We push this action into queue to keep ordering deterministic.
        self.incoming_queue.put(("register_username", username))

    def _disconnect(self) -> None:
        """Disconnect client and reset GUI to idle state."""

        self.running = False
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None

        self.online_users = []
        self._render_online_players()
        self.waiting_var.set("Not connected.")
        self._set_connected_ui(False)
        self._append_log("[SYSTEM] Disconnected.")

    def _receiver_loop(self) -> None:
        """Continuously receive messages from server in background thread."""

        while self.running and self.connection is not None:
            try:
                message = self.connection.receive_message()
            except Exception as error:
                self.incoming_queue.put(("socket_error", str(error)))
                break
            self.incoming_queue.put(("server_message", message))

    def _drain_incoming_queue(self) -> None:
        """Handle cross-thread events inside Tk main thread."""

        while True:
            try:
                event_type, payload = self.incoming_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "register_username":
                # Send username once connection is up.
                if self.connection is not None:
                    self.connection.send_message(make_username_message(payload))
                    self._append_log(f"[SYSTEM] Username submitted: {payload}")
                continue

            if event_type == "socket_error":
                if self.running:
                    self._append_log(f"[SYSTEM] Connection closed: {payload}")
                    self._disconnect()
                continue

            if event_type == "server_message":
                self._handle_server_message(payload)

        self.root.after(100, self._drain_incoming_queue)

    def _handle_server_message(self, message: dict[str, Any]) -> None:
        """Route incoming protocol messages to GUI updates."""

        msg_type = message.get("type")
        payload = message.get("payload", {})

        if msg_type == MessageType.CONNECT.value:
            self._append_log(f"[CONNECT] {payload}")
            return

        if msg_type == MessageType.ONLINE_USERS.value:
            self.online_users = payload.get("users", [])
            self._render_online_players()

            has_opponent = any(user != self.username for user in self.online_users)
            if has_opponent:
                self.waiting_var.set("Opponent available. Select a player and click 'Invite Selected'.")
            else:
                self.waiting_var.set("No opponent available. Waiting for another player to join.")
            return

        if msg_type == MessageType.INVITATION.value:
            from_user = payload.get("from_user", "Unknown")
            self._append_log(f"[INVITATION] {from_user} invited you to play.")
            messagebox.showinfo("Invitation Received", f"{from_user} wants to play with you.")
            return

        if msg_type == MessageType.CHAT.value:
            sender = payload.get("sender", "SERVER")
            text = payload.get("message", "")
            self._append_log(f"[CHAT] {sender}: {text}")
            return

        if msg_type == MessageType.ERROR.value:
            self._append_log(f"[ERROR] {payload.get('message', 'Unknown error')} - {payload.get('details', '')}")
            return

        self._append_log(f"[INCOMING] {message}")

    def _render_online_players(self) -> None:
        """Render latest online users into listbox."""

        self.players_listbox.delete(0, "end")
        for user in self.online_users:
            label = f"{user} (You)" if user == self.username else user
            self.players_listbox.insert("end", label)

    def _selected_opponent(self) -> str | None:
        """Return selected opponent username from listbox, excluding self."""

        selection = self.players_listbox.curselection()
        if not selection:
            return None

        selected_label = self.players_listbox.get(selection[0])
        selected_user = selected_label.replace(" (You)", "")

        if selected_user == self.username:
            return None
        return selected_user

    def _invite_selected_player(self) -> None:
        """Send invitation to selected player from online list."""

        if self.connection is None:
            return

        opponent = self._selected_opponent()
        if opponent is None:
            messagebox.showwarning("Select Opponent", "Select another player from the online list.")
            return

        invite = make_invitation_message(
            from_user=self.username,
            to_user=opponent,
            action="send",
        )
        self.connection.send_message(invite)
        self._append_log(f"[SYSTEM] Invitation requested for {opponent}.")

    def _send_chat(self) -> None:
        """Send one chat message from input field."""

        if self.connection is None:
            return

        text = self.chat_var.get().strip()
        if not text:
            return

        self.connection.send_message(make_chat_message(sender=self.username, message=text))
        self.chat_var.set("")

    def _on_close(self) -> None:
        """Window close callback for clean socket shutdown."""

        self._disconnect()
        self.root.destroy()

    def run(self) -> None:
        """Start Tk main loop."""

        self.root.mainloop()


def main() -> None:
    """Module entrypoint for launching GUI client."""

    app = ArenaGuiApp()
    app.run()


if __name__ == "__main__":
    main()
