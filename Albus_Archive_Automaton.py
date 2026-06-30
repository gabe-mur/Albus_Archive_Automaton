"""Tkinter desktop app for queueing and downloading archival media.

The app is intentionally self-contained: Tkinter owns the UI, yt-dlp/gallery-dl
handle site-specific downloads, and ffmpeg handles media compatibility work.
"""

import os
import json
import queue
import re
import shutil
import signal
import ssl
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import traceback
import html
import imageio_ffmpeg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urljoin, urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


class QueuedMediaDownloader:
    """Queue-driven media downloader with video, audio-only, and image modes."""

    WINDOW_TITLE = "Albus’ Archive Automaton"
    WINDOW_GEOMETRY = "860x940"
    MIN_WINDOW_SIZE = (760, 700)

    HEADER_TITLE = "Albus’ Archive Automaton"
    HEADER_BYLINE = "by gabe murray"
    HEADER_INSTRUCTIONS = "Copy in a URL, pick a save location, and then start the queue!"
    HEADER_BACKGROUND_FILE = Path("Assets") / "Albus Background.png"
    HEADER_HEIGHT = 166
    HEADER_BACKGROUND_OVERSCAN = 1.0
    HEADER_BACKGROUND_FOCUS_X = 0.68
    HEADER_BACKGROUND_FOCUS_Y = 0.58
    HEADER_BACKGROUND_FIT_MODE = "cover"
    APP_BACKGROUND_COLOR = "#0a1724"
    PANEL_BACKGROUND_COLOR = "#2d4352"
    FIELD_BACKGROUND_COLOR = "#203443"
    PANEL_TEXT_COLOR = "#e8edf1"
    PANEL_MUTED_TEXT_COLOR = "#c1cad2"
    PANEL_TITLE_COLOR = "#f2eadc"
    PANEL_BORDER_COLOR = "#607685"
    BUTTON_BACKGROUND_COLOR = "#5c6675"
    BUTTON_ACTIVE_BACKGROUND_COLOR = "#6d7a8c"
    BUTTON_TEXT_COLOR = APP_BACKGROUND_COLOR
    PROGRESS_TRACK_COLOR = "#203443"
    PROGRESS_FILL_COLOR = "#7f9fb4"
    SECTION_TITLE_FONT = ("Luminari", 16)
    BUTTON_FONT = ("Luminari", 13)
    APP_STATE_FILE = Path(".albus_archive_automaton_state.json")
    QUEUE_DONE_ROW_COLORS = {"background": "#263f32", "foreground": "#ecf4ee"}
    QUEUE_FAILED_ROW_COLORS = {"background": "#523030", "foreground": "#f7eeee"}

    SYSTEM_SOUND_DIR = Path("/System/Library/Sounds")
    COMPLETION_SOUND_NAMES = ("Blow.aiff", "Glass.aiff")
    COMPLETION_SOUND_VOLUME = 0.6

    VIDEO_EXTENSIONS = (".mp4", ".m3u8", ".mpd", ".mov", ".webm")
    AUDIO_EXTENSIONS = (".m4a", ".mp3", ".aac", ".flac", ".wav", ".ogg", ".oga", ".opus")
    IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif")

    def __init__(self, root):
        self.root = root
        self.root.title(self.WINDOW_TITLE)
        self.root.geometry(self.WINDOW_GEOMETRY)

        self.url = tk.StringVar()
        self.custom_name = tk.StringVar()
        self.media_mode = tk.StringVar(value="video")
        self.output_folder = tk.StringVar()
        self.use_browser_cookies = tk.BooleanVar(value=False)
        self.scan_direct_media = tk.BooleanVar(value=True)
        self.auto_start = tk.BooleanVar(value=False)
        self.audio_lossless_wav = tk.BooleanVar(value=False)
        self.browser_name = tk.StringVar(value="Firefox")
        self.ding_on_complete = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="")
        self.progress_status = tk.StringVar(value="Idle")
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_percent = tk.StringVar(value="")

        self.items = []
        self.current_index = 0
        self.running = False
        self.stop_after_current = False
        self.cancel_current = False
        self.active_process = None
        self.active_process_lock = threading.Lock()
        self.active_log_item = None
        self.log_queue = queue.Queue()
        self.entry_undo_states = {}
        self.advanced_window = None
        self.browser_combo = None
        self.logo_image = None
        self.header_canvas = None
        self.header_background_source = None
        self.header_background_image = None
        self.header_background_render_key = None

        self.load_app_state()
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)
        self.root.after(100, self.process_log_queue)

    # UI construction -----------------------------------------------------

    def build_ui(self):
        self.root.minsize(*self.MIN_WINDOW_SIZE)
        self.configure_styles()
        self.root.configure(background=self.APP_BACKGROUND_COLOR)

        app_body = tk.Frame(self.root, background=self.APP_BACKGROUND_COLOR)
        app_body.pack(fill="both", expand=True)

        self.add_header(app_body)

        main = tk.Frame(app_body, background=self.APP_BACKGROUND_COLOR, padx=14, pady=0)
        main.pack(fill="both", expand=True)

        source_frame = self.create_section(main, "Source", fill="x", pady=(0, 10))

        mode_row = self.create_panel_row(source_frame)
        mode_row.pack(fill="x", padx=8, pady=(8, 6))

        tk.Radiobutton(
            mode_row,
            text="Video",
            variable=self.media_mode,
            value="video",
            **self.panel_radio_options()
        ).pack(side="left")

        tk.Radiobutton(
            mode_row,
            text="Audio Only",
            variable=self.media_mode,
            value="audio",
            **self.panel_radio_options()
        ).pack(side="left", padx=(12, 0))

        tk.Radiobutton(
            mode_row,
            text="Images",
            variable=self.media_mode,
            value="images",
            **self.panel_radio_options()
        ).pack(side="left", padx=(12, 0))

        self.create_panel_label(source_frame, "Media URL:").pack(anchor="w", padx=8)
        self.url_entry = self.create_panel_entry(source_frame, self.url)
        self.url_entry.pack(fill="x", padx=8, pady=(2, 8))

        self.create_panel_label(source_frame, "Save as, optional - no extension needed:").pack(anchor="w", padx=8)
        self.name_entry = self.create_panel_entry(source_frame, self.custom_name)
        self.name_entry.pack(fill="x", padx=8, pady=(2, 8))
        self.setup_entry_undo(self.url_entry, self.url)
        self.setup_entry_undo(self.name_entry, self.custom_name)

        add_row = self.create_panel_row(source_frame)
        add_row.pack(fill="x", padx=8, pady=(0, 8))

        self.add_button = self.create_action_button(add_row, text="Add to Queue", command=self.add_to_queue)
        self.add_button.pack(side="left")
        self.create_action_button(
            add_row,
            text="Advanced Options",
            command=self.open_advanced_options
        ).pack(side="left", padx=(8, 0))

        output_frame = self.create_section(main, "Output", fill="x", pady=(0, 10))

        folder_row = self.create_panel_row(output_frame)
        folder_row.pack(fill="x", padx=8, pady=8)

        self.output_entry = self.create_panel_entry(folder_row, self.output_folder)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.create_action_button(folder_row, text="Choose Folder", command=self.choose_folder).pack(side="left", padx=(8, 0))

        queue_frame = self.create_section(main, "Queue", fill="both", expand=True, pady=(0, 10))

        queue_table_frame = self.create_panel_row(queue_frame)
        queue_table_frame.pack(fill="both", expand=True, padx=8, pady=(8, 6))

        columns = ("status", "type", "name", "url")
        self.queue_list = ttk.Treeview(
            queue_table_frame,
            columns=columns,
            show="headings",
            height=8,
            selectmode="extended"
        )
        self.queue_list.heading("status", text="Status")
        self.queue_list.heading("type", text="Type")
        self.queue_list.heading("name", text="Name")
        self.queue_list.heading("url", text="URL")
        self.queue_list.column("status", width=110, minwidth=90, stretch=False)
        self.queue_list.column("type", width=80, minwidth=70, stretch=False)
        self.queue_list.column("name", width=190, minwidth=120)
        self.queue_list.column("url", width=420, minwidth=180)
        self.queue_list.tag_configure("done", **self.QUEUE_DONE_ROW_COLORS)
        self.queue_list.tag_configure("failed", **self.QUEUE_FAILED_ROW_COLORS)

        queue_scrollbar = ttk.Scrollbar(queue_table_frame, orient="vertical", command=self.queue_list.yview)
        self.queue_list.configure(yscrollcommand=queue_scrollbar.set)
        self.queue_list.pack(side="left", fill="both", expand=True)
        queue_scrollbar.pack(side="right", fill="y")
        self.queue_list.bind("<Double-1>", self.open_queue_item_file)
        self.queue_list.bind("<Button-2>", self.show_queue_context_menu)
        self.queue_list.bind("<Button-3>", self.show_queue_context_menu)
        self.queue_list.bind("<<TreeviewSelect>>", lambda event: self.update_button_states())

        queue_help_row = self.create_panel_row(queue_frame)
        queue_help_row.pack(fill="x", padx=8, pady=(0, 8))
        self.create_panel_label(
            queue_help_row,
            "Right-click an item for file actions and logs.",
            muted=True
        ).pack(side="left")
        self.clear_button = self.create_action_button(queue_help_row, text="Clear Queue", command=self.clear_queue)
        self.clear_button.pack(side="right", padx=(0, 8))
        self.remove_button = self.create_action_button(queue_help_row, text="Remove Selected", command=self.remove_selected)
        self.remove_button.pack(side="right", padx=(0, 8))

        progress_frame = self.create_section(main, "Progress", fill="x", pady=(0, 10))

        progress_row = self.create_panel_row(progress_frame)
        progress_row.pack(fill="x", padx=8, pady=(8, 4))
        self.progress = self.create_progress_bar(progress_row)
        self.progress.pack(fill="x", expand=True)

        progress_status_row = self.create_panel_row(progress_frame)
        progress_status_row.pack(fill="x", padx=8, pady=(0, 8))
        self.create_panel_label(progress_status_row, variable=self.progress_status).pack(
            side="left",
            anchor="w",
            fill="x",
            expand=True
        )
        self.create_panel_label(
            progress_status_row,
            variable=self.progress_percent,
            width=7,
            anchor="e"
        ).pack(side="right")

        control_row = tk.Frame(main, background=self.APP_BACKGROUND_COLOR)
        control_row.pack(fill="x", padx=(18, 0), pady=(0, 8))

        self.start_button = self.create_action_button(
            control_row,
            text="Start Queue",
            command=self.start_queue
        )
        self.start_button.pack(side="left")

        self.stop_button = self.create_action_button(
            control_row,
            text="Stop After Current",
            state="disabled",
            command=self.stop_queue
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        self.cancel_button = self.create_action_button(
            control_row,
            text="Cancel Current",
            state="disabled",
            command=self.cancel_current_download
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.output_folder.trace_add("write", lambda *_: self.update_button_states())
        self.refresh_queue_list()
        self.update_button_states()
        self.root.bind("<ButtonRelease-1>", self.clear_queue_selection_outside_queue, add="+")

    def create_section(self, parent, title, fill="x", expand=False, pady=(0, 10)):
        section = tk.Frame(parent, background=self.APP_BACKGROUND_COLOR)
        section.pack(fill=fill, expand=expand, pady=pady)

        tk.Label(
            section,
            text=title,
            background=self.APP_BACKGROUND_COLOR,
            foreground=self.PANEL_TITLE_COLOR,
            font=self.SECTION_TITLE_FONT
        ).pack(anchor="w", padx=(14, 0), pady=(0, 3))

        content = tk.Frame(
            section,
            background=self.PANEL_BACKGROUND_COLOR,
            highlightbackground=self.PANEL_BORDER_COLOR,
            highlightcolor=self.PANEL_BORDER_COLOR,
            highlightthickness=1,
            borderwidth=0,
            padx=10,
            pady=8
        )
        content.pack(fill=fill, expand=expand)
        return content

    def create_panel_row(self, parent):
        return tk.Frame(parent, background=self.PANEL_BACKGROUND_COLOR)

    def create_panel_label(self, parent, text=None, variable=None, muted=False, width=None, anchor="w"):
        options = {
            "background": self.PANEL_BACKGROUND_COLOR,
            "foreground": self.PANEL_MUTED_TEXT_COLOR if muted else self.PANEL_TEXT_COLOR,
            "anchor": anchor,
        }
        if text is not None:
            options["text"] = text
        if variable is not None:
            options["textvariable"] = variable
        if width is not None:
            options["width"] = width

        return tk.Label(parent, **options)

    def create_panel_entry(self, parent, variable):
        return tk.Entry(
            parent,
            textvariable=variable,
            background=self.FIELD_BACKGROUND_COLOR,
            foreground=self.PANEL_TEXT_COLOR,
            insertbackground=self.PANEL_TEXT_COLOR,
            disabledbackground=self.FIELD_BACKGROUND_COLOR,
            disabledforeground=self.PANEL_MUTED_TEXT_COLOR,
            highlightbackground=self.PANEL_BORDER_COLOR,
            highlightcolor=self.PROGRESS_FILL_COLOR,
            highlightthickness=1,
            relief="flat",
            borderwidth=0
        )

    def create_progress_bar(self, parent):
        canvas = tk.Canvas(
            parent,
            width=200,
            height=12,
            background=self.PANEL_BACKGROUND_COLOR,
            highlightthickness=0,
            borderwidth=0
        )
        canvas.bind("<Configure>", lambda event: self.update_progress_bar())
        self.progress_track = None
        self.progress_fill = None
        return canvas

    def update_progress_bar(self):
        if not hasattr(self, "progress") or self.progress is None:
            return

        width = max(1, self.progress.winfo_width())
        try:
            configured_width = int(float(self.progress.cget("width")))
        except (tk.TclError, TypeError, ValueError):
            configured_width = 1
        if width <= 1 and configured_width > 1:
            width = configured_width
        height = max(1, self.progress.winfo_height())
        percent = min(100, max(0, float(self.progress_value.get())))
        fill_width = max(0, int(width * percent / 100))

        self.progress.delete("all")
        self.progress.create_rectangle(
            0,
            2,
            width,
            max(3, height - 2),
            fill=self.PROGRESS_TRACK_COLOR,
            outline=""
        )
        if fill_width > 0:
            self.progress.create_rectangle(
                0,
                2,
                fill_width,
                max(3, height - 2),
                fill=self.PROGRESS_FILL_COLOR,
                outline=""
            )

    def create_action_button(self, parent, text, command=None, state="normal"):
        return tk.Button(
            parent,
            text=text,
            command=command,
            state=state,
            background=self.BUTTON_BACKGROUND_COLOR,
            foreground=self.BUTTON_TEXT_COLOR,
            activebackground=self.BUTTON_ACTIVE_BACKGROUND_COLOR,
            activeforeground=self.BUTTON_TEXT_COLOR,
            disabledforeground="#2f3944",
            highlightbackground=self.BUTTON_BACKGROUND_COLOR,
            highlightcolor=self.BUTTON_BACKGROUND_COLOR,
            font=self.BUTTON_FONT,
            relief="raised",
            borderwidth=1,
            padx=14,
            pady=3
        )

    def panel_radio_options(self):
        return {
            "background": self.PANEL_BACKGROUND_COLOR,
            "foreground": self.PANEL_TEXT_COLOR,
            "activebackground": self.PANEL_BACKGROUND_COLOR,
            "activeforeground": self.PANEL_TEXT_COLOR,
            "selectcolor": self.PANEL_BACKGROUND_COLOR,
            "highlightthickness": 0,
            "borderwidth": 0,
        }

    def configure_styles(self):
        style = ttk.Style()
        style.configure("App.TFrame", background=self.APP_BACKGROUND_COLOR)
        style.configure("Panel.TFrame", background=self.PANEL_BACKGROUND_COLOR)
        style.configure("App.TLabel", background=self.APP_BACKGROUND_COLOR, foreground=self.PANEL_TEXT_COLOR)
        style.configure("Muted.App.TLabel", background=self.APP_BACKGROUND_COLOR, foreground=self.PANEL_MUTED_TEXT_COLOR)
        style.configure("Panel.TLabel", background=self.PANEL_BACKGROUND_COLOR, foreground=self.PANEL_TEXT_COLOR)
        style.configure(
            "Muted.Panel.TLabel",
            background=self.PANEL_BACKGROUND_COLOR,
            foreground=self.PANEL_MUTED_TEXT_COLOR
        )
        style.configure(
            "Floating.TLabelframe",
            background=self.PANEL_BACKGROUND_COLOR,
            bordercolor=self.PANEL_BORDER_COLOR,
            relief="solid"
        )
        style.configure(
            "Floating.TLabelframe.Label",
            background=self.APP_BACKGROUND_COLOR,
            foreground=self.PANEL_TITLE_COLOR,
            font=self.SECTION_TITLE_FONT
        )
        style.configure(
            "Panel.TRadiobutton",
            background=self.PANEL_BACKGROUND_COLOR,
            foreground=self.PANEL_TEXT_COLOR
        )
        style.map(
            "Panel.TRadiobutton",
            background=[("active", self.PANEL_BACKGROUND_COLOR)],
            foreground=[("active", self.PANEL_TEXT_COLOR)]
        )

    def add_header(self, parent):
        self.header_background_source = self.load_header_background_source()
        self.header_canvas = tk.Canvas(
            parent,
            height=self.HEADER_HEIGHT,
            highlightthickness=0,
            borderwidth=0,
            background=self.APP_BACKGROUND_COLOR
        )
        self.header_canvas.pack(fill="x", pady=(0, 10))
        self.header_canvas.bind("<Configure>", self.draw_header)

    def load_header_background_source(self):
        background_path = Path(__file__).resolve().parent / self.HEADER_BACKGROUND_FILE
        if not background_path.exists():
            return None

        try:
            if Image is not None:
                return Image.open(background_path).convert("RGB")

            return tk.PhotoImage(file=str(background_path))
        except Exception:
            return None

    def draw_header(self, event=None):
        if self.header_canvas is None:
            return

        width = max(1, event.width if event is not None else self.header_canvas.winfo_width())
        height = self.HEADER_HEIGHT
        self.header_canvas.delete("all")
        self.draw_header_background(width, height)

        title_x = 16
        instructions_y = height - 18
        title_y = 47
        byline_y = 91
        shadow = "#111820"
        title_fill = "#f3eee2"
        byline_fill = "#c9c1b1"
        instructions_fill = "#d7d0c3"

        self.header_canvas.create_text(
            title_x + 2,
            title_y + 2,
            text=self.HEADER_TITLE,
            font=("Luminari", 36),
            fill=shadow,
            anchor="w"
        )
        self.header_canvas.create_text(
            title_x,
            title_y,
            text=self.HEADER_TITLE,
            font=("Luminari", 36),
            fill=title_fill,
            anchor="w"
        )
        self.header_canvas.create_text(
            title_x + 1,
            byline_y + 1,
            text=self.HEADER_BYLINE,
            font=("Luminari", 13),
            fill=shadow,
            anchor="w"
        )
        self.header_canvas.create_text(
            title_x,
            byline_y,
            text=self.HEADER_BYLINE,
            font=("Luminari", 13),
            fill=byline_fill,
            anchor="w"
        )
        self.header_canvas.create_text(
            title_x + 1,
            instructions_y + 1,
            text=self.HEADER_INSTRUCTIONS,
            font=("Luminari", 13),
            fill=shadow,
            anchor="w"
        )
        self.header_canvas.create_text(
            title_x,
            instructions_y,
            text=self.HEADER_INSTRUCTIONS,
            font=("Luminari", 13),
            fill=instructions_fill,
            anchor="w"
        )

    def draw_header_background(self, width, height):
        if self.header_background_source is None:
            return

        if Image is not None and ImageTk is not None and isinstance(self.header_background_source, Image.Image):
            render_key = (width, height)
            if self.header_background_render_key != render_key:
                self.header_background_image = self.render_header_background(width, height)
                self.header_background_render_key = render_key

            if self.header_background_image is not None:
                self.header_canvas.create_image(width // 2, height // 2, image=self.header_background_image, anchor="center")
            return

        source = self.header_background_source
        scale = max(
            1,
            min(
                source.width() // max(1, width),
                source.height() // max(1, height)
            )
        )
        self.header_background_image = source.subsample(scale, scale)
        self.header_canvas.create_image(width // 2, height // 2, image=self.header_background_image, anchor="center")

    def render_header_background(self, width, height):
        source = self.header_background_source
        target_width = max(1, int(width * self.HEADER_BACKGROUND_OVERSCAN))
        target_height = max(1, int(height * self.HEADER_BACKGROUND_OVERSCAN))
        if self.HEADER_BACKGROUND_FIT_MODE == "contain":
            scale = min(target_width / source.width, target_height / source.height)
        else:
            scale = max(target_width / source.width, target_height / source.height)
        resized_size = (
            max(1, int(source.width * scale)),
            max(1, int(source.height * scale))
        )
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        resized = source.resize(resized_size, resampling)
        if self.HEADER_BACKGROUND_FIT_MODE == "contain":
            canvas = Image.new("RGB", (width, height), self.APP_BACKGROUND_COLOR)
            left = max(0, (width - resized.width) // 2)
            top = max(0, height - resized.height)
            canvas.paste(resized, (left, top))
            return ImageTk.PhotoImage(canvas)

        max_left = max(0, resized.width - width)
        max_top = max(0, resized.height - height)
        focus_x = int(resized.width * self.HEADER_BACKGROUND_FOCUS_X)
        focus_y = int(resized.height * self.HEADER_BACKGROUND_FOCUS_Y)
        left = min(max_left, max(0, focus_x - width // 2))
        top = min(max_top, max(0, focus_y - height // 2))
        cropped = resized.crop((left, top, left + width, top + height))
        return ImageTk.PhotoImage(cropped)

    # App state persistence ----------------------------------------------

    def app_state_path(self):
        return Path(__file__).resolve().parent / self.APP_STATE_FILE

    def load_app_state(self):
        path = self.app_state_path()
        if not path.exists():
            return

        try:
            with path.open("r", encoding="utf-8") as file:
                state = json.load(file)
        except Exception:
            return

        if not isinstance(state, dict):
            return

        settings = state.get("settings", {})
        if isinstance(settings, dict):
            self.apply_saved_settings(settings)

        self.items = self.valid_saved_items(state.get("items", []))
        self.current_index = self.first_runnable_queue_index()

        geometry = state.get("geometry")
        if self.valid_geometry(geometry):
            self.root.geometry(geometry)

    def apply_saved_settings(self, settings):
        string_settings = {
            "url": self.url,
            "custom_name": self.custom_name,
            "output_folder": self.output_folder,
            "browser_name": self.browser_name,
        }
        for key, variable in string_settings.items():
            value = settings.get(key)
            if isinstance(value, str):
                variable.set(value)

        mode = settings.get("media_mode")
        if mode in {"video", "audio", "images"}:
            self.media_mode.set(mode)

        browser = settings.get("browser_name")
        if browser in {"Firefox", "Chrome", "Safari"}:
            self.browser_name.set(browser)

        boolean_settings = {
            "use_browser_cookies": self.use_browser_cookies,
            "scan_direct_media": self.scan_direct_media,
            "auto_start": self.auto_start,
            "audio_lossless_wav": self.audio_lossless_wav,
            "ding_on_complete": self.ding_on_complete,
        }
        for key, variable in boolean_settings.items():
            value = settings.get(key)
            if isinstance(value, bool):
                variable.set(value)

    def valid_saved_items(self, items):
        if not isinstance(items, list):
            return []

        saved_items = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue

            mode = item.get("mode") if item.get("mode") in {"video", "audio", "images"} else "video"
            status = item.get("status") if isinstance(item.get("status"), str) else "Queued"
            if status == "Downloading":
                status = "Queued"

            saved_items.append({
                "url": item["url"],
                "name": item.get("name", "") if isinstance(item.get("name", ""), str) else "",
                "mode": mode,
                "status": status,
                "cookies_browser": item.get("cookies_browser") if isinstance(item.get("cookies_browser"), str) else None,
                "scan_direct_media": item.get("scan_direct_media", True) is not False,
                "log": item.get("log", []) if isinstance(item.get("log"), list) else [],
                "failure_detail": item.get("failure_detail", "") if isinstance(item.get("failure_detail", ""), str) else "",
                "saved_paths": item.get("saved_paths", []) if isinstance(item.get("saved_paths", []), list) else [],
            })

        return saved_items

    def first_runnable_queue_index(self):
        for index, item in enumerate(self.items):
            if item.get("status") == "Queued":
                return index

        return len(self.items)

    def valid_geometry(self, geometry):
        if not isinstance(geometry, str):
            return False

        return re.match(r"^\d+x\d+(?:[+-]\d+){0,2}$", geometry) is not None

    def save_app_state(self):
        state = {
            "geometry": self.root.geometry(),
            "settings": {
                "url": self.url.get(),
                "custom_name": self.custom_name.get(),
                "media_mode": self.media_mode.get(),
                "output_folder": self.output_folder.get(),
                "use_browser_cookies": self.use_browser_cookies.get(),
                "scan_direct_media": self.scan_direct_media.get(),
                "auto_start": self.auto_start.get(),
                "audio_lossless_wav": self.audio_lossless_wav.get(),
                "browser_name": self.browser_name.get(),
                "ding_on_complete": self.ding_on_complete.get(),
            },
            "items": self.items,
        }

        path = self.app_state_path()
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(state, file, indent=2)
            temp_path.replace(path)
        except Exception:
            pass

    def on_app_close(self):
        if self.running:
            should_quit = messagebox.askyesno(
                "Quit while downloading?",
                "A download is still running. Quit and stop the current download?"
            )
            if not should_quit:
                return

            self.cancel_current = True
            self.terminate_active_process()

        self.save_app_state()
        self.root.destroy()

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose save location")
        if folder:
            self.output_folder.set(folder)
            self.update_button_states()
            if self.auto_start.get() and self.items and not self.running and self.current_index < len(self.items):
                self.start_queue()

    def open_advanced_options(self):
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            self.advanced_window.lift()
            self.advanced_window.focus_set()
            return

        window = tk.Toplevel(self.root)
        window.title("Advanced Options")
        window.resizable(False, False)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self.close_advanced_options)
        self.advanced_window = window

        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)

        ttk.Checkbutton(
            body,
            text="Start automatically when a save location is ready",
            variable=self.auto_start,
            command=self.update_button_states
        ).pack(anchor="w", pady=(0, 6))

        ttk.Checkbutton(
            body,
            text="If yt-dlp fails, scan page for direct media URLs",
            variable=self.scan_direct_media
        ).pack(anchor="w", pady=(0, 6))

        ttk.Checkbutton(
            body,
            text="Save Audio Only as WAV",
            variable=self.audio_lossless_wav
        ).pack(anchor="w", pady=(0, 6))

        ttk.Checkbutton(
            body,
            text="Use browser cookies / logged-in session",
            variable=self.use_browser_cookies,
            command=self.update_browser_state
        ).pack(anchor="w", pady=(0, 6))

        browser_row = ttk.Frame(body)
        browser_row.pack(fill="x", pady=(0, 6))

        ttk.Label(browser_row, text="Browser:").pack(side="left")
        self.browser_combo = ttk.Combobox(
            browser_row,
            textvariable=self.browser_name,
            values=("Firefox", "Chrome", "Safari"),
            state="readonly",
            width=12
        )
        self.browser_combo.pack(side="left", padx=(6, 0))

        ttk.Label(
            body,
            text="Use only for sites you're authorized to access. Cookies are not saved by this app.",
            foreground="gray60",
            wraplength=420
        ).pack(anchor="w", pady=(0, 10))

        ttk.Checkbutton(
            body,
            text="Ding when finished",
            variable=self.ding_on_complete
        ).pack(anchor="w")

        button_row = ttk.Frame(body)
        button_row.pack(fill="x", pady=(14, 0))
        ttk.Button(button_row, text="Done", command=self.close_advanced_options).pack(side="right")

        self.update_browser_state()
        window.update_idletasks()
        self.center_child_window(window)

    def close_advanced_options(self):
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            self.advanced_window.destroy()
        self.advanced_window = None
        self.browser_combo = None

    def center_child_window(self, window):
        self.root.update_idletasks()
        window.update_idletasks()

        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()
        width = window.winfo_width()
        height = window.winfo_height()

        x = parent_x + max(0, (parent_width - width) // 2)
        y = parent_y + max(0, (parent_height - height) // 3)
        window.geometry(f"+{x}+{y}")

    def update_browser_state(self):
        if self.browser_combo is None:
            return

        try:
            if self.use_browser_cookies.get():
                self.browser_combo.config(state="readonly")
            else:
                self.browser_combo.config(state="disabled")
        except tk.TclError:
            self.browser_combo = None

    # UI state helpers ----------------------------------------------------

    def clear_queue_selection_outside_queue(self, event):
        if not hasattr(self, "queue_list") or not self.queue_list.selection():
            return

        widget = event.widget
        while widget is not None:
            if widget is self.queue_list:
                return
            try:
                widget = widget.master
            except AttributeError:
                break

        self.queue_list.selection_remove(self.queue_list.selection())
        self.queue_list.focus("")
        self.update_button_states()

    def update_button_states(self):
        selected = self.selected_queue_indices()
        can_start = (
            bool(self.items)
            and bool(self.output_folder.get().strip())
            and not self.running
            and self.current_index < len(self.items)
        )

        if hasattr(self, "start_button"):
            self.start_button.config(state="normal" if can_start else "disabled")
        if hasattr(self, "stop_button"):
            self.stop_button.config(state="normal" if self.running else "disabled")
        if hasattr(self, "cancel_button"):
            self.cancel_button.config(
                state="normal" if self.running and self.active_log_item is not None else "disabled"
            )
        if hasattr(self, "remove_button"):
            self.remove_button.config(state="normal" if selected else "disabled")
        if hasattr(self, "clear_button"):
            self.clear_button.config(state="normal" if self.items and not self.running else "disabled")

    def selected_cookies_browser(self):
        if not self.use_browser_cookies.get():
            return None

        browser = self.browser_name.get().strip().lower()
        if browser in {"firefox", "chrome", "safari"}:
            return browser

        return "firefox"

    # Entry undo support --------------------------------------------------

    def setup_entry_undo(self, entry, variable):
        self.entry_undo_states[entry] = {
            "history": [variable.get()],
            "index": 0,
            "after_id": None,
            "applying": False
        }

        for sequence in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<<Clear>>"):
            entry.bind(
                sequence,
                lambda event, e=entry, v=variable: self.schedule_entry_undo_snapshot(e, v, event),
                add="+"
            )

        for sequence in ("<Command-z>", "<Command-Z>", "<Control-z>", "<Control-Z>"):
            entry.bind(
                sequence,
                lambda event, e=entry, v=variable: self.undo_entry_edit(e, v),
                add="+"
            )

    def schedule_entry_undo_snapshot(self, entry, variable, event=None):
        state = self.entry_undo_states.get(entry)
        if not state or state.get("applying"):
            return None

        ignored_keys = {
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Command", "Meta_L",
            "Meta_R", "Alt_L", "Alt_R", "Caps_Lock", "Left", "Right", "Up",
            "Down", "Home", "End", "Tab", "Escape"
        }
        if event and getattr(event, "keysym", None) in ignored_keys:
            return None

        after_id = state.get("after_id")
        if after_id:
            self.root.after_cancel(after_id)

        state["after_id"] = self.root.after(
            120,
            lambda: self.record_entry_undo_snapshot(entry, variable)
        )
        return None

    def record_entry_undo_snapshot(self, entry, variable):
        state = self.entry_undo_states.get(entry)
        if not state:
            return

        state["after_id"] = None
        current_value = variable.get()
        history = state["history"]
        index = state["index"]

        if history and history[index] == current_value:
            return

        del history[index + 1:]
        history.append(current_value)
        if len(history) > 100:
            del history[0]

        state["index"] = len(history) - 1

    def undo_entry_edit(self, entry, variable):
        state = self.entry_undo_states.get(entry)
        if not state:
            return "break"

        after_id = state.get("after_id")
        if after_id:
            self.root.after_cancel(after_id)
            self.record_entry_undo_snapshot(entry, variable)

        if state["index"] <= 0:
            return "break"

        state["index"] -= 1
        state["applying"] = True
        try:
            variable.set(state["history"][state["index"]])
            entry.icursor("end")
            entry.selection_clear()
        finally:
            state["applying"] = False

        return "break"

    def reset_entry_undo_history(self, entry, variable):
        state = self.entry_undo_states.get(entry)
        if not state:
            return

        after_id = state.get("after_id")
        if after_id:
            self.root.after_cancel(after_id)

        state["history"] = [variable.get()]
        state["index"] = 0
        state["after_id"] = None
        state["applying"] = False

    # Queue management ----------------------------------------------------

    def add_to_queue(self):
        url = self.url.get().strip()
        name = self.custom_name.get().strip()

        if not url:
            messagebox.showerror("Missing URL", "Paste a media URL first.")
            return

        item = {
            "url": url,
            "name": name,
            "mode": self.media_mode.get(),
            "status": "Queued",
            "cookies_browser": self.selected_cookies_browser(),
            "scan_direct_media": self.scan_direct_media.get(),
            "log": [],
            "failure_detail": "",
            "saved_paths": []
        }

        self.items.append(item)
        self.refresh_queue_list()

        self.url.set("")
        self.custom_name.set("")
        self.reset_entry_undo_history(self.url_entry, self.url)
        self.reset_entry_undo_history(self.name_entry, self.custom_name)

        if self.running:
            self.status.set(f"Added to queue. {len(self.items)} item(s) total.")
        elif self.auto_start.get() and self.output_folder.get().strip():
            self.start_queue()
        elif self.output_folder.get().strip():
            self.status.set("Added to queue. Press Start Queue when ready.")
        else:
            self.status.set("Added to queue. Choose a save location to start downloading.")
        self.update_button_states()

    def remove_selected(self):
        selected = self.selected_queue_indices()

        if self.running:
            removable = [
                index for index in selected
                if index > self.current_index and self.items[index].get("status") == "Queued"
            ]
            blocked = len(selected) - len(removable)

            if not removable:
                messagebox.showinfo(
                    "Queue running",
                    "Only queued items that have not started can be removed while the queue is running."
                )
                return

            for index in reversed(removable):
                del self.items[index]

            self.refresh_queue_list()
            if blocked:
                self.status.set("Removed queued item(s). Active or finished items were left in the queue.")
            else:
                self.status.set("Removed queued item(s).")
            self.update_button_states()
            return

        for index in reversed(selected):
            del self.items[index]

        self.refresh_queue_list()
        self.update_button_states()

    def clear_queue(self):
        if self.running:
            messagebox.showinfo(
                "Queue running",
                "Clearing while running is not supported. Use Stop After Current first."
            )
            return

        self.items.clear()
        self.current_index = 0
        self.refresh_queue_list()
        self.update_button_states()

    def refresh_queue_list(self):
        children = self.queue_list.get_children()
        if children:
            self.queue_list.delete(*children)

        for index, item in enumerate(self.items):
            display_name = item["name"] if item["name"] else "(use media title)"
            mode = self.media_mode_label(item.get("mode", "video"))
            row_tags = self.queue_row_tags(item)
            self.queue_list.insert(
                "",
                "end",
                iid=str(index),
                values=(item["status"], mode, display_name, item["url"]),
                tags=row_tags
            )
        self.update_button_states()

    def queue_row_tags(self, item):
        status = item.get("status")
        if status == "Done":
            return ("done",)
        if status == "Failed":
            return ("failed",)

        return ()

    def selected_queue_indices(self):
        if not hasattr(self, "queue_list"):
            return []

        indices = []
        for item_id in self.queue_list.selection():
            try:
                index = int(item_id)
            except ValueError:
                continue
            if 0 <= index < len(self.items):
                indices.append(index)

        return sorted(indices)

    def media_mode_label(self, mode):
        labels = {
            "video": "Video",
            "audio": "Audio Only",
            "images": "Images",
        }
        return labels.get(mode, str(mode).title())

    def start_queue(self):
        folder = self.output_folder.get().strip()

        if not self.items:
            messagebox.showerror("Empty queue", "Add at least one media URL first.")
            return

        if not folder:
            messagebox.showerror("Missing folder", "Choose a save location first.")
            return

        if self.running:
            return

        if self.current_index >= len(self.items):
            self.status.set("Queue is already finished. Add another media URL to continue.")
            return

        Path(folder).mkdir(parents=True, exist_ok=True)

        self.running = True
        self.stop_after_current = False
        self.cancel_current = False
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.cancel_button.config(state="normal")
        self.progress_status.set("Starting queue...")
        self.progress_value.set(0)
        self.progress_percent.set("0%")

        thread = threading.Thread(target=self.run_queue, daemon=True)
        thread.start()

    def stop_queue(self):
        self.stop_after_current = True
        self.status.set("Will stop after the current item finishes.")
        self.progress_status.set("Will stop after current item finishes")

    def cancel_current_download(self):
        if not self.running or self.active_log_item is None:
            self.status.set("No active download to cancel.")
            return

        if not messagebox.askyesno("Cancel Current", "Cancel the current item and delete any partial output?"):
            return

        self.cancel_current = True
        self.status.set("Canceling current item...")
        self.progress_status.set("Canceling current item...")
        self.log_message("Cancel requested by user.")
        self.terminate_active_process()

    # Queue execution -----------------------------------------------------

    def run_queue(self):
        """Process queued items on a worker thread and marshal UI updates safely."""
        try:
            folder = self.output_folder.get().strip()

            while self.current_index < len(self.items):
                item = self.items[self.current_index]

                self.cancel_current = False
                item["status"] = "Downloading"
                item["log"] = []
                item["failure_detail"] = ""
                item["saved_paths"] = []
                self.active_log_item = item
                self.safe_refresh()
                self.safe_status(f"Downloading {self.current_index + 1} of {len(self.items)}")
                self.safe_progress_status(f"Downloading item {self.current_index + 1} of {len(self.items)}")
                self.safe_progress_value(0)

                self.log_message("")
                self.log_message("=" * 70)
                self.log_message(f"Starting item {self.current_index + 1} of {len(self.items)}")
                self.log_message(f"Mode: {self.media_mode_label(item.get('mode', 'video'))}")
                self.log_message(item["url"])
                if item["name"]:
                    self.log_message(f"Save as: {item['name']}")
                self.log_message("=" * 70)

                try:
                    saved_paths = self.download_one(
                        item["url"],
                        folder,
                        item["name"],
                        item.get("mode", "video"),
                        item.get("cookies_browser"),
                        item.get("scan_direct_media", True)
                    )
                    success = bool(saved_paths)
                    item["saved_paths"] = [str(path) for path in saved_paths] if success else []
                except Exception as e:
                    success = False
                    item["saved_paths"] = []
                    item["failure_detail"] = str(e)
                    self.log_message("")
                    self.log_message(f"Item failed: {e}")
                    self.log_message("")
                    self.log_message("Traceback:")
                    for line in traceback.format_exc().rstrip().splitlines():
                        self.log_message(line)

                if self.cancel_current:
                    self.delete_saved_paths(item.get("saved_paths", []))
                    item["saved_paths"] = []
                    item["failure_detail"] = "Canceled by user."
                    item["status"] = "Canceled"
                    self.log_message("Item canceled. Partial output was removed.")
                    self.safe_status("Canceled current item.")
                    self.safe_progress_status("Canceled current item")
                    self.safe_progress_value(0)
                else:
                    item["status"] = "Done" if success else "Failed"
                    if success:
                        self.safe_progress_value(100)
                    else:
                        self.safe_progress_value(0)
                if not success and not item.get("failure_detail"):
                    item["failure_detail"] = self.last_error_from_log(item)
                self.safe_refresh()
                self.active_log_item = None

                self.current_index += 1

                if self.stop_after_current:
                    self.safe_status("Stopped after current item.")
                    self.safe_progress_status("Stopped")
                    self.safe_progress_value(0)
                    break

            if self.current_index >= len(self.items):
                self.safe_status("Queue finished.")
                self.safe_progress_status("Finished")
                self.root.after(0, self.play_queue_finished_sound)

        except Exception as e:
            self.safe_status("Error.")
            self.safe_progress_status("Error")
            self.safe_progress_value(0)
            self.log_message(str(e))

        finally:
            self.active_log_item = None
            self.running = False
            self.stop_after_current = False
            self.root.after(0, lambda: self.start_button.config(state="normal"))
            self.root.after(0, lambda: self.stop_button.config(state="disabled"))
            self.root.after(0, lambda: self.cancel_button.config(state="disabled"))
            self.root.after(0, self.update_button_states)

    # Mode dispatch and download flows -----------------------------------

    def download_one(self, url, folder, custom_name, mode="video", cookies_browser=None, scan_direct_media=True):
        """Dispatch one queued URL to the selected download mode."""
        if mode == "images":
            return self.download_images(url, folder, custom_name, cookies_browser)
        if mode == "audio":
            return self.download_audio(url, folder, custom_name, cookies_browser, allow_discovery=scan_direct_media)

        return self.download_video(url, folder, custom_name, cookies_browser, allow_discovery=scan_direct_media)

    def download_audio(self, url, folder, custom_name, cookies_browser=None, allow_discovery=True):
        """Download the best available audio, defaulting to MP3 unless WAV is selected."""
        ffmpeg_path = self.get_ffmpeg_path()

        self.log_message(f"Using ffmpeg:")
        self.log_message(ffmpeg_path)
        if cookies_browser:
            self.log_message(f"Using browser cookies from: {cookies_browser.title()}")
        self.log_message("")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            audio_file = self.download_best_audio(url, temp_dir, ffmpeg_path, cookies_browser)
            if self.cancel_current:
                return False

            if not audio_file:
                self.log_message("Audio download failed.")
                if allow_discovery and not self.is_direct_media_url(url):
                    return self.try_discovered_direct_media(url, folder, custom_name, cookies_browser, mode="audio")
                return False

            final_path = self.final_audio_output_path(folder, audio_file, custom_name)
            shutil.move(str(audio_file), str(final_path))
            self.log_message("")
            self.log_message(f"Saved audio to: {final_path}")
            return [final_path]

    def download_video(self, url, folder, custom_name, cookies_browser=None, allow_discovery=True):
        """Download video as a QuickTime-friendly MP4, converting when necessary."""
        ffmpeg_path = self.get_ffmpeg_path()

        self.log_message(f"Using ffmpeg:")
        self.log_message(ffmpeg_path)
        if cookies_browser:
            self.log_message(f"Using browser cookies from: {cookies_browser.title()}")
        self.log_message("")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)

            # First try: 1080p-or-lower H.264 MP4 video + M4A/AAC audio.
            # This avoids conversion when the site offers a compatible file.
            compatible_file = self.try_compatible_download(url, temp_dir, ffmpeg_path, cookies_browser)
            if self.cancel_current:
                return False

            if compatible_file and self.is_quicktime_friendly(compatible_file, ffmpeg_path):
                final_path = self.final_output_path(folder, compatible_file, custom_name)
                shutil.move(str(compatible_file), str(final_path))
                self.log_message("")
                self.log_message("Already QuickTime-friendly. Conversion skipped.")
                self.log_message(f"Saved to: {final_path}")
                return [final_path]

            self.log_message("")
            self.log_message("Compatible 1080p MP4 was not available or not valid.")
            self.log_message("Downloading best available source and converting...")

            best_file = self.download_best_source(url, temp_dir, ffmpeg_path, cookies_browser)
            if self.cancel_current:
                return False

            if not best_file:
                self.log_message("Best-source download failed.")
                if allow_discovery and not self.is_direct_media_url(url):
                    return self.try_discovered_direct_media(url, folder, custom_name, cookies_browser, mode="video")
                return False

            final_path = self.final_output_path(folder, best_file, custom_name)

            if self.is_quicktime_friendly(best_file, ffmpeg_path):
                shutil.move(str(best_file), str(final_path))
                self.log_message("")
                self.log_message("Downloaded file was already compatible. Conversion skipped.")
                self.log_message(f"Saved to: {final_path}")
                return [final_path]

            return self.convert_to_compatible_mp4(best_file, final_path, ffmpeg_path)

    # Direct media discovery fallback ------------------------------------

    def try_discovered_direct_media(self, page_url, folder, custom_name, cookies_browser=None, mode="video"):
        """Try direct media URLs scraped from a page after yt-dlp fails."""
        self.log_message("")
        self.log_message("yt-dlp failed. Scanning this page once for direct media URLs...")
        self.safe_progress_status("Scanning page for direct media URLs...")
        if cookies_browser:
            self.log_message("Browser cookies apply only to yt-dlp for now; the page scanner will not read browser cookies.")

        candidates = self.discover_direct_media_urls(page_url, mode=mode)
        self.log_message(f"Found {len(candidates)} candidate direct media URL(s).")

        for index, candidate in enumerate(candidates, start=1):
            media_type = self.media_url_extension(candidate).upper().lstrip(".") or "MEDIA"
            self.log_message(f"{index}. [{media_type}] {candidate}")

        for index, candidate in enumerate(candidates, start=1):
            self.log_message("")
            self.log_message(f"Trying discovered candidate {index} of {len(candidates)}:")
            self.log_message(candidate)
            self.safe_progress_status(f"Trying discovered media URL {index} of {len(candidates)}...")

            if self.cancel_current:
                return False

            if mode == "audio":
                saved_paths = self.download_audio(candidate, folder, custom_name, cookies_browser, allow_discovery=False)
            else:
                saved_paths = self.download_video(candidate, folder, custom_name, cookies_browser, allow_discovery=False)
            if saved_paths:
                self.log_message("")
                self.log_message("Downloaded using discovered direct media URL.")
                return saved_paths

        if candidates:
            self.log_message("All discovered direct media URL candidates failed.")

        return False

    def discover_direct_media_urls(self, page_url, mode="video"):
        """Fetch one page and extract direct media links without crawling."""
        try:
            request = Request(
                page_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": page_url,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            with urlopen(request, timeout=20, context=self.page_scan_ssl_context()) as response:
                raw = response.read(2_000_000)
                content_type = response.headers.get_content_charset()
        except URLError as e:
            self.log_message(f"Page scan failed: {e}")
            if self.is_ssl_certificate_error(e):
                self.log_message("Python could not verify the site's HTTPS certificate.")
                self.log_message("On python.org macOS installs, run the bundled 'Install Certificates.command' if this persists.")
            return []
        except Exception as e:
            self.log_message(f"Page scan failed: {e}")
            return []

        encoding = content_type or "utf-8"
        text = raw.decode(encoding, errors="replace")
        title = self.extract_page_title(text)
        candidates = self.extract_media_urls_from_text(text, base_url=page_url, mode=mode)
        return self.rank_media_candidates(candidates, page_url=page_url, page_title=title, mode=mode)

    def page_scan_ssl_context(self):
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    def is_ssl_certificate_error(self, error):
        reason = getattr(error, "reason", error)
        return isinstance(reason, ssl.SSLCertVerificationError)

    def extract_media_urls_from_text(self, text, base_url=None, mode="video"):
        media_extensions = "(?:" + "|".join(
            extension.lstrip(".")
            for extension in self.direct_media_extensions(mode)
        ) + ")"
        seen = set()
        candidates = []

        variants = [
            text,
            html.unescape(text),
            text.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/"),
            unquote(text),
        ]

        normalized_variants = []
        for variant in variants:
            normalized = html.unescape(variant)
            normalized = normalized.replace("\\/", "/")
            normalized = normalized.replace("\\u002F", "/").replace("\\u002f", "/")
            normalized_variants.append(normalized)
            decoded = unquote(normalized)
            if decoded != normalized:
                normalized_variants.append(decoded)

        absolute_pattern = re.compile(
            rf"https?://[^\s\"'<>]+?\.{media_extensions}(?:\?[^\s\"'<>]*)?",
            re.IGNORECASE
        )
        relative_pattern = re.compile(
            rf"(?:(?:src|href|url|file|source)\s*[:=]\s*)?[\"']?"
            rf"((?:/|\./|\.\./)[^\s\"'<>]+?\.{media_extensions}(?:\?[^\s\"'<>]*)?)",
            re.IGNORECASE
        )

        for variant in normalized_variants:
            for match in absolute_pattern.finditer(variant):
                self.add_media_candidate(candidates, seen, match.group(0), base_url, mode=mode)

            for match in relative_pattern.finditer(variant):
                self.add_media_candidate(candidates, seen, match.group(1), base_url, mode=mode)

        return candidates

    def add_media_candidate(self, candidates, seen, url, base_url=None, mode="video"):
        url = self.clean_media_url(url)
        if base_url:
            url = urljoin(base_url, url)

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return

        if not self.is_direct_media_url(url, mode=mode):
            return

        key = url
        if key in seen:
            return

        seen.add(key)
        candidates.append(url)

    def clean_media_url(self, url):
        url = html.unescape(url.strip())
        url = url.replace("\\/", "/")
        url = url.replace("\\u002F", "/").replace("\\u002f", "/")
        url = unquote(url)

        trailing_junk = "\"'\\<>,})];"
        while url and url[-1] in trailing_junk:
            url = url[:-1]

        return url

    def rank_media_candidates(self, candidates, page_url=None, page_title=None, mode="video"):
        keywords = self.page_keywords(page_url, page_title)

        def score(candidate):
            parsed = urlparse(candidate)
            path = parsed.path.lower()
            full_url = candidate.lower()
            extension = self.media_url_extension(candidate)

            if mode == "audio":
                type_rank = {
                    ".m4a": 0,
                    ".mp3": 1,
                    ".aac": 2,
                    ".flac": 3,
                    ".wav": 4,
                    ".ogg": 5,
                    ".oga": 5,
                    ".opus": 6,
                    ".mp4": 7,
                    ".m3u8": 8,
                    ".mpd": 9,
                    ".mov": 10,
                    ".webm": 10,
                }.get(extension, 99)
            else:
                type_rank = {
                    ".mp4": 0,
                    ".m3u8": 1,
                    ".mpd": 2,
                    ".mov": 3,
                    ".webm": 3,
                }.get(extension, 9)

            tracker_words = ("ad", "ads", "doubleclick", "googleads", "tracking", "beacon", "analytics", "preroll")
            tracker_penalty = 10 if any(word in full_url for word in tracker_words) else 0
            keyword_bonus = -1 if any(keyword in path for keyword in keywords) else 0
            scheme_penalty = 1 if parsed.scheme == "http" else 0

            return (type_rank + tracker_penalty + keyword_bonus + scheme_penalty, len(candidate), candidate)

        return sorted(candidates, key=score)

    def page_keywords(self, page_url=None, page_title=None):
        source = " ".join(part for part in [page_url or "", page_title or ""] if part)
        words = re.findall(r"[a-z0-9]{4,}", source.lower())
        skip_words = {
            "http", "https", "www", "html", "video", "videos", "watch",
            "news", "article", "page", "index", "com", "net", "org"
        }
        return [word for word in words if word not in skip_words][:12]

    def extract_page_title(self, text):
        match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""

        title = re.sub(r"\s+", " ", match.group(1))
        return html.unescape(title).strip()

    def is_direct_media_url(self, url, mode="any"):
        return self.media_url_extension(url) in self.direct_media_extensions(mode)

    def media_url_extension(self, url):
        path = urlparse(url).path.lower()
        for extension in self.direct_media_extensions("any"):
            if path.endswith(extension):
                return extension

        return ""

    def direct_media_extensions(self, mode="any"):
        if mode == "video":
            return self.VIDEO_EXTENSIONS
        if mode == "audio":
            return self.AUDIO_EXTENSIONS + self.VIDEO_EXTENSIONS

        return self.AUDIO_EXTENSIONS + self.VIDEO_EXTENSIONS

    # Tool-specific download commands ------------------------------------

    def download_images(self, url, folder, custom_name, cookies_browser=None):
        self.log_message("Using gallery-dl for image download.")
        self.safe_progress_status("Downloading images...")
        if cookies_browser:
            self.log_message(f"Using browser cookies from: {cookies_browser.title()}")
        self.log_message("")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir = Path(temp_dir)
            command = self.get_gallery_dl_command()
            command.append("--verbose")
            command.extend(["--dest", str(temp_dir)])
            command = self.add_gallery_cookie_options(command, cookies_browser)
            command.append(url)

            self.log_message("Downloading images...")
            returncode = self.run_command_live(command, progress_label="Downloading images")
            if self.cancel_current:
                return False

            if returncode != 0:
                self.log_message(f"gallery-dl exited with code {returncode}.")
                self.log_message("If gallery-dl is not installed, run: python3 -m pip install gallery-dl")
                self.log_message("Right-click this queue item and choose View Log to review the full gallery-dl diagnostics.")

            image_files = self.find_downloaded_images(temp_dir)
            if not image_files:
                self.log_message("No downloaded image files were found.")
                self.log_message("The site may have rejected the request, the cookies may be unavailable, or gallery-dl may not support this URL.")
                return False

            multiple = len(image_files) > 1
            base_name = self.image_base_name(image_files, custom_name, multiple, temp_dir)

            saved_paths = []
            for index, image_file in enumerate(image_files, start=1):
                final_path = self.final_image_output_path(
                    folder,
                    image_file,
                    custom_name,
                    base_name,
                    index if multiple else None
                )
                shutil.move(str(image_file), str(final_path))
                saved_paths.append(final_path)

            self.log_message("")
            self.log_message(f"Saved {len(saved_paths)} image(s):")
            for path in saved_paths:
                self.log_message(str(path))

            return saved_paths

    def get_gallery_dl_command(self):
        gallery_dl = shutil.which("gallery-dl")
        if gallery_dl:
            return [gallery_dl]

        return ["python3", "-m", "gallery_dl"]

    def add_gallery_cookie_options(self, command, cookies_browser):
        if cookies_browser:
            command.extend(["--cookies-from-browser", cookies_browser])

        return command

    def find_downloaded_images(self, temp_dir):
        files = [
            p for p in temp_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in self.IMAGE_EXTENSIONS
        ]

        return sorted(files, key=lambda p: str(p.relative_to(temp_dir)).lower())

    def image_base_name(self, image_files, custom_name, multiple, temp_dir):
        if custom_name:
            return self.safe_filename(custom_name)

        if multiple:
            common_parent = image_files[0].parent
            if common_parent != temp_dir and all(image_file.parent == common_parent for image_file in image_files):
                parent_name = self.safe_filename(common_parent.name)
                if parent_name and parent_name != "downloaded_media":
                    return parent_name

        return self.safe_filename(image_files[0].stem)

    def try_compatible_download(self, url, temp_dir, ffmpeg_path, cookies_browser=None):
        output_template = str(temp_dir / "%(title).180B [%(id)s].compatible.%(ext)s")

        command = [
            "python3",
            "-m",
            "yt_dlp",

            # First priority: 1080p-or-lower H.264 MP4 + M4A/AAC.
            "-f",
            "bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[ext=mp4]",

            "--ffmpeg-location", ffmpeg_path,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", output_template,
        ]

        command = self.add_cookie_options(command, cookies_browser)
        command.append(url)

        self.log_message("Trying direct compatible MP4 download...")
        self.safe_progress_status("Downloading compatible media...")
        returncode = self.run_command_live(command, progress_label="Downloading compatible media")

        if returncode != 0:
            self.log_message(f"Compatible download attempt exited with code {returncode}.")

        files = [
            p for p in temp_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".mp4"
        ]

        if not files:
            return None

        return max(files, key=lambda p: p.stat().st_size)

    def download_best_source(self, url, temp_dir, ffmpeg_path, cookies_browser=None):
        output_template = str(temp_dir / "%(title).180B [%(id)s].source.%(ext)s")

        command = [
            "python3",
            "-m",
            "yt_dlp",

            # Best available video + audio, up to 1080p.
            # Change height<=1080 to height<=2160 if you ever want 4K.
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",

            "--ffmpeg-location", ffmpeg_path,
            "--merge-output-format", "mkv",
            "--no-playlist",
            "-o", output_template,
        ]

        command = self.add_cookie_options(command, cookies_browser)
        command.append(url)

        self.log_message("Downloading best available source...")
        self.safe_progress_status("Downloading best available media...")
        returncode = self.run_command_live(command, progress_label="Downloading best available media")

        if returncode != 0:
            self.log_message(f"Best-source download exited with code {returncode}.")

        files = [
            p for p in temp_dir.iterdir()
            if p.is_file() and p.suffix.lower() in [".mkv", ".webm", ".mp4", ".mov", ".m4v"]
        ]

        if not files:
            return None

        return max(files, key=lambda p: p.stat().st_size)

    def download_best_audio(self, url, temp_dir, ffmpeg_path, cookies_browser=None):
        output_template = str(temp_dir / "%(title).180B [%(id)s].audio.%(ext)s")
        audio_format = self.selected_audio_format()
        audio_quality = self.selected_audio_quality(audio_format)

        command = [
            "python3",
            "-m",
            "yt_dlp",

            # Prefer the source's best audio stream, or fall back to best media
            # so video-only/direct-media links can still produce audio.
            "-f", "ba/bestaudio/best",
            "--extract-audio",
            "--audio-format", audio_format,
            "--audio-quality", audio_quality,
            "--ffmpeg-location", ffmpeg_path,
            "--no-playlist",
            "-o", output_template,
        ]

        command = self.add_cookie_options(command, cookies_browser)
        command.append(url)

        self.log_message("Downloading best available audio...")
        self.safe_progress_status("Downloading audio...")
        returncode = self.run_command_live(command, progress_label="Downloading audio")

        if returncode != 0:
            self.log_message(f"Audio download exited with code {returncode}.")

        files = [
            p for p in temp_dir.iterdir()
            if p.is_file() and p.suffix.lower() in self.audio_file_extensions()
        ]

        if not files:
            return None

        return max(files, key=lambda p: p.stat().st_size)

    def add_cookie_options(self, command, cookies_browser):
        if cookies_browser:
            command.extend(["--cookies-from-browser", cookies_browser])

        return command

    def audio_file_extensions(self):
        return set(self.AUDIO_EXTENSIONS)

    def selected_audio_format(self):
        return "wav" if self.audio_lossless_wav.get() else "mp3"

    def selected_audio_quality(self, audio_format):
        if audio_format == "wav":
            return "0"

        return "320K"

    def convert_to_compatible_mp4(self, source_file, final_path, ffmpeg_path):
        temp_output = final_path.with_suffix(".tmp.mp4")

        temp_output.unlink(missing_ok=True)

        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-nostdin",
            "-loglevel", "warning",
            "-stats",
            "-stats_period", "10",
            "-i", str(source_file),

            # Mac hardware H.264 encoder. Much faster than libx264.
            "-c:v", "h264_videotoolbox",
            "-b:v", "18000k",

            # QuickTime/Premiere-friendly audio.
            "-c:a", "aac",
            "-b:a", "192k",

            "-movflags", "+faststart",

            str(temp_output)
        ]

        self.log_message("")
        self.log_message("Converting to QuickTime-friendly MP4...")
        self.log_message(f"Output: {final_path}")
        self.safe_progress_status("Converting video...")
        source_duration = self.media_duration_seconds(source_file, ffmpeg_path)
        returncode = self.run_command_live(
            command,
            compact_ffmpeg=True,
            progress_label="Converting video",
            source_duration=source_duration
        )
        if self.cancel_current:
            temp_output.unlink(missing_ok=True)
            return False

        if returncode == 0 and temp_output.exists() and temp_output.stat().st_size > 1024:
            temp_output.rename(final_path)
            self.log_message("")
            self.log_message(f"Saved to: {final_path}")
            return [final_path]

        self.log_message("")
        self.log_message("Conversion failed or produced a tiny file.")
        temp_output.unlink(missing_ok=True)
        return False

    # Process control and completion sound --------------------------------

    def terminate_active_process(self):
        with self.active_process_lock:
            process = self.active_process

        if process is None or process.poll() is not None:
            return

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                return

        threading.Thread(target=self.force_kill_active_process_after_delay, args=(process,), daemon=True).start()

    def force_kill_active_process_after_delay(self, process):
        time.sleep(3)
        if process.poll() is not None:
            return

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def delete_saved_paths(self, paths):
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception as e:
                self.log_message(f"Could not delete canceled output {path}: {e}")

    def play_queue_finished_sound(self):
        if not self.ding_on_complete.get():
            return

        self.play_completion_sound()

    def play_completion_sound(self):
        sound_path = self.completion_sound_path()
        afplay = shutil.which("afplay")
        if not sound_path or not afplay:
            self.ring_system_bell()
            return

        threading.Thread(
            target=self.play_sound_file,
            args=(afplay, sound_path),
            daemon=True
        ).start()

    def completion_sound_path(self):
        for sound_name in self.COMPLETION_SOUND_NAMES:
            sound_path = self.SYSTEM_SOUND_DIR / sound_name
            if sound_path.exists():
                return sound_path

        return None

    def play_sound_file(self, afplay, sound_path):
        try:
            subprocess.run(
                [afplay, "-v", str(self.COMPLETION_SOUND_VOLUME), str(sound_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            self.ring_system_bell()

    def ring_system_bell(self):
        try:
            self.root.after(0, self.root.bell)
        except Exception:
            pass

    # Command output and progress parsing ---------------------------------

    def run_command_live(self, command, compact_ffmpeg=False, progress_label=None, source_duration=None):
        """Run a subprocess, capture its logs, and update progress from output."""
        self.log_message(" ".join(command))
        self.log_message("")

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True
        )

        with self.active_process_lock:
            self.active_process = process

        try:
            if self.cancel_current:
                self.terminate_active_process()

            last_ffmpeg_update = 0
            active_progress_label = progress_label
            for line in process.stdout:
                clean_line = line.replace("\r", "\n").strip()
                if not clean_line:
                    continue

                if compact_ffmpeg and "frame=" in clean_line:
                    now = time.monotonic()
                    percent = self.progress_percent_from_ffmpeg_line(clean_line, source_duration)
                    if percent is not None:
                        self.safe_progress_value(percent)
                    progress_text = self.progress_from_ffmpeg_line(clean_line, progress_label, source_duration)
                    if progress_text:
                        self.safe_progress_status(progress_text)
                    if now - last_ffmpeg_update >= 10:
                        self.log_message(self.compact_ffmpeg_line(clean_line))
                        last_ffmpeg_update = now
                    continue

                detected_label = self.progress_label_from_tool_line(clean_line, progress_label)
                if detected_label:
                    active_progress_label = detected_label

                percent = self.progress_percent_from_status(clean_line)
                if percent is not None:
                    self.safe_progress_value(percent)

                progress_text = self.progress_from_tool_line(clean_line, active_progress_label)
                if progress_text:
                    self.safe_progress_status(progress_text)

                self.log_message(clean_line)

            process.wait()
            return process.returncode
        finally:
            with self.active_process_lock:
                if self.active_process is process:
                    self.active_process = None

    def progress_from_tool_line(self, line, label=None):
        if not label:
            return None

        speed = re.search(r"\bat\s+([0-9.]+\s*[KMGTP]?i?B/s)", line, re.IGNORECASE)

        if speed:
            return f"{label}... {speed.group(1).replace(' ', '')}"

        if self.progress_percent_from_status(line) is not None:
            return f"{label}..."

        return None

    def progress_label_from_tool_line(self, line, fallback_label=None):
        lower_line = line.lower()

        if "[merger]" in lower_line or "merging formats" in lower_line:
            return "Merging audio and video"
        if "[download]" not in lower_line:
            return None

        destination_match = re.search(r"\b(?:destination|resuming download at byte|has already been downloaded):\s*(.+)$", line, re.IGNORECASE)
        if not destination_match:
            return None

        path_text = destination_match.group(1).strip()
        suffix = Path(path_text).suffix.lower()

        if suffix in {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav"}:
            return "Downloading audio"
        if suffix in {".mp4", ".mkv", ".mov", ".m4v", ".webm"}:
            return "Downloading video"

        return fallback_label

    def progress_from_ffmpeg_line(self, line, label=None, source_duration=None):
        if not label:
            return None

        speed_match = re.search(r"speed=\s*([0-9.]+)x", line)
        if speed_match:
            return f"{label}... speed {float(speed_match.group(1)):.2f}x"

        if self.progress_percent_from_ffmpeg_line(line, source_duration) is not None:
            return f"{label}..."

        return None

    def progress_percent_from_ffmpeg_line(self, line, source_duration=None):
        time_match = re.search(r"time=\s*([0-9:.]+)", line)
        if not time_match:
            return None

        elapsed = self.duration_string_to_seconds(time_match.group(1))
        if source_duration and source_duration > 0:
            return min(100, max(0, elapsed / source_duration * 100))

        return None

    def media_duration_seconds(self, file_path, ffmpeg_path):
        command = [
            ffmpeg_path,
            "-i", str(file_path)
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        match = re.search(r"Duration:\s*([0-9:.]+)", result.stdout)
        if not match:
            return None

        return self.duration_string_to_seconds(match.group(1))

    def duration_string_to_seconds(self, value):
        parts = value.split(":")
        if len(parts) != 3:
            return 0

        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    def compact_ffmpeg_line(self, line):
        frame = re.search(r"frame=\s*([0-9]+)", line)
        time_match = re.search(r"time=\s*([0-9:.]+)", line)
        speed = re.search(r"speed=\s*([0-9.]+x)", line)

        parts = ["Converting..."]
        if frame:
            parts.append(f"frame {frame.group(1)}")
        if time_match:
            parts.append(f"time {time_match.group(1)}")
        if speed:
            parts.append(f"speed {speed.group(1)}")

        return " ".join(parts)

    def is_quicktime_friendly(self, file_path, ffmpeg_path):
        command = [
            ffmpeg_path,
            "-i", str(file_path)
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        info = result.stdout.lower()

        is_mp4 = str(file_path).lower().endswith(".mp4")
        has_h264 = "video: h264" in info or "video: avc1" in info
        has_aac = "audio: aac" in info

        return is_mp4 and has_h264 and has_aac

    # Output naming and filesystem helpers --------------------------------

    def final_output_path(self, folder, source_file, custom_name):
        if custom_name:
            stem = self.safe_filename(custom_name)
        else:
            stem = self.safe_filename(source_file.stem)
            stem = re.sub(r"\s*\[[^\]]+\]\.(compatible|source)$", "", stem)
            stem = re.sub(r"\.(compatible|source)$", "", stem)

            # Remove generic temp names if possible.
            if stem in ["compatible", "best_source", "source"]:
                stem = "downloaded_media"

        return self.get_unique_output_path(folder, stem, ".mp4")

    def final_audio_output_path(self, folder, source_file, custom_name):
        if custom_name:
            stem = self.safe_filename(custom_name)
        else:
            stem = self.safe_filename(source_file.stem)
            stem = re.sub(r"\s*\[[^\]]+\]\.audio$", "", stem)
            stem = re.sub(r"\.audio$", "", stem)

            if stem in ["audio", "bestaudio", "best_audio"]:
                stem = "downloaded_audio"

        return self.get_unique_output_path(folder, stem, f".{self.selected_audio_format()}")

    def final_image_output_path(self, folder, source_file, custom_name, base_name, sequence_number=None):
        if sequence_number is None:
            if custom_name:
                stem = self.safe_filename(custom_name)
            else:
                stem = self.safe_filename(source_file.stem)
        else:
            stem = f"{self.safe_filename(base_name)}_{sequence_number:03d}"

        extension = source_file.suffix.lower()
        if not extension:
            extension = ".jpg"

        return self.get_unique_output_path(folder, stem, extension)

    def safe_filename(self, name):
        name = name.strip()
        name = re.sub(r"[\\/:*?\"<>|]", "-", name)
        name = re.sub(r"[\x00-\x1f]", "", name)
        name = re.sub(r"\s+", " ", name)
        name = name.strip(". ")

        if not name:
            name = "downloaded_media"

        return name[:180]

    def get_unique_output_path(self, folder, stem, extension):
        folder = Path(folder)
        output_path = folder / f"{stem}{extension}"

        if not output_path.exists():
            return output_path

        counter = 2
        while True:
            candidate = folder / f"{stem} ({counter}){extension}"
            if not candidate.exists():
                return candidate
            counter += 1

    # Tool lookup, logging, and item actions -------------------------------

    def get_ffmpeg_path(self):
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return system_ffmpeg

        common_paths = [
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
        ]

        for path in common_paths:
            if Path(path).exists():
                return path

        return imageio_ffmpeg.get_ffmpeg_exe()

    def log_message(self, message):
        if self.active_log_item is not None:
            self.active_log_item.setdefault("log", []).append(message)
        self.log_queue.put(("log", message))

    def last_error_from_log(self, item):
        error_words = ("error", "failed", "exception", "permission", "login", "cookies")
        for line in reversed(item.get("log", [])):
            if any(word in line.lower() for word in error_words):
                return line

        return "Download failed. Open the item log for details."

    # Queue context menu actions ------------------------------------------

    def open_queue_item_file(self, event):
        item_id = self.queue_list.identify_row(event.y)
        if not item_id:
            return

        try:
            index = int(item_id)
        except ValueError:
            return

        if 0 <= index < len(self.items):
            self.queue_list.selection_set(item_id)
            self.queue_list.focus(item_id)
            self.open_saved_files(index)

    def show_queue_context_menu(self, event):
        item_id = self.queue_list.identify_row(event.y)
        if not item_id:
            return

        try:
            index = int(item_id)
        except ValueError:
            return

        if index < 0 or index >= len(self.items):
            return

        self.queue_list.selection_set(item_id)
        self.queue_list.focus(item_id)

        item = self.items[index]
        saved_paths = self.existing_saved_paths(item)
        has_files = bool(saved_paths)
        plural = len(saved_paths) != 1
        is_failed = item.get("status") == "Failed"

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="Reset to Inputs",
            command=lambda: self.reset_failed_item_to_inputs(index),
            state="normal" if is_failed else "disabled"
        )
        menu.add_command(
            label="Copy Link",
            command=lambda: self.copy_item_link(index)
        )
        menu.add_separator()
        menu.add_command(
            label=f"Open {'Files' if plural else 'File'}",
            command=lambda: self.open_saved_files(index),
            state="normal" if has_files else "disabled"
        )
        menu.add_command(
            label="Reveal in Finder",
            command=lambda: self.reveal_saved_files(index),
            state="normal" if has_files else "disabled"
        )
        menu.add_command(
            label=f"Copy File {'Paths' if plural else 'Path'}",
            command=lambda: self.copy_saved_paths(index),
            state="normal" if has_files else "disabled"
        )
        menu.add_separator()
        menu.add_command(
            label="Move to Trash",
            command=lambda: self.trash_saved_files(index),
            state="normal" if has_files else "disabled"
        )
        menu.add_separator()
        menu.add_command(label="View Log", command=lambda: self.open_item_log(index))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def reset_failed_item_to_inputs(self, index):
        item = self.items[index]
        if item.get("status") != "Failed":
            self.status.set("Only failed items can be reset to the input fields.")
            return

        self.url.set(item.get("url", ""))
        self.custom_name.set(item.get("name", ""))
        self.media_mode.set(item.get("mode", "video"))
        self.scan_direct_media.set(item.get("scan_direct_media", True))

        cookies_browser = item.get("cookies_browser")
        self.use_browser_cookies.set(bool(cookies_browser))
        if cookies_browser:
            self.browser_name.set(cookies_browser.title())
        self.update_browser_state()
        self.update_button_states()

        self.reset_entry_undo_history(self.url_entry, self.url)
        self.reset_entry_undo_history(self.name_entry, self.custom_name)
        self.url_entry.focus_set()
        self.url_entry.selection_range(0, "end")
        self.status.set("Failed item restored to inputs. Edit the link, then add it to the queue.")

    def existing_saved_paths(self, item):
        return [
            Path(path)
            for path in item.get("saved_paths", [])
            if path and Path(path).exists()
        ]

    def copy_item_link(self, index):
        url = self.items[index].get("url", "")
        if not url:
            self.status.set("No link was found for this item.")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.status.set("Copied link.")

    def open_saved_files(self, index):
        saved_paths = self.existing_saved_paths(self.items[index])
        if not saved_paths:
            self.status.set("Saved file was not found.")
            return

        for path in saved_paths:
            subprocess.Popen(["open", str(path)])

        self.status.set(f"Opened {len(saved_paths)} file(s).")

    def reveal_saved_files(self, index):
        saved_paths = self.existing_saved_paths(self.items[index])
        if not saved_paths:
            self.status.set("Saved file was not found.")
            return

        subprocess.Popen(["open", "-R", *[str(path) for path in saved_paths]])
        self.status.set("Revealed in Finder.")

    def copy_saved_paths(self, index):
        saved_paths = self.existing_saved_paths(self.items[index])
        if not saved_paths:
            self.status.set("Saved file was not found.")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(str(path) for path in saved_paths))
        self.status.set(f"Copied {len(saved_paths)} file path(s).")

    def trash_saved_files(self, index):
        item = self.items[index]
        saved_paths = self.existing_saved_paths(item)
        if not saved_paths:
            self.status.set("Saved file was not found.")
            return

        noun = "file" if len(saved_paths) == 1 else f"{len(saved_paths)} files"
        if not messagebox.askyesno("Move to Trash", f"Move {noun} to Trash?"):
            return

        failed_paths = []
        for path in saved_paths:
            try:
                self.move_file_to_trash(path)
            except Exception as e:
                failed_paths.append((path, e))

        if failed_paths:
            first_path, error = failed_paths[0]
            messagebox.showerror("Move to Trash failed", f"Could not move to Trash:\n{first_path}\n\n{error}")
            self.status.set("Move to Trash failed.")
            return

        item["saved_paths"] = []
        item["status"] = "Moved to Trash"
        self.refresh_queue_list()
        self.status.set(f"Moved {noun} to Trash.")

    def move_file_to_trash(self, path):
        subprocess.run(
            [
                "osascript",
                "-e", "on run argv",
                "-e", 'tell application "Finder" to delete POSIX file (item 1 of argv)',
                "-e", "end run",
                str(path)
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

    def open_item_log(self, index):
        item = self.items[index]
        title = f"Download Log - Item {index + 1} [{item.get('status', 'Unknown')}]"

        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("900x520")

        header = tk.Frame(window)
        header.pack(fill="x", padx=10, pady=(10, 6))

        mode = self.media_mode_label(item.get("mode", "video"))
        tk.Label(
            header,
            text=f"Item {index + 1} - {mode} - {item.get('status', 'Unknown')}",
            font=("Arial", 12, "bold")
        ).pack(anchor="w")
        tk.Label(header, text=item.get("url", ""), wraplength=860, justify="left").pack(anchor="w", pady=(2, 0))

        failure_detail = item.get("failure_detail")
        if failure_detail:
            tk.Label(
                header,
                text=f"Last failure detail: {failure_detail}",
                wraplength=860,
                justify="left"
            ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        scrollbar = tk.Scrollbar(body)
        scrollbar.pack(side="right", fill="y")

        text = tk.Text(body, wrap="word", yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=text.yview)

        log_lines = item.get("log", [])
        if log_lines:
            text.insert("1.0", "\n".join(log_lines))
        else:
            text.insert("1.0", "No log has been recorded for this item yet.")

        text.config(state="disabled")

    # Thread-safe UI updates ----------------------------------------------

    def safe_status(self, message):
        self.log_queue.put(("status", message))

    def safe_progress_status(self, message):
        self.log_queue.put(("progress_status", message))

    def safe_progress_value(self, value):
        self.log_queue.put(("progress_value", value))

    def safe_refresh(self):
        self.log_queue.put(("refresh", None))

    def process_log_queue(self):
        """Apply worker-thread messages to Tk variables on the main thread."""
        try:
            while True:
                kind, value = self.log_queue.get_nowait()

                if kind == "log":
                    pass
                elif kind == "status":
                    self.status.set(value)
                elif kind == "progress_status":
                    self.progress_status.set(value)
                    percent = self.progress_percent_from_status(value)
                    if percent is not None:
                        self.set_progress_value(percent)
                elif kind == "progress_value":
                    self.set_progress_value(value)
                elif kind == "refresh":
                    self.refresh_queue_list()

        except queue.Empty:
            pass

        self.root.after(100, self.process_log_queue)

    def progress_percent_from_status(self, message):
        percent = re.search(r"(\d+(?:\.\d+)?)%", message)
        if not percent:
            return None

        return min(100, max(0, float(percent.group(1))))

    def set_progress_value(self, value):
        percent = min(100, max(0, float(value)))
        self.progress_value.set(percent)
        self.update_progress_bar()
        if percent.is_integer():
            self.progress_percent.set(f"{percent:.0f}%")
        else:
            self.progress_percent.set(f"{percent:.1f}%")


if __name__ == "__main__":
    root = tk.Tk()
    app = QueuedMediaDownloader(root)
    root.mainloop()
