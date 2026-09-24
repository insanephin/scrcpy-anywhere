from __future__ import annotations

import json
import itertools
import os
import platform
import queue
import select
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
import atexit
import base64
import struct
from pathlib import Path
from tkinter import Frame, PhotoImage, StringVar, Text, Tk
from tkinter import font as tkfont
from tkinter import ttk
from tkinter import messagebox

APP_NAME = "cloudflared adb scrcpy quick-connect"
APP_DIR = Path(__file__).resolve().parent
ICON_FILE = "scrcpy-anywhere.ico"
CONFIG_PATH = Path.home() / "adb-cloud-dashboard.json"
ADB_TUNNEL_MARKER = b"SCRCPY-ANYWHERE/1 ADB\n"
SCRCPY_TUNNEL_PREFIX = b"SCRCPY-ANYWHERE/1 SCRCPY "


def application_dir() -> Path:
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return APP_DIR


def bundled_resource(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", APP_DIR)) / name


def largest_png_in_ico(data: bytes) -> bytes | None:
    _reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1:
        return None
    best: tuple[int, bytes] | None = None
    for index in range(count):
        width, height, _colors, _reserved, _planes, _bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
        image = data[offset:offset + size]
        area = (width or 256) * (height or 256)
        if image.startswith(b"\x89PNG") and (best is None or area > best[0]):
            best = (area, image)
    return best[1] if best else None


THEME_MODES = ("auto", "light", "dark")
THEME_LABELS = {"auto": "Theme: Auto", "light": "Theme: Light", "dark": "Theme: Dark"}
PALETTES = {
    "light": {
        "bg": "#f7f7f8", "field": "#ffffff", "border": "#dcdfe4", "text": "#1c1e21", "muted": "#6b7078",
        "hover": "#eceef1", "accent": "#2563eb", "accent_hover": "#1d4ed8", "on_accent": "#ffffff",
        "success": "#16a34a", "danger": "#dc2626",
    },
    "dark": {
        "bg": "#18191c", "field": "#111214", "border": "#2f3136", "text": "#e4e6ea", "muted": "#9095a0",
        "hover": "#26282c", "accent": "#3b82f6", "accent_hover": "#60a5fa", "on_accent": "#ffffff",
        "success": "#22c55e", "danger": "#f87171",
    },
}


def system_prefers_dark() -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return winreg.QueryValueEx(handle, "AppsUseLightTheme")[0] == 0
        if system == "Darwin":
            result = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2)
            return result.stdout.strip().lower() == "dark"
        for key in ("color-scheme", "gtk-theme"):
            result = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key], capture_output=True, text=True, timeout=2)
            if "dark" in result.stdout.lower():
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def tool_storage_dir() -> Path:
    return application_dir() / "tools"


def log_file_path() -> Path:
    return application_dir() / "logs" / "connector.log"


TOOLS_DIR = tool_storage_dir()
GITHUB_RELEASE = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
PLATFORM_TOOLS = {
    "Windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
}
CLOUDFLARED = {
    ("Windows", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    ("Windows", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-arm64.exe",
    ("Darwin", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    ("Darwin", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz",
    ("Linux", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("Linux", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
}


SYSTEM = platform.system()
USER_AGENT = {"User-Agent": "adb-cloud-dashboard"}
# Console-subsystem tools (cloudflared, adb, scrcpy) would otherwise open a console window on Windows.
NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if SYSTEM == "Windows" else 0


def normalized_machine() -> str:
    value = platform.machine().lower()
    return "arm64" if value in {"arm64", "aarch64"} else "x86_64"


def executable_name(name: str) -> str:
    return f"{name}.exe" if SYSTEM == "Windows" else name


def copy_stream(source, target: Path):
    with source, target.open("wb") as output:
        shutil.copyfileobj(source, output)


class ProcessSupervisor:
    def __init__(self):
        self.processes: list[subprocess.Popen] = []
        self.job = None
        if SYSTEM == "Windows":
            self._create_windows_job()
        atexit.register(self.stop_all)

    def _create_windows_job(self):
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())

        info = (ctypes.c_byte * 144)()
        ctypes.c_uint32.from_buffer(info, 16).value = 0x00002000
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self.job = job

    @staticmethod
    def _linux_parent_death_signal():
        import ctypes
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)

    def spawn(self, command: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
        options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace", env=env)
        if SYSTEM == "Windows":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW_FLAG
        elif SYSTEM == "Linux":
            options["preexec_fn"] = self._linux_parent_death_signal
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
        if self.job is not None:
            from ctypes import wintypes
            # Best effort: without the job, stop_all/atexit still terminates the child.
            self._kernel32.AssignProcessToJobObject(self.job, wintypes.HANDLE(process._handle))
        self.processes.append(process)
        return process

    def stop(self, process: subprocess.Popen | None):
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()
        if process in self.processes:
            self.processes.remove(process)

    def stop_all(self):
        for process in self.processes[:]:
            self.stop(process)


class TaggedTcpProxy:
    """Add an explicit route marker before relaying a local TCP connection."""

    def __init__(self, upstream_port: int, marker, log=None):
        self.upstream_port = upstream_port
        self.marker = marker
        self.log = log or (lambda _message: None)
        self.stop_event = threading.Event()
        self.connections: set[socket.socket] = set()
        self.connections_lock = threading.Lock()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._accept_connections, daemon=True)
        self.thread.start()

    def _accept_connections(self):
        while not self.stop_event.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            marker = self.marker() if callable(self.marker) else self.marker
            threading.Thread(target=self._relay, args=(client, marker), daemon=True).start()

    def _relay(self, client: socket.socket, marker: bytes):
        upstream = None
        connected = False
        try:
            upstream = socket.create_connection(("127.0.0.1", self.upstream_port), timeout=10)
            upstream.sendall(marker)
            upstream.settimeout(None)
            connected = True
            with self.connections_lock:
                self.connections.update((client, upstream))
            while not self.stop_event.is_set():
                readable, _, _ = select.select((client, upstream), (), (), 0.5)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = upstream if source is client else client
                    destination.sendall(data)
        except OSError as exc:
            if not self.stop_event.is_set() and not connected:
                self.log(f"Tunnel proxy could not connect: {exc}")
        finally:
            with self.connections_lock:
                self.connections.discard(client)
                if upstream is not None:
                    self.connections.discard(upstream)
            client.close()
            if upstream is not None:
                upstream.close()

    def stop(self):
        self.stop_event.set()
        self.listener.close()
        with self.connections_lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(timeout=2)


class ScrcpyTaggedTcpProxy(TaggedTcpProxy):
    """Tag scrcpy sockets with a session and their local acceptance order."""

    def __init__(self, upstream_port: int, log=None):
        self.session = secrets.token_hex(8).encode("ascii")
        self.sequence = itertools.count()
        super().__init__(upstream_port, self._next_marker, log)

    def _next_marker(self) -> bytes:
        return SCRCPY_TUNNEL_PREFIX + self.session + b" " + str(next(self.sequence)).encode("ascii") + b"\n"


class ToolManager:
    def __init__(self, log):
        self.log = log
        self.system = SYSTEM
        self.machine = normalized_machine()
        self.storage_dir = TOOLS_DIR

    def path(self, tool: str) -> Path:
        if tool == "scrcpy" and self.system == "Linux":
            installed = shutil.which("scrcpy")
            if installed:
                return Path(installed)
        return self.storage_dir / executable_name(tool)

    def download(self, url: str, target: Path):
        self.log(f"Downloading: {url}")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers=USER_AGENT)
        try:
            copy_stream(urllib.request.urlopen(request, timeout=60), partial)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def make_executable(self, path: Path):
        if self.system != "Windows":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def extract_zip_folder(self, archive: Path, tool: str):
        """Flatten the archive folder that contains the tool binary into the storage dir."""
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            binary = next(n for n in names if n.endswith("/" + executable_name(tool)))
            prefix = binary.rsplit("/", 1)[0] + "/"
            for name in names:
                if name.startswith(prefix) and not name.endswith("/"):
                    copy_stream(zf.open(name), self.storage_dir / Path(name).name)

    def install_platform_tools(self):
        archive = self.storage_dir / "platform-tools.zip"
        self.download(PLATFORM_TOOLS[self.system], archive)
        self.extract_zip_folder(archive, "adb")
        archive.unlink(missing_ok=True)
        self.make_executable(self.path("adb"))

    def install_cloudflared(self):
        try:
            url = CLOUDFLARED[(self.system, self.machine)]
        except KeyError as exc:
            raise RuntimeError(f"Unsupported platform: {self.system} {self.machine}") from exc
        target = self.path("cloudflared")
        if url.endswith(".tgz"):
            archive = self.storage_dir / "cloudflared.tgz"
            self.download(url, archive)
            with tarfile.open(archive, "r:gz") as tf:
                binary = next(m for m in tf.getmembers() if m.isfile() and Path(m.name).name == "cloudflared")
                copy_stream(tf.extractfile(binary), target)
            archive.unlink(missing_ok=True)
        else:
            self.download(url, target)
        self.make_executable(target)

    def install_scrcpy(self):
        if self.system == "Linux":
            raise RuntimeError("Install the scrcpy package on Linux, then try again. (Ubuntu/Debian: sudo apt install scrcpy)")
        with urllib.request.urlopen(urllib.request.Request(GITHUB_RELEASE, headers=USER_AGENT), timeout=30) as response:
            assets = json.load(response).get("assets", [])
        marker = "win64" if self.system == "Windows" else ("macos-aarch64" if self.machine == "arm64" else "macos-x86_64")
        asset = next((a for a in assets if marker in a["name"].lower() and a["name"].endswith((".zip", ".tar.gz"))), None)
        if not asset:
            raise RuntimeError(f"Could not find a {marker} asset in the latest scrcpy release.")
        archive = self.storage_dir / asset["name"]
        self.download(asset["browser_download_url"], archive)
        if archive.suffix == ".zip":
            self.extract_zip_folder(archive, "scrcpy")
        else:
            with tarfile.open(archive, "r:gz") as tf:
                members = tf.getmembers()
                prefix = next(m.name.rsplit("/", 1)[0] + "/" for m in members if Path(m.name).name == "scrcpy")
                for member in members:
                    if member.isfile() and member.name.startswith(prefix):
                        copy_stream(tf.extractfile(member), self.storage_dir / Path(member.name).name)
        archive.unlink(missing_ok=True)
        self.make_executable(self.path("scrcpy"))

    def ensure(self):
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.path("cloudflared").exists(): self.install_cloudflared()
        if not self.path("adb").exists(): self.install_platform_tools()
        if not self.path("scrcpy").exists(): self.install_scrcpy()


class Dashboard:
    def __init__(self):
        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.minsize(720, 520)
        self.apply_window_icon()
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.tunnel: subprocess.Popen | None = None
        self.scrcpy: subprocess.Popen | None = None
        self.adb_proxy: TaggedTcpProxy | None = None
        self.scrcpy_proxy: TaggedTcpProxy | None = None
        self.supervisor = ProcessSupervisor()
        self.config = self.load_config()
        self.domain = StringVar(value=self.config.get("hostname", ""))
        self.port = StringVar(value=str(self.config.get("port", 5555)))
        self.status = StringVar(value="Ready")
        self.theme_mode = self.config.get("theme", "auto") if self.config.get("theme") in THEME_MODES else "auto"
        self.system_dark = system_prefers_dark()
        self.busy = False
        self.log_lock = threading.Lock()
        self.log_file = self.open_log_file()
        self.tools = ToolManager(self.log)
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")
        self.build_ui()
        self.apply_theme()
        threading.Thread(target=self.watch_system_theme, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        signal.signal(signal.SIGINT, self.handle_termination_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self.handle_termination_signal)
        self.root.after(100, self.consume_events)

    def apply_window_icon(self):
        icon_path = bundled_resource(ICON_FILE)
        try:
            if platform.system() == "Windows":
                self.root.iconbitmap(default=str(icon_path))
                return
            png = largest_png_in_ico(icon_path.read_bytes())
            if png:
                self.icon_image = PhotoImage(data=base64.b64encode(png))
                self.root.iconphoto(True, self.icon_image)
        except Exception:
            pass

    def handle_termination_signal(self, _signum, _frame):
        self.stop_proxies()
        self.supervisor.stop_all()
        self.root.after(0, self.root.destroy)

    def load_config(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self, **values):
        self.config.update(values)
        CONFIG_PATH.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def build_ui(self):
        base = tkfont.nametofont("TkDefaultFont").actual()
        self.fonts = {
            "body": (base["family"], 10),
            "small": (base["family"], 9),
            "title": (base["family"], 15, "bold"),
            "mono": (tkfont.nametofont("TkFixedFont").actual()["family"], 9),
        }
        frame = ttk.Frame(self.root, padding=(20, 18))
        frame.pack(fill="both", expand=True)

        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Label(header, text="scrcpy anywhere", style="Title.TLabel").pack(side="left")
        self.theme_button = ttk.Button(header, command=self.cycle_theme, style="Ghost.TButton")
        self.theme_button.pack(side="right")
        ttk.Label(frame, text="Cloudflare Access \u2192 adb \u2192 scrcpy", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        form = ttk.Frame(frame)
        form.pack(fill="x", pady=(18, 0))
        ttk.Label(form, text="Access hostname", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Local port", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Entry(form, textvariable=self.domain).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Entry(form, textvariable=self.port, width=8).grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(4, 0))
        form.columnconfigure(0, weight=1)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))
        self.connect_button = ttk.Button(buttons, text="Connect", command=self.connect, style="Accent.TButton")
        self.connect_button.pack(side="left")
        self.disconnect_button = ttk.Button(buttons, text="Disconnect", command=self.disconnect)
        self.disconnect_button.pack(side="left", padx=(8, 0))
        self.download_button = ttk.Button(buttons, text="Check tools", command=self.ensure_tools)
        self.download_button.pack(side="right")

        status = ttk.Frame(frame)
        status.pack(fill="x", pady=(16, 8))
        self.status_dot = ttk.Label(status, text="\u25cf", style="Dot.TLabel")
        self.status_dot.pack(side="left")
        ttk.Label(status, textvariable=self.status).pack(side="left", padx=(6, 0))
        self.status.trace_add("write", lambda *_: self.update_status_dot())

        self.log_frame = Frame(frame, highlightthickness=1, borderwidth=0)
        self.log_frame.pack(fill="both", expand=True)
        self.log_widget = Text(self.log_frame, height=16, state="disabled", wrap="word", relief="flat",
                               borderwidth=0, highlightthickness=0, padx=10, pady=8, font=self.fonts["mono"])
        scrollbar = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_widget.yview)
        self.log_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_widget.pack(side="left", fill="both", expand=True)
        self.log("Tool location: " + str(self.tools.storage_dir))

    def current_palette(self) -> dict[str, str]:
        dark = self.theme_mode == "dark" or (self.theme_mode == "auto" and self.system_dark)
        return PALETTES["dark" if dark else "light"]

    def apply_theme(self):
        c = self.current_palette()
        style = self.style
        self.root.configure(background=c["bg"])
        style.configure(".", background=c["bg"], foreground=c["text"], fieldbackground=c["field"],
                        bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                        troughcolor=c["bg"], focuscolor=c["accent"], selectbackground=c["accent"],
                        selectforeground=c["on_accent"], insertcolor=c["text"], font=self.fonts["body"])
        style.configure("Title.TLabel", font=self.fonts["title"])
        style.configure("Muted.TLabel", foreground=c["muted"], font=self.fonts["small"])
        style.configure("TEntry", padding=(8, 6), insertcolor=c["text"])
        style.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
                  darkcolor=[("focus", c["accent"])])
        style.configure("TButton", padding=(14, 6), background=c["bg"], focusthickness=0,
                        bordercolor=c["border"], lightcolor=c["bg"], darkcolor=c["bg"])
        style.map("TButton", background=[("disabled", c["bg"]), ("pressed", c["border"]), ("active", c["hover"])],
                  foreground=[("disabled", c["muted"])],
                  bordercolor=[("focus", c["border"])], lightcolor=[("active", c["hover"]), ("focus", c["bg"])],
                  darkcolor=[("active", c["hover"]), ("focus", c["bg"])])
        style.configure("Accent.TButton", background=c["accent"], foreground=c["on_accent"], bordercolor=c["accent"],
                        lightcolor=c["accent"], darkcolor=c["accent"])
        style.map("Accent.TButton",
                  background=[("disabled", c["border"]), ("pressed", c["accent_hover"]), ("active", c["accent_hover"])],
                  foreground=[("disabled", c["muted"])],
                  bordercolor=[("disabled", c["border"]), ("active", c["accent_hover"]), ("focus", c["accent"])],
                  lightcolor=[("disabled", c["border"]), ("active", c["accent_hover"]), ("focus", c["accent"])],
                  darkcolor=[("disabled", c["border"]), ("active", c["accent_hover"]), ("focus", c["accent"])])
        style.configure("Ghost.TButton", padding=(8, 4), foreground=c["muted"], bordercolor=c["bg"],
                        lightcolor=c["bg"], darkcolor=c["bg"], font=self.fonts["small"])
        style.map("Ghost.TButton", foreground=[("active", c["text"])],
                  bordercolor=[("active", c["hover"]), ("focus", c["bg"])], lightcolor=[("active", c["hover"]), ("focus", c["bg"])],
                  darkcolor=[("active", c["hover"]), ("focus", c["bg"])])
        style.configure("Vertical.TScrollbar", background=c["hover"], troughcolor=c["field"], bordercolor=c["field"],
                        lightcolor=c["hover"], darkcolor=c["hover"], arrowcolor=c["muted"], gripcount=0)
        style.map("Vertical.TScrollbar", background=[("active", c["border"])])
        self.log_frame.configure(background=c["field"], highlightbackground=c["border"], highlightcolor=c["border"])
        self.log_widget.configure(background=c["field"], foreground=c["text"], insertbackground=c["text"],
                                  selectbackground=c["accent"], selectforeground=c["on_accent"])
        self.theme_button.configure(text=THEME_LABELS[self.theme_mode])
        self.update_status_dot()
        self.set_title_bar_dark(c is PALETTES["dark"])

    def update_status_dot(self):
        c = self.current_palette()
        text = self.status.get().lower()
        if "error" in text:
            color = c["danger"]
        elif self.busy or text.endswith("..."):
            color = c["accent"]
        elif any(word in text for word in ("connected", "running", "ready")) and text != "ready":
            color = c["success"]
        else:
            color = c["muted"]
        self.style.configure("Dot.TLabel", foreground=color, font=self.fonts["small"])

    def set_title_bar_dark(self, dark: bool):
        if platform.system() != "Windows":
            return
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1 if dark else 0)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer / older Windows 10 builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    def cycle_theme(self):
        self.theme_mode = THEME_MODES[(THEME_MODES.index(self.theme_mode) + 1) % len(THEME_MODES)]
        try:
            self.save_config(theme=self.theme_mode)
        except OSError:
            pass
        self.apply_theme()

    def watch_system_theme(self):
        while True:
            time.sleep(3)
            if self.theme_mode == "auto":
                dark = system_prefers_dark()
                if dark != self.system_dark:
                    self.events.put(("system_theme", dark))

    @staticmethod
    def open_log_file():
        try:
            path = log_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            return None

    def log(self, message: str):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        if self.log_file is not None:
            with self.log_lock:
                try:
                    self.log_file.write(line)
                except (OSError, ValueError):
                    pass
        self.events.put(("log", line))

    def append_log(self, lines: list[str]):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", "".join(lines))
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def consume_events(self):
        pending_logs: list[str] = []
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                pending_logs.append(value)
                continue
            # Flush first so log lines stay ordered relative to dialogs and status changes.
            if pending_logs:
                self.append_log(pending_logs)
                pending_logs = []
            if kind == "status": self.status.set(value)
            elif kind == "system_theme":
                self.system_dark = value
                self.apply_theme()
            elif kind == "error": messagebox.showerror(APP_NAME, value)
            elif kind == "busy":
                state = "disabled" if value else "normal"
                for button in (self.download_button, self.connect_button, self.disconnect_button):
                    button.configure(state=state)
        if pending_logs:
            self.append_log(pending_logs)
        self.root.after(100, self.consume_events)

    def worker(self, label, fn):
        if self.busy:
            self.log("An operation is already in progress.")
            return
        self.busy = True
        self.events.put(("busy", True))
        def run():
            self.events.put(("status", label))
            try:
                fn()
            except Exception as exc:
                self.log(f"Error: {exc}")
                self.events.put(("error", str(exc)))
                self.events.put(("status", "Error"))
            finally:
                self.busy = False
                self.events.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def ensure_tools(self):
        self.worker("Preparing tools...", self._ensure_tools)

    def _ensure_tools(self):
        self.tools.ensure()
        self.log("cloudflared, adb, and scrcpy are ready")
        self.events.put(("status", "Tools ready"))

    def pipe_output(self, process, label):
        assert process.stdout is not None
        for line in process.stdout:
            self.log(f"{label}: {line.rstrip()}")

    def spawn_logged(self, label: str, command: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
        self.log("Running: " + " ".join(command))
        process = self.supervisor.spawn(command, env=env)
        threading.Thread(target=self.pipe_output, args=(process, label), daemon=True).start()
        return process

    def connect(self):
        hostname = self.domain.get().strip()
        port_text = self.port.get()
        self.worker("Connecting...", lambda: self._connect(hostname, port_text))

    def _connect(self, hostname: str, port_text: str):
        if not hostname: raise ValueError("Enter a Cloudflare Access hostname.")
        port = int(port_text)
        if not 1 <= port <= 65535: raise ValueError("Port must be between 1 and 65535.")
        self.save_config(hostname=hostname, port=port)
        self.tools.ensure()
        self.supervisor.stop(self.tunnel)
        self.stop_proxies()
        command = [str(self.tools.path("cloudflared")), "access", "tcp", "--hostname", hostname, "--url", f"localhost:{port}"]
        self.tunnel = self.spawn_logged("cloudflared", command)
        self.adb_proxy = TaggedTcpProxy(port, ADB_TUNNEL_MARKER, self.log)
        self.scrcpy_proxy = ScrcpyTaggedTcpProxy(port, self.log)
        adb = str(self.tools.path("adb"))
        serial = self.wait_for_adb_device(adb, self.adb_proxy.port)
        self.log(f"Remote adb device ready: {serial}. Starting scrcpy...")
        self._launch_scrcpy(serial, self.adb_proxy.port, self.scrcpy_proxy.port)
        self.events.put(("status", f"Connected and running scrcpy: {serial}"))

    @staticmethod
    def remote_adb_command(adb: str, port: int, *arguments: str) -> list[str]:
        return [adb, "-H", "127.0.0.1", "-P", str(port), *arguments]

    @staticmethod
    def remote_adb_environment(port: int) -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("ADB_VENDOR_KEYS", None)
        environment["ADB_SERVER_SOCKET"] = f"tcp:127.0.0.1:{port}"
        return environment

    def wait_for_adb_device(self, adb: str, port: int, timeout: int = 60) -> str:
        """Allow time for the Access login and TCP listener to become ready."""
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            if self.tunnel is None or self.tunnel.poll() is not None:
                raise RuntimeError("cloudflared exited before the adb tunnel became ready. Check the log.")
            try:
                devices = self.run_and_log(self.remote_adb_command(adb, port, "devices", "-l"))
                for line in devices.splitlines():
                    fields = line.split()
                    if len(fields) >= 2 and fields[1] == "device":
                        return fields[0]
            except RuntimeError as exc:
                last_error = str(exc)
            if time.monotonic() < deadline:
                self.events.put(("status", "Waiting for Cloudflare Access authentication..."))
                time.sleep(2)
        suffix = f" Last adb error: {last_error}" if last_error else ""
        raise RuntimeError(f"adb device was not found within {timeout} seconds. Check Access authentication and the cloudflared log." + suffix)

    def run_and_log(self, command: list[str], env: dict[str, str] | None = None) -> str:
        self.log("Running: " + " ".join(command))
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=env,
                                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW_FLAG)
        output = (completed.stdout + completed.stderr).strip()
        if output: self.log(output)
        if completed.returncode: raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")
        return output

    def _launch_scrcpy(self, serial: str, adb_port: int, scrcpy_port: int):
        self.supervisor.stop(self.scrcpy)
        command = [
            str(self.tools.path("scrcpy")),
            "-s",
            serial,
            "--force-adb-forward",
            "--port=27183",
            "--tunnel-host=127.0.0.1",
            f"--tunnel-port={scrcpy_port}",
        ]
        self.scrcpy = self.spawn_logged("scrcpy", command, env=self.remote_adb_environment(adb_port))
        self.events.put(("status", "scrcpy is running"))

    def disconnect(self):
        self.worker("Disconnecting...", self._disconnect)

    def _disconnect(self):
        self.supervisor.stop(self.scrcpy)
        self.stop_proxies()
        self.supervisor.stop(self.tunnel)
        self.tunnel = self.scrcpy = None
        self.events.put(("status", "Disconnected"))

    def stop_proxies(self):
        for proxy in (self.adb_proxy, self.scrcpy_proxy):
            if proxy is not None:
                proxy.stop()
        self.adb_proxy = self.scrcpy_proxy = None

    def close(self):
        self.stop_proxies()
        self.supervisor.stop_all()
        self.root.destroy()
        if self.log_file is not None:
            with self.log_lock:
                self.log_file.close()

    def run(self): self.root.mainloop()


if __name__ == "__main__":
    Dashboard().run()
