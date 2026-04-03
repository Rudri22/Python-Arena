"""
GUI client for Python-Arena Sprint 1 + Sprint 2 flows.

This Tkinter app covers:
- username validation
- connect by IP/port
- online players list
- waiting state when no opponents are available
- opponent selection and invitation send
- incoming invitation notice
- public and private chat send/receive
"""

from __future__ import annotations

import math
import queue
import random
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
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

    def __init__(
        self,
        *,
        initial_username: str | None = None,
        server_ip: str | None = None,
        server_port: int | None = None,
        auto_connect: bool = False,
        login_gate_enabled: bool = False,
    ) -> None:
        self.root = tk.Tk()
        self.root.title("Python-Arena Lobby")
        self.root.geometry("900x620")
        self.root.minsize(820, 560)
        self.root.configure(bg="#0f172a")

        # Runtime networking state.
        self.connection: ClientConnection | None = None
        self.receiver_thread: threading.Thread | None = None
        self.running = False
        self.username = ""
        self.online_users: list[str] = []
        self.username_confirmed = False
        self.incoming_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._switching_to_pygame = False
        self._retry_same_username = False
        self._username_retry_count = 0
        self._username_retry_max = 8
        self._chat_enabled_by_connection = False
        self.chat_tabs: dict[str, tk.Text] = {}
        self._login_advanced_visible = False
        self._login_music_on = True
        self._login_anim_job: str | None = None
        self._login_play_hover = False
        self._login_ping_phase = 0.0
        self._login_title_phase = 0.0
        self._login_online_count = 0
        self._login_active_count = 0
        self._login_snakes: list[dict[str, Any]] = []
        self._login_particles: list[dict[str, float | str]] = []
        self._login_emojis: list[dict[str, float | str]] = []
        self._login_craters: list[dict[str, float]] = []
        self._login_skin_menu_visible = False
        self._login_skins: list[dict[str, str]] = [
            {"name": "Classic Green", "color": "#4ade80", "accent": "#22c55e"},
            {"name": "Fire Red", "color": "#f87171", "accent": "#ef4444"},
            {"name": "Ocean Blue", "color": "#60a5fa", "accent": "#3b82f6"},
            {"name": "Royal Purple", "color": "#c084fc", "accent": "#a855f7"},
            {"name": "Golden", "color": "#fbbf24", "accent": "#f59e0b"},
            {"name": "Neon Pink", "color": "#f472b6", "accent": "#ec4899"},
        ]
        self._selected_skin_index = 0
        self.login_gate_enabled = login_gate_enabled

        self._configure_login_styles()
        self._build_layout()
        if server_ip:
            self.server_ip_var.set(server_ip)
        if server_port is not None:
            self.server_port_var.set(str(server_port))
        if initial_username:
            self.username_var.set(initial_username)
        self._on_username_changed()
        if self.login_gate_enabled:
            self._init_login_scene()
            self._start_login_animation()
            self._show_login_gate()
        else:
            self._show_lobby()
        if auto_connect and initial_username:
            self.root.after(200, self._connect)
        self.root.after(100, self._drain_incoming_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_login_styles(self) -> None:
        """Configure a clean esports-like style for the login gate."""

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Esports.Card.TLabelframe", background="#111827", borderwidth=0, relief="flat")
        style.configure("Esports.Card.TLabelframe.Label", background="#111827", foreground="#93c5fd", font=("Segoe UI", 9, "bold"))
        style.configure("Esports.Title.TLabel", background="#111827", foreground="#f8fafc", font=("Segoe UI Semibold", 24, "bold"))
        style.configure("Esports.Subtitle.TLabel", background="#111827", foreground="#94a3b8", font=("Segoe UI", 10))
        style.configure("Esports.Field.TLabel", background="#111827", foreground="#cbd5e1", font=("Segoe UI", 10, "bold"))
        style.configure("Esports.Status.TLabel", background="#111827", foreground="#7dd3fc", font=("Segoe UI", 9))
        style.configure(
            "Esports.Primary.TButton",
            font=("Segoe UI Semibold", 10, "bold"),
            padding=(12, 8),
            background="#0ea5e9",
            foreground="#0b1120",
            borderwidth=0,
        )
        style.map(
            "Esports.Primary.TButton",
            background=[("pressed", "#0284c7"), ("active", "#38bdf8"), ("disabled", "#334155")],
            foreground=[("disabled", "#94a3b8")],
        )
        style.configure(
            "Esports.Secondary.TButton",
            font=("Segoe UI", 9),
            padding=(10, 6),
            background="#1e293b",
            foreground="#cbd5e1",
            borderwidth=0,
        )
        style.map(
            "Esports.Secondary.TButton",
            background=[("pressed", "#0f172a"), ("active", "#334155"), ("disabled", "#1f2937")],
            foreground=[("disabled", "#64748b")],
        )

    def _draw_login_background(self, event: tk.Event | None = None) -> None:
        """Draw a soft hex-like patterned background for the pre-lobby gate."""

        canvas = self.login_bg
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        canvas.delete("bg")

        # Gradient-ish horizontal stripes.
        stripe_colors = ["#9cd7df", "#95d2db", "#8ecdd7", "#86c7d2", "#8ecdd7", "#95d2db"]
        stripe_h = max(24, height // len(stripe_colors))
        y = 0
        for color in stripe_colors:
            canvas.create_rectangle(0, y, width, y + stripe_h, fill=color, outline="", tags="bg")
            y += stripe_h

        # Hex-dot motif.
        step_x = 64
        step_y = 54
        radius = 17
        for row, y0 in enumerate(range(24, height + step_y, step_y)):
            offset = 0 if row % 2 == 0 else step_x // 2
            for x0 in range(offset + 24, width + step_x, step_x):
                canvas.create_polygon(
                    x0 - radius,
                    y0,
                    x0 - radius // 2,
                    y0 - radius,
                    x0 + radius // 2,
                    y0 - radius,
                    x0 + radius,
                    y0,
                    x0 + radius // 2,
                    y0 + radius,
                    x0 - radius // 2,
                    y0 + radius,
                    fill="",
                    outline="#a9dee5",
                    width=1,
                    tags="bg",
                )

        # Keep overlay elements centered/positioned on resize.
        canvas.coords(self.login_title_id, width / 2, 56)
        canvas.coords(self.login_music_id, 92, 74)
        canvas.coords(self.login_skin_id, width - 96, 74)
        canvas.coords(self.login_skin_popup_id, width - 130, 190)
        canvas.coords(self.login_left_rail_id, 120, height / 2 + 8)
        canvas.coords(self.login_card_id, width / 2 + 70, height / 2 + 12)
        canvas.coords(self.login_hint_id, width / 2, height - 34)

    def _build_layout(self) -> None:
        """Build all UI widgets for login, lobby, and chat."""

        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # Dedicated pre-lobby login gate.
        self.login_frame = tk.Frame(self.root, bg="#8ecdd7")
        self.login_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.login_frame.columnconfigure(0, weight=1)
        self.login_frame.rowconfigure(0, weight=1)

        self.login_bg = tk.Canvas(
            self.login_frame,
            bg="#8ecdd7",
            highlightthickness=0,
            bd=0,
        )
        self.login_bg.grid(row=0, column=0, sticky="nsew")

        title_label = tk.Label(
            self.login_bg,
            text="SNAKE BATTLE",
            bg="#8ecdd7",
            fg="#f8fafc",
            font=("Segoe UI", 33, "bold"),
        )
        self.login_title_id = self.login_bg.create_window(0, 0, window=title_label)
        self.login_title_label = title_label

        self.music_chip = tk.Frame(self.login_bg, bg="#2a3b56", bd=0, highlightthickness=2, highlightbackground="#4ade80")
        self.music_button = tk.Button(
            self.music_chip,
            text="♫ Music  ON",
            command=self._toggle_music,
            bg="#14532d",
            fg="#dcfce7",
            activebackground="#166534",
            activeforeground="#f0fdf4",
            relief="flat",
            width=12,
            font=("Segoe UI", 10, "bold"),
        )
        self.music_button.pack(padx=10, pady=8)
        self.music_button.bind("<Enter>", lambda _e: self._set_music_hover(True))
        self.music_button.bind("<Leave>", lambda _e: self._set_music_hover(False))
        self.login_music_id = self.login_bg.create_window(0, 0, window=self.music_chip)

        self.skin_chip = tk.Frame(self.login_bg, bg="#2a3b56", bd=0, highlightthickness=2, highlightbackground="#67e8f9")
        self.skin_button = tk.Button(
            self.skin_chip,
            text="🎨 Skins",
            command=self._toggle_skin_menu,
            bg="#0e7490",
            fg="#ecfeff",
            activebackground="#155e75",
            activeforeground="#ecfeff",
            relief="flat",
            width=10,
            font=("Segoe UI", 10, "bold"),
        )
        self.skin_button.pack(padx=10, pady=8)
        self.login_skin_id = self.login_bg.create_window(0, 0, window=self.skin_chip)

        self.skin_popup_frame = tk.Frame(self.login_bg, bg="#111827", bd=0, highlightthickness=2, highlightbackground="#334155")
        tk.Label(
            self.skin_popup_frame,
            text="Choose Your Skin",
            bg="#111827",
            fg="#e2e8f0",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, pady=(8, 6), padx=8)
        self._skin_popup_buttons: list[tk.Button] = []
        for idx, skin in enumerate(self._login_skins):
            btn = tk.Button(
                self.skin_popup_frame,
                text="🐍",
                command=lambda i=idx: self._select_skin(i),
                bg=skin["color"],
                fg="#ffffff",
                relief="flat",
                width=4,
                height=2,
                font=("Segoe UI Emoji", 11, "bold"),
            )
            btn.grid(row=1 + idx // 3, column=idx % 3, padx=6, pady=6)
            self._skin_popup_buttons.append(btn)
        self.skin_popup_label = tk.Label(
            self.skin_popup_frame,
            text=self._login_skins[self._selected_skin_index]["name"],
            bg="#111827",
            fg="#94a3b8",
            font=("Segoe UI", 9, "bold"),
        )
        self.skin_popup_label.grid(row=3, column=0, columnspan=3, pady=(2, 10))
        self.login_skin_popup_id = self.login_bg.create_window(0, 0, window=self.skin_popup_frame)
        self.login_bg.itemconfigure(self.login_skin_popup_id, state="hidden")

        left_rail = tk.Frame(self.login_bg, bg="#8ecdd7")
        tk.Label(left_rail, text="Store", bg="#8ecdd7", fg="#f8fafc", font=("Segoe UI", 13, "bold")).pack(
            pady=(0, 10),
            anchor="w",
        )
        self.badge_google = tk.Button(
            left_rail,
            text="GET IT ON\nGOOGLE PLAY",
            bg="#0b1220",
            fg="#f8fafc",
            activebackground="#1e293b",
            activeforeground="#f8fafc",
            relief="flat",
            width=18,
            height=2,
            font=("Segoe UI", 10, "bold"),
        )
        self.badge_google.pack(pady=8, fill="x")
        self.badge_apple = tk.Button(
            left_rail,
            text="DOWNLOAD ON THE\nAPP STORE",
            bg="#0b1220",
            fg="#f8fafc",
            activebackground="#1e293b",
            activeforeground="#f8fafc",
            relief="flat",
            width=18,
            height=2,
            font=("Segoe UI", 10, "bold"),
        )
        self.badge_apple.pack(pady=8, fill="x")
        self.login_left_rail_id = self.login_bg.create_window(0, 0, window=left_rail)

        login_card = tk.Frame(self.login_bg, bg="#1a2f42", bd=0, highlightthickness=2, highlightbackground="#7ef9f2")
        self.login_card_id = self.login_bg.create_window(0, 0, window=login_card)
        login_card.grid_columnconfigure(0, weight=1)
        login_card.grid_columnconfigure(1, weight=1)
        login_card.grid_columnconfigure(2, weight=1)
        self.login_card = login_card

        tk.Label(
            login_card,
            text="WELCOME",
            bg="#1a2f42",
            fg="#8ff7f7",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 2))

        tk.Label(
            login_card,
            text="Enter the battlefield",
            bg="#1a2f42",
            fg="#c9d7e5",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 12))

        self.server_ip_var = tk.StringVar(value="127.0.0.1")
        self.server_port_var = tk.StringVar(value="5000")

        tk.Label(login_card, text="Username", bg="#1a2f42", fg="#c9d7e5", font=("Segoe UI", 10, "bold")).grid(
            row=2,
            column=0,
            sticky="w",
            padx=(18, 8),
        )
        self.username_var = tk.StringVar(value="Player")
        username_entry = tk.Entry(
            login_card,
            textvariable=self.username_var,
            width=26,
            bg="#101c29",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            highlightthickness=2,
            highlightbackground="#22384f",
            highlightcolor="#22c55e",
            font=("Segoe UI", 12, "bold"),
        )
        username_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 18))
        username_entry.bind("<Return>", lambda _event: self._connect())
        username_entry.bind("<KeyRelease>", lambda _event: self._on_username_changed())
        username_entry.bind("<FocusIn>", lambda _event: username_entry.configure(highlightbackground="#22c55e"))
        username_entry.bind("<FocusOut>", lambda _event: username_entry.configure(highlightbackground="#22384f"))
        self.username_entry = username_entry

        self.username_count_var = tk.StringVar(value="0/20")
        tk.Label(
            login_card,
            textvariable=self.username_count_var,
            bg="#1a2f42",
            fg="#93c5fd",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=3, column=2, sticky="e", padx=(0, 20), pady=(2, 0))

        self.login_settings_button = tk.Button(
            login_card,
            text="Show Settings",
            command=self._toggle_login_advanced,
            bg="#172636",
            fg="#d8e6f2",
            activebackground="#22364b",
            activeforeground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        self.login_settings_button.grid(row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(10, 0))

        self.login_advanced_frame = tk.Frame(login_card, bg="#1a2f42", padx=0, pady=10)
        self.login_advanced_frame.columnconfigure(1, weight=1)

        tk.Label(self.login_advanced_frame, text="Server IP", bg="#1a2f42", fg="#c9d7e5", font=("Segoe UI", 10, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=(0, 8),
        )
        tk.Entry(
            self.login_advanced_frame,
            textvariable=self.server_ip_var,
            width=24,
            bg="#101c29",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 10),
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=(0, 8),
        )

        tk.Label(self.login_advanced_frame, text="Port", bg="#1a2f42", fg="#c9d7e5", font=("Segoe UI", 10, "bold")).grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 8),
        )
        tk.Entry(
            self.login_advanced_frame,
            textvariable=self.server_port_var,
            width=24,
            bg="#101c29",
            fg="#f8fafc",
            insertbackground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 10),
        ).grid(
            row=1,
            column=1,
            sticky="ew",
        )

        self.login_connect_button = tk.Button(
            login_card,
            text="🎮 JOIN BATTLE",
            command=self._connect,
            bg="#38d86b",
            fg="#f8fafc",
            activebackground="#21b554",
            activeforeground="#f8fafc",
            relief="flat",
            font=("Segoe UI", 20, "bold"),
            width=8,
            height=2,
        )
        self.login_connect_button.grid(row=7, column=0, columnspan=3, sticky="ew", padx=18, pady=(14, 8))
        self.login_connect_button.bind("<Enter>", lambda _event: self._set_play_hover(True))
        self.login_connect_button.bind("<Leave>", lambda _event: self._set_play_hover(False))

        self.login_stats_var = tk.StringVar(value="Players Online: 0   |   Active Games: 0")
        tk.Label(
            login_card,
            textvariable=self.login_stats_var,
            bg="#1a2f42",
            fg="#99f6e4",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 8))

        self.skin_preview_name_var = tk.StringVar(value=self._login_skins[self._selected_skin_index]["name"])
        skin_preview_wrap = tk.Frame(login_card, bg="#1a2f42")
        skin_preview_wrap.grid(row=6, column=0, columnspan=3, sticky="ew", padx=18, pady=(6, 0))
        tk.Label(
            skin_preview_wrap,
            text="Your Snake:",
            bg="#1a2f42",
            fg="#94a3b8",
            font=("Segoe UI", 10),
        ).pack(side="left")
        self.skin_preview_swatch = tk.Label(
            skin_preview_wrap,
            text="🐍",
            bg=self._login_skins[self._selected_skin_index]["color"],
            fg="#ffffff",
            font=("Segoe UI Emoji", 11, "bold"),
            width=3,
            height=1,
        )
        self.skin_preview_swatch.pack(side="left", padx=8)
        tk.Label(
            skin_preview_wrap,
            textvariable=self.skin_preview_name_var,
            bg="#1a2f42",
            fg="#f8fafc",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        self.login_status_var = tk.StringVar(value="Enter your username to continue.")
        tk.Label(login_card, textvariable=self.login_status_var, justify="left", bg="#1a2f42", fg="#8ff7f7", font=("Segoe UI", 9)).grid(
            row=9,
            column=0,
            columnspan=3,
            sticky="w",
            padx=18,
            pady=(8, 14),
        )

        self.login_emojis = [
            {"glyph": "🎮", "x_ratio": 0.79, "y_ratio": 0.18, "phase": 0.0},
            {"glyph": "⚔️", "x_ratio": 0.16, "y_ratio": 0.24, "phase": 1.8},
            {"glyph": "🏆", "x_ratio": 0.88, "y_ratio": 0.76, "phase": 3.1},
        ]
        hint_label = tk.Label(
            self.login_bg,
            text="Watch the snakes battle below! ⚔️",
            bg="#8ecdd7",
            fg="#4b5563",
            font=("Segoe UI", 10, "bold"),
        )
        self.login_hint_id = self.login_bg.create_window(0, 0, window=hint_label)

        self.login_bg.bind("<Configure>", self._draw_login_background)

        # Top connection bar.
        self.top_frame = ttk.Frame(self.root, padding=10)
        self.top_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        for idx in range(8):
            self.top_frame.columnconfigure(idx, weight=1 if idx in {1, 3, 5} else 0)

        ttk.Label(self.top_frame, text="Server IP").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(self.top_frame, textvariable=self.server_ip_var, width=16).grid(row=0, column=1, sticky="ew", padx=(0, 12))

        ttk.Label(self.top_frame, text="Port").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(self.top_frame, textvariable=self.server_port_var, width=8).grid(row=0, column=3, sticky="ew", padx=(0, 12))

        ttk.Label(self.top_frame, text="Username").grid(row=0, column=4, sticky="w", padx=(0, 6))
        ttk.Entry(self.top_frame, textvariable=self.username_var, width=16).grid(row=0, column=5, sticky="ew", padx=(0, 12))

        self.connect_button = ttk.Button(self.top_frame, text="Connect", command=self._connect)
        self.connect_button.grid(row=0, column=6, padx=(0, 8))

        self.disconnect_button = ttk.Button(
            self.top_frame,
            text="Disconnect",
            command=self._disconnect_to_prelobby,
            state="disabled",
        )
        self.disconnect_button.grid(row=0, column=7)

        # Left panel (online users + actions).
        self.left_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        self.left_frame.grid(row=1, column=0, sticky="nsew")
        self.left_frame.rowconfigure(1, weight=1)
        self.left_frame.columnconfigure(0, weight=1)

        ttk.Label(self.left_frame, text="Online Players", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")

        self.players_listbox = tk.Listbox(self.left_frame, height=18, exportselection=False)
        self.players_listbox.grid(row=1, column=0, sticky="nsew", pady=(6, 8))

        actions = ttk.Frame(self.left_frame)
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.columnconfigure(2, weight=1)

        self.refresh_button = ttk.Button(actions, text="Refresh View", command=self._render_online_players, state="disabled")
        self.refresh_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.invite_button = ttk.Button(actions, text="Invite Selected", command=self._invite_selected_player, state="disabled")
        self.invite_button.grid(row=0, column=1, sticky="ew")

        # Sprint 6 PBI 6.8: one-click spectator mode entry from lobby GUI.
        self.spectate_button = ttk.Button(
            actions,
            text="Watch Spectator",
            command=self._switch_to_spectator,
            state="disabled",
        )
        self.spectate_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # Waiting state card.
        self.waiting_var = tk.StringVar(value="Not connected.")
        waiting = ttk.LabelFrame(self.left_frame, text="Waiting State", padding=10)
        waiting.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(waiting, textvariable=self.waiting_var, wraplength=240, justify="left").grid(row=0, column=0, sticky="w")

        # Right panel (logs + chat).
        self.right_frame = ttk.Frame(self.root, padding=(0, 0, 10, 10))
        self.right_frame.grid(row=1, column=1, sticky="nsew")
        self.right_frame.rowconfigure(0, weight=1)
        self.right_frame.rowconfigure(1, weight=1)
        self.right_frame.columnconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(self.right_frame, text="Lobby Events", padding=8)
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        chat_tabs_frame = ttk.LabelFrame(self.right_frame, text="Chat", padding=8)
        chat_tabs_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        chat_tabs_frame.rowconfigure(0, weight=1)
        chat_tabs_frame.columnconfigure(0, weight=1)

        self.chat_notebook = ttk.Notebook(chat_tabs_frame)
        self.chat_notebook.grid(row=0, column=0, sticky="nsew")
        self.chat_notebook.bind("<<NotebookTabChanged>>", lambda _event: self._sync_mode_from_selected_tab())
        self._ensure_chat_tab("Public")
        self._select_chat_tab("Public")

        chat_bar = ttk.Frame(self.right_frame, padding=(0, 10, 0, 0))
        chat_bar.grid(row=2, column=0, sticky="ew")
        chat_bar.columnconfigure(0, weight=1)

        self.chat_var = tk.StringVar()
        self.chat_entry = ttk.Entry(chat_bar, textvariable=self.chat_var)
        self.chat_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.chat_entry.bind("<Return>", lambda _event: self._send_chat())

        self.chat_mode_var = tk.StringVar(value="Public")
        self.chat_mode_combo = ttk.Combobox(
            chat_bar,
            textvariable=self.chat_mode_var,
            values=["Public", "Private"],
            state="readonly",
            width=10,
        )
        self.chat_mode_combo.grid(row=0, column=1, padx=(0, 8))
        self.chat_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_chat_target_state())

        self.chat_target_var = tk.StringVar(value="None")
        self.chat_target_combo = ttk.Combobox(
            chat_bar,
            textvariable=self.chat_target_var,
            state="disabled",
            width=16,
        )
        self.chat_target_combo.grid(row=0, column=2, padx=(0, 8))
        self.chat_target_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_chat_target_changed())
        self.chat_target_combo.bind("<KeyRelease>", lambda _event: self._on_chat_target_changed())

        self.send_button = ttk.Button(chat_bar, text="Send Chat", command=self._send_chat, state="disabled")
        self.send_button.grid(row=0, column=3)

        # Start with login-only UI until username is accepted.
        self._show_login_gate()

    def _show_login_gate(self) -> None:
        """Show only the login interface and hide lobby panels."""

        self.login_frame.grid()
        self.top_frame.grid_remove()
        self.left_frame.grid_remove()
        self.right_frame.grid_remove()
        self.login_connect_button.configure(state="normal")
        self.connect_button.configure(state="normal")

    def _toggle_login_advanced(self) -> None:
        """Toggle login advanced settings panel for IP/port overrides."""

        self._login_advanced_visible = not self._login_advanced_visible
        if self._login_advanced_visible:
            self.login_advanced_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
            self.login_settings_button.configure(text="Hide Settings")
        else:
            self.login_advanced_frame.grid_remove()
            self.login_settings_button.configure(text="Show Settings")

    def _show_lobby(self) -> None:
        """Reveal lobby panels after successful username admission."""

        self.login_frame.grid_remove()
        self.top_frame.grid()
        self.left_frame.grid()
        self.right_frame.grid()

    def _on_username_changed(self) -> None:
        """Apply username limits and update connect-button enable state."""

        raw = self.username_var.get()
        if len(raw) > 20:
            raw = raw[:20]
            self.username_var.set(raw)
        self.username_count_var.set(f"{len(raw.strip())}/20")
        has_username = bool(raw.strip())
        self.login_connect_button.configure(state="normal" if has_username else "disabled")
        if has_username:
            self.login_connect_button.configure(bg="#38d86b", fg="#f8fafc")
        else:
            self.login_connect_button.configure(bg="#5b6d7e", fg="#cad5df")

    def _set_play_hover(self, hovering: bool) -> None:
        """Handle hover visual state for the big play button."""

        self._login_play_hover = hovering
        if self.login_connect_button.cget("state") == "disabled":
            return
        if hovering:
            self.login_connect_button.configure(bg="#4be57a")
        else:
            self.login_connect_button.configure(bg="#38d86b")

    def _toggle_skin_menu(self) -> None:
        """Show or hide skin selector popup."""

        self._login_skin_menu_visible = not self._login_skin_menu_visible
        self.login_bg.itemconfigure(
            self.login_skin_popup_id,
            state=("normal" if self._login_skin_menu_visible else "hidden"),
        )

    def _select_skin(self, index: int) -> None:
        """Select one snake skin and refresh preview/highlight."""

        self._selected_skin_index = index
        chosen = self._login_skins[index]
        self.skin_preview_swatch.configure(bg=chosen["color"])
        self.skin_preview_name_var.set(chosen["name"])
        self.skin_popup_label.configure(text=chosen["name"])
        for idx, btn in enumerate(self._skin_popup_buttons):
            if idx == index:
                btn.configure(highlightthickness=2, highlightbackground="#f8fafc")
            else:
                btn.configure(highlightthickness=0)

    def _set_music_hover(self, hovering: bool) -> None:
        """Handle hover visual state for the music toggle."""

        if self._login_music_on:
            self.music_button.configure(bg="#166534" if hovering else "#14532d")
        else:
            self.music_button.configure(bg="#7f1d1d" if hovering else "#991b1b")

    def _toggle_music(self) -> None:
        """Toggle ambient music indicator state in pre-lobby UI."""

        self._login_music_on = not self._login_music_on
        if self._login_music_on:
            self.music_button.configure(text="♫ Music  ON", bg="#14532d", fg="#dcfce7")
            self.music_chip.configure(highlightbackground="#4ade80")
        else:
            self.music_button.configure(text="♫ Music  OFF", bg="#991b1b", fg="#fee2e2")
            self.music_chip.configure(highlightbackground="#ef4444")

    def _init_login_scene(self) -> None:
        """Initialize animated snake scene and particles for pre-lobby."""

        self._on_username_changed()
        snakes_config = [
            ("#39d353", "#22c55e"),
            ("#ef4444", "#f87171"),
            ("#14b8a6", "#2dd4bf"),
            ("#facc15", "#fde047"),
        ]
        self._login_snakes = []
        for index, (color, glow) in enumerate(snakes_config):
            base_x = 220 + (index * 145)
            base_y = 160 + (index % 2) * 180
            self._login_snakes.append(
                {
                    "x": float(base_x),
                    "y": float(base_y),
                    "angle": random.uniform(0, math.tau),
                    "speed": random.uniform(1.8, 2.8),
                    "color": color,
                    "glow": glow,
                    "body": [(float(base_x), float(base_y)) for _ in range(18)],
                    "tongue_ttl": 0.0,
                    "eye_blink": random.uniform(0.0, 2.0),
                }
            )
        self._login_particles = []
        self._login_craters = []
        for _ in range(15):
            self._login_craters.append(
                {
                    "x": random.uniform(40, 860),
                    "y": random.uniform(80, 580),
                    "r": random.uniform(16, 54),
                }
            )

    def _start_login_animation(self) -> None:
        """Start perpetual animation loop for pre-lobby visual effects."""

        if self._login_anim_job is not None:
            return
        self._animate_login_scene()

    def _animate_login_scene(self) -> None:
        """Animate snakes, collisions, particles, and subtle UI accents."""

        self._login_ping_phase += 0.16
        self._login_title_phase += 0.08
        if not self.login_frame.winfo_exists():
            self._login_anim_job = None
            return

        width = max(1, self.login_bg.winfo_width())
        height = max(1, self.login_bg.winfo_height())
        self.login_bg.delete("anim")

        # Draw battlefield crater details.
        for crater in self._login_craters:
            cx = float(crater["x"]) * (width / 900.0)
            cy = float(crater["y"]) * (height / 620.0)
            r = float(crater["r"])
            self.login_bg.create_oval(
                cx - r + 3,
                cy - (r * 0.62) + 3,
                cx + r + 3,
                cy + (r * 0.62) + 3,
                fill="#000000",
                outline="",
                stipple="gray25",
                tags="anim",
            )
            self.login_bg.create_oval(
                cx - r,
                cy - (r * 0.62),
                cx + r,
                cy + (r * 0.62),
                fill="#213243",
                outline="#8dd6dd",
                width=1,
                tags="anim",
            )

        # Update snake positions.
        for snake in self._login_snakes:
            snake["angle"] += random.uniform(-0.08, 0.08)
            next_x = snake["x"] + math.cos(snake["angle"]) * snake["speed"]
            next_y = snake["y"] + math.sin(snake["angle"]) * snake["speed"]

            # Bounce inside safe content area.
            min_x, max_x = 110.0, max(200.0, width - 80.0)
            min_y, max_y = 90.0, max(200.0, height - 90.0)
            if next_x < min_x or next_x > max_x:
                snake["angle"] = math.pi - snake["angle"]
                next_x = max(min_x, min(max_x, next_x))
            if next_y < min_y or next_y > max_y:
                snake["angle"] = -snake["angle"]
                next_y = max(min_y, min(max_y, next_y))

            snake["x"], snake["y"] = next_x, next_y
            body = snake["body"]
            body.insert(0, (next_x, next_y))
            del body[18:]

            # Tongue flicker.
            if snake["tongue_ttl"] <= 0 and random.random() < 0.02:
                snake["tongue_ttl"] = 8
            snake["tongue_ttl"] = max(0, snake["tongue_ttl"] - 1)

        # Collision sparks and repel.
        for i in range(len(self._login_snakes)):
            for j in range(i + 1, len(self._login_snakes)):
                a = self._login_snakes[i]
                b = self._login_snakes[j]
                dx = a["x"] - b["x"]
                dy = a["y"] - b["y"]
                dist = math.hypot(dx, dy)
                if dist < 52:
                    a["angle"] += 0.22
                    b["angle"] -= 0.22
                    mid_x = (a["x"] + b["x"]) / 2.0
                    mid_y = (a["y"] + b["y"]) / 2.0
                    for _ in range(5):
                        self._login_particles.append(
                            {
                                "x": mid_x,
                                "y": mid_y,
                                "vx": random.uniform(-2.4, 2.4),
                                "vy": random.uniform(-2.4, 2.4),
                                "life": random.uniform(8.0, 18.0),
                                "color": random.choice([a["glow"], b["glow"], "#ffffff"]),
                            }
                        )

        # Draw snakes.
        for snake in self._login_snakes:
            body = snake["body"]
            glow_points: list[float] = []
            for bx, by in body[:12]:
                glow_points.extend([bx, by])
            if len(glow_points) >= 4:
                self.login_bg.create_line(
                    glow_points,
                    fill=snake["glow"],
                    width=20,
                    smooth=True,
                    capstyle="round",
                    tags="anim",
                )
                self.login_bg.create_line(
                    glow_points,
                    fill=snake["color"],
                    width=12,
                    smooth=True,
                    capstyle="round",
                    tags="anim",
                )

            head_x, head_y = body[0]
            # Eyes and pupils.
            eye_offset_x = math.cos(snake["angle"] + 1.0) * 6
            eye_offset_y = math.sin(snake["angle"] + 1.0) * 6
            self.login_bg.create_oval(
                head_x - 7 + eye_offset_x,
                head_y - 8 + eye_offset_y,
                head_x - 1 + eye_offset_x,
                head_y - 2 + eye_offset_y,
                fill="#ffffff",
                outline="",
                tags="anim",
            )
            self.login_bg.create_oval(
                head_x + 1 + eye_offset_x,
                head_y - 8 + eye_offset_y,
                head_x + 7 + eye_offset_x,
                head_y - 2 + eye_offset_y,
                fill="#ffffff",
                outline="",
                tags="anim",
            )
            pupil_x = math.cos(snake["angle"]) * 1.7
            pupil_y = math.sin(snake["angle"]) * 1.7
            self.login_bg.create_oval(
                head_x - 5 + eye_offset_x + pupil_x,
                head_y - 6 + eye_offset_y + pupil_y,
                head_x - 2 + eye_offset_x + pupil_x,
                head_y - 3 + eye_offset_y + pupil_y,
                fill="#0f172a",
                outline="",
                tags="anim",
            )
            self.login_bg.create_oval(
                head_x + 3 + eye_offset_x + pupil_x,
                head_y - 6 + eye_offset_y + pupil_y,
                head_x + 6 + eye_offset_x + pupil_x,
                head_y - 3 + eye_offset_y + pupil_y,
                fill="#0f172a",
                outline="",
                tags="anim",
            )

            # Forked tongue.
            if snake["tongue_ttl"] > 0:
                tip_x = head_x + math.cos(snake["angle"]) * 15
                tip_y = head_y + math.sin(snake["angle"]) * 15
                side1_x = tip_x + math.cos(snake["angle"] + 0.35) * 7
                side1_y = tip_y + math.sin(snake["angle"] + 0.35) * 7
                side2_x = tip_x + math.cos(snake["angle"] - 0.35) * 7
                side2_y = tip_y + math.sin(snake["angle"] - 0.35) * 7
                self.login_bg.create_line(head_x, head_y, tip_x, tip_y, fill="#fecaca", width=2, tags="anim")
                self.login_bg.create_line(tip_x, tip_y, side1_x, side1_y, fill="#fca5a5", width=2, tags="anim")
                self.login_bg.create_line(tip_x, tip_y, side2_x, side2_y, fill="#fca5a5", width=2, tags="anim")

        # Draw/update particles.
        next_particles: list[dict[str, float | str]] = []
        for particle in self._login_particles:
            life = float(particle["life"]) - 1.0
            if life <= 0:
                continue
            x = float(particle["x"]) + float(particle["vx"])
            y = float(particle["y"]) + float(particle["vy"])
            particle["x"], particle["y"], particle["life"] = x, y, life
            next_particles.append(particle)
            size = max(1, int(life / 4))
            self.login_bg.create_oval(
                x - size,
                y - size,
                x + size,
                y + size,
                fill=str(particle["color"]),
                outline="",
                tags="anim",
            )
        self._login_particles = next_particles

        # Floating emoji decorations.
        for emoji in self.login_emojis:
            phase = float(emoji["phase"]) + self._login_title_phase
            ex = float(emoji["x_ratio"]) * width
            ey = float(emoji["y_ratio"]) * height + math.sin(phase) * 9
            self.login_bg.create_text(
                ex,
                ey,
                text=str(emoji["glyph"]),
                font=("Segoe UI Emoji", 22, "bold"),
                fill="#f8fafc",
                tags="anim",
            )

        # Music ping indicator.
        music_x, music_y = self.login_bg.coords(self.login_music_id)
        ping_color = "#4ade80" if self._login_music_on else "#ef4444"
        ping_r = 6 + (2 if self._login_music_on else 0) + abs(math.sin(self._login_ping_phase)) * (6 if self._login_music_on else 1)
        self.login_bg.create_oval(
            music_x + 80 - ping_r,
            music_y - 20 - ping_r,
            music_x + 80 + ping_r,
            music_y - 20 + ping_r,
            outline=ping_color,
            width=2,
            tags="anim",
        )
        self.login_bg.create_oval(
            music_x + 80 - 3,
            music_y - 20 - 3,
            music_x + 80 + 3,
            music_y - 20 + 3,
            fill=ping_color,
            outline="",
            tags="anim",
        )

        # Play button arrow hover animation.
        if self._login_play_hover and self.login_connect_button.cget("state") == "normal":
            arrow_count = 1 + int(abs(math.sin(self._login_ping_phase * 1.4)) * 2)
            self.login_connect_button.configure(text=f"🎮 JOIN BATTLE {'▶' * arrow_count}")
        elif self.login_connect_button.cget("state") == "normal":
            self.login_connect_button.configure(text="🎮 JOIN BATTLE ▶")

        # Title shimmer pulse.
        shimmer = 236 + int(abs(math.sin(self._login_title_phase)) * 18)
        self.login_title_label.configure(fg=f"#{shimmer:02x}{shimmer:02x}{245:02x}")

        self._login_anim_job = self.root.after(33, self._animate_login_scene)

    def _append_log(self, text: str) -> None:
        """Append one line to the event log area."""

        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _append_chat(self, tab_name: str, text: str) -> None:
        """Append one line to a chat tab, creating the tab if needed."""

        chat_text = self._ensure_chat_tab(tab_name)
        chat_text.configure(state="normal")
        chat_text.insert("end", f"{text}\n")
        chat_text.see("end")
        chat_text.configure(state="disabled")

    def _ensure_chat_tab(self, tab_name: str) -> tk.Text:
        """Create a chat tab on demand and return its text widget."""

        if tab_name in self.chat_tabs:
            return self.chat_tabs[tab_name]

        tab = ttk.Frame(self.chat_notebook)
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        text_widget = tk.Text(tab, height=8, wrap="word", state="disabled")
        text_widget.grid(row=0, column=0, sticky="nsew")
        self.chat_notebook.add(tab, text=tab_name)
        self.chat_tabs[tab_name] = text_widget
        return text_widget

    def _select_chat_tab(self, tab_name: str) -> None:
        """Select a tab if it exists."""

        for tab_id in self.chat_notebook.tabs():
            if self.chat_notebook.tab(tab_id, "text") == tab_name:
                self.chat_notebook.select(tab_id)
                return

    def _selected_chat_tab_name(self) -> str:
        """Return current chat tab label."""

        current = self.chat_notebook.select()
        if not current:
            return "Public"
        return str(self.chat_notebook.tab(current, "text"))

    def _sync_mode_from_selected_tab(self) -> None:
        """Keep mode/target selectors aligned with selected chat tab."""

        tab_name = self._selected_chat_tab_name()
        if tab_name == "Public":
            self.chat_mode_var.set("Public")
            self.chat_target_var.set("None")
        else:
            self.chat_mode_var.set("Private")
            self.chat_target_var.set(tab_name)
        self._update_chat_target_state()

    def _set_connected_ui(self, connected: bool) -> None:
        """Enable/disable controls based on connection state."""

        self._chat_enabled_by_connection = connected
        self.connect_button.configure(state="disabled" if connected else "normal")
        self.login_connect_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        # Invite/spectate become enabled only after username is accepted.
        self.invite_button.configure(state="normal" if connected and self.username_confirmed else "disabled")
        self.refresh_button.configure(state="normal" if connected else "disabled")
        self.spectate_button.configure(state="normal" if connected and self.username_confirmed else "disabled")
        self._update_send_chat_state()

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
        self.username_confirmed = False
        self._username_retry_count = 0
        self._set_connected_ui(True)
        self.login_status_var.set("Connecting and submitting username...")
        self.waiting_var.set("Submitting username...")
        self._append_log(f"[SYSTEM] Connected to {server_ip}:{server_port}")

        # Start socket receiver in background so GUI thread remains responsive.
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receiver_thread.start()

        # Send username registration after receiving connect ack.
        # We push this action into queue to keep ordering deterministic.
        self.incoming_queue.put(("register_username", username))

    def _disconnect(self, *, silent: bool = False) -> None:
        """Disconnect client and reset GUI to idle state."""

        self.running = False
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None

        self.online_users = []
        self.username_confirmed = False
        self._login_online_count = 0
        self._login_active_count = 0
        self.login_stats_var.set("Players Online: 0   |   Active Games: 0")
        self._render_online_players()
        self.chat_mode_var.set("Public")
        self.chat_target_var.set("None")
        self._select_chat_tab("Public")
        self._update_chat_target_state()
        self.login_status_var.set("Enter your username to continue.")
        if self.login_gate_enabled:
            self._show_login_gate()
        else:
            self._show_lobby()
        self.waiting_var.set("Not connected.")
        self._set_connected_ui(False)
        if not silent:
            self._append_log("[SYSTEM] Disconnected.")

    def _disconnect_to_prelobby(self) -> None:
        """Disconnect and return user to the Pygame pre-lobby flow."""

        server_ip = self.server_ip_var.get().strip() or "127.0.0.1"
        try:
            server_port = int(self.server_port_var.get().strip())
        except ValueError:
            server_port = 5000

        self._disconnect(silent=True)
        self.root.destroy()

        from client.prelobby_pygame import PreLobby

        selected_username, _selected_skin = PreLobby().run()
        if not selected_username:
            return

        app = ArenaGuiApp(
            initial_username=selected_username,
            server_ip=server_ip,
            server_port=server_port,
            auto_connect=True,
            login_gate_enabled=False,
        )
        app.run()

    def _switch_to_pygame(self, game_id: str, opponent: str | None = None) -> None:
        """Close lobby GUI and launch the real Pygame match window."""

        if self._switching_to_pygame:
            return
        self._switching_to_pygame = True

        server_ip = self.server_ip_var.get().strip() or "127.0.0.1"
        try:
            server_port = int(self.server_port_var.get().strip())
        except ValueError:
            server_port = 5000

        self._append_log(f"[MATCH] Switching to gameplay window (game_id={game_id})...")
        if self.connection is not None and opponent is not None:
            try:
                # Tell backend this disconnect is intentional handoff.
                self.connection.send_message(
                    make_invitation_message(
                        from_user=self.username,
                        to_user=opponent,
                        action="handoff",
                        game_id=game_id,
                    )
                )
            except OSError:
                # If handoff marker fails, fallback to normal reconnect flow.
                pass
        self._disconnect(silent=True)
        self.root.destroy()

        # Import lazily so GUI startup does not require pygame until needed.
        from client.game_window import main as pygame_main

        pygame_main(
            server_ip=server_ip,
            server_port=server_port,
            username=self.username,
            preferred_opponent=opponent,
        )

    def _switch_to_spectator(self) -> None:
        """Launch the Pygame viewer in spectator mode from lobby GUI."""

        if self._switching_to_pygame:
            return

        target_player = self._selected_opponent()
        if target_player is None:
            messagebox.showwarning(
                "Select Spectate Target",
                "Select a player from 'Online Players' to spectate their match.",
            )
            return

        self._switching_to_pygame = True

        server_ip = self.server_ip_var.get().strip() or "127.0.0.1"
        try:
            server_port = int(self.server_port_var.get().strip())
        except ValueError:
            server_port = 5000

        self._append_log(f"[SPECTATOR] Switching to spectator view for {target_player}...")
        self._disconnect(silent=True)
        self.root.destroy()

        from client.game_window import main as pygame_main

        pygame_main(
            server_ip=server_ip,
            server_port=server_port,
            username=self.username,
            preferred_opponent=target_player,
            spectator_mode=True,
        )

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
            self._login_online_count = len(self.online_users)
            self._login_active_count = int(payload.get("active_players", 0))
            self.login_stats_var.set(
                f"Players Online: {self._login_online_count}   |   Active Games: {self._login_active_count}"
            )
            # Username is considered accepted once server includes it in
            # authoritative online users list.
            self.username_confirmed = self.username in self.online_users
            if self.username_confirmed:
                self.login_status_var.set(f"Welcome {self.username}.")
                self._show_lobby()
            self._render_online_players()
            self._set_connected_ui(True)

            has_opponent = any(user != self.username for user in self.online_users)
            if has_opponent:
                self.waiting_var.set("Opponent available. Select a player and click 'Invite Selected'.")
            else:
                self.waiting_var.set("No opponent available. Waiting for another player to join.")
            return

        if msg_type == MessageType.INVITATION.value:
            from_user = payload.get("from_user", "Unknown")
            to_user = payload.get("to_user", "")
            action = payload.get("action", "").lower()

            if action == "send":
                # PBI invitation UX: target player can accept or decline.
                self._append_log(f"[INVITATION] {from_user} invited you to play.")
                accepted = messagebox.askyesno(
                    "Invitation Received",
                    f"{from_user} wants to play with you.\n\nDo you accept?",
                )

                if self.connection is not None:
                    response_action = "accept" if accepted else "decline"
                    self.connection.send_message(
                        make_invitation_message(
                            from_user=from_user,
                            to_user=self.username,
                            action=response_action,
                        )
                    )
                    self._append_log(
                        f"[INVITATION] You {response_action}ed invitation from {from_user}."
                    )
                return

            if action in {"accepted", "declined", "cancelled"}:
                # Decision updates from server for both involved players.
                self._append_log(f"[INVITATION] {from_user} -> {to_user}: {action}")
                messagebox.showinfo("Invitation Update", f"Invitation status: {action}")
                return

            if action == "match_started":
                game_id = payload.get("game_id", "unknown")
                self._append_log(f"[MATCH] Started for {from_user} vs {to_user} (game_id={game_id})")
                # Auto-switch matched players from lobby GUI to the gameplay window.
                if self.username.casefold() in {str(from_user).casefold(), str(to_user).casefold()}:
                    opponent = str(to_user) if str(from_user).casefold() == self.username.casefold() else str(from_user)
                    self._switch_to_pygame(str(game_id), opponent=opponent)
                else:
                    messagebox.showinfo("Match Started", f"Game started! ID: {game_id}")
                return

            # Fallback for any unknown invitation action.
            self._append_log(f"[INVITATION] {payload}")
            return

        if msg_type == MessageType.CHAT.value:
            sender = payload.get("sender", "SERVER")
            text = payload.get("message", "")
            scope = str(payload.get("scope", "")).strip().lower()
            recipient = payload.get("recipient")
            if scope == "match":
                # Keep in-match chat isolated to the Pygame gameplay window.
                return
            if recipient:
                if str(sender).casefold() == self.username.casefold():
                    private_tab = str(recipient)
                    self._append_chat(private_tab, f"{sender}: {text}")
                else:
                    private_tab = str(sender)
                    self._append_chat(private_tab, f"{sender}: {text}")
            else:
                self._append_chat("Public", f"{sender}: {text}")
            return

        if msg_type == MessageType.ERROR.value:
            self._append_log(f"[ERROR] {payload.get('message', 'Unknown error')} - {payload.get('details', '')}")

            # If username is rejected as taken, force user to enter another.
            # Keep connection alive and retry registration immediately.
            if "Username already taken" in payload.get("message", ""):
                if self._retry_same_username:
                    self._username_retry_count += 1
                    if self._username_retry_count <= self._username_retry_max:
                        self._append_log(
                            f"[SYSTEM] Username still releasing, retry {self._username_retry_count}/{self._username_retry_max}..."
                        )
                    elif self._username_retry_count % 5 == 0:
                        self._append_log(
                            f"[SYSTEM] Waiting to reclaim username '{self.username}'... retry {self._username_retry_count}"
                        )
                    self.root.after(500, lambda: self.incoming_queue.put(("register_username", self.username)))
                    return

                new_username = simpledialog.askstring(
                    "Username Taken",
                    "This username is already in use.\nPlease enter a different username:",
                    parent=self.root,
                )
                if new_username is None:
                    self._append_log("[SYSTEM] Username change cancelled. Disconnecting.")
                    self._disconnect()
                    return

                candidate = new_username.strip()
                is_valid, validation_message = validate_username(candidate)
                if not is_valid:
                    messagebox.showerror("Invalid Username", validation_message)
                    return

                self.username = candidate
                self.username_var.set(candidate)
                self._on_username_changed()
                if self.connection is not None:
                    self.connection.send_message(make_username_message(candidate))
                    self._append_log(f"[SYSTEM] Retrying username: {candidate}")
            return

        self._append_log(f"[INCOMING] {message}")

    def _render_online_players(self) -> None:
        """Render latest online users into listbox."""

        self.players_listbox.delete(0, "end")
        for user in self.online_users:
            label = f"{user} (You)" if user == self.username else user
            self.players_listbox.insert("end", label)
        self._refresh_chat_targets()

    def _refresh_chat_targets(self) -> None:
        """Refresh private chat target choices from current online users."""

        candidates = [user for user in self.online_users if user != self.username]
        previous = self.chat_target_var.get().strip()
        self.chat_target_combo["values"] = ["None", *candidates]

        if previous in candidates:
            self.chat_target_var.set(previous)
        else:
            self.chat_target_var.set("None")

        self._update_chat_target_state()

    def _update_chat_target_state(self) -> None:
        """Enable private target picker only when private mode is selected."""

        is_private = self.chat_mode_var.get().strip().lower() == "private"
        self.chat_target_combo.configure(state="normal" if is_private else "disabled")
        if is_private and not self.chat_target_var.get().strip():
            self.chat_target_var.set("None")
        if is_private:
            target = self._private_chat_target()
            if target is not None:
                self._ensure_chat_tab(target)
                self._select_chat_tab(target)
        else:
            self._select_chat_tab("Public")
        self._update_send_chat_state()

    def _on_chat_target_changed(self) -> None:
        """Handle private target edits and switch chat tab when valid."""

        if self.chat_mode_var.get().strip().lower() != "private":
            self._update_send_chat_state()
            return

        target = self._private_chat_target()
        if target is not None:
            self._ensure_chat_tab(target)
            self._select_chat_tab(target)
        self._update_send_chat_state()

    def _private_chat_target(self) -> str | None:
        """Return selected/typed private target when it matches an online opponent."""

        raw_target = self.chat_target_var.get().strip()
        if not raw_target or raw_target.lower() == "none":
            return None

        for user in self.online_users:
            if user == self.username:
                continue
            if user.casefold() == raw_target.casefold():
                return user
        return None

    def _update_send_chat_state(self) -> None:
        """Lock chat send when private mode has no valid selected/typed target."""

        can_chat = self._chat_enabled_by_connection and self.username_confirmed
        is_private = self.chat_mode_var.get().strip().lower() == "private"
        if is_private and self._private_chat_target() is None:
            can_chat = False

        self.send_button.configure(state="normal" if can_chat else "disabled")
        self.chat_entry.configure(state="normal" if can_chat else "disabled")

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

        recipient: str | None = None
        selected_tab = self._selected_chat_tab_name()
        if selected_tab != "Public":
            recipient = selected_tab
            self.chat_mode_var.set("Private")
            self.chat_target_var.set(selected_tab)
        elif self.chat_mode_var.get().strip().lower() == "private":
            target = self._private_chat_target()
            if target is None:
                messagebox.showwarning("Private Chat", "Choose or type a valid private recipient first.")
                return
            recipient = target
            self._ensure_chat_tab(target)
            self._select_chat_tab(target)

        self.connection.send_message(make_chat_message(sender=self.username, message=text, recipient=recipient))
        self.chat_var.set("")

    def _on_close(self) -> None:
        """Window close callback for clean socket shutdown."""

        self._disconnect()
        self.root.destroy()

    def run(self) -> None:
        """Start Tk main loop."""

        self.root.mainloop()


def main(
    *,
    initial_username: str | None = None,
    server_ip: str | None = None,
    server_port: int | None = None,
    auto_connect: bool = False,
    login_gate_enabled: bool = False,
) -> None:
    """Module entrypoint for launching GUI client."""

    app = ArenaGuiApp(
        initial_username=initial_username,
        server_ip=server_ip,
        server_port=server_port,
        auto_connect=auto_connect,
        login_gate_enabled=login_gate_enabled,
    )
    app.run()


if __name__ == "__main__":
    main()
