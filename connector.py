from __future__ import annotations

import json
import platform
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
import atexit
from pathlib import Path
from tkinter import StringVar, Tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from tkinter import messagebox

APP_NAME = "cloudflared adb scrcpy quick-connect"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path.home() / "adb-cloud-dashboard.json"


def application_dir() -> Path:
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return APP_DIR


def tool_storage_dir() -> Path:
    return application_dir() / "tools"


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


def normalized_machine() -> str:
    value = platform.machine().lower()
    return "arm64" if value in {"arm64", "aarch64"} else "x86_64"


def executable_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


class ProcessSupervisor:
    def __init__(self):
        self.processes: list[subprocess.Popen] = []
        self.job = None
        if platform.system() == "Windows":
            self._create_windows_job()
        atexit.register(self.stop_all)

    def _create_windows_job(self):
        import ctypes
        from ctypes import wintypes
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
        if platform.system() == "Linux":
            import ctypes
            ctypes.CDLL(None).prctl(1, signal.SIGTERM)

    def spawn(self, command: list[str]) -> subprocess.Popen:
        options = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
        if platform.system() == "Windows":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        elif platform.system() == "Linux":
            options["preexec_fn"] = self._linux_parent_death_signal
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
        if self.job is not None:
            import ctypes
            from ctypes import wintypes
            if not self._kernel32.AssignProcessToJobObject(self.job, wintypes.HANDLE(process._handle)):
                pass
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


class ToolManager:
    def __init__(self, log):
        self.log = log
        self.system = platform.system()
        self.machine = normalized_machine()
        self.base = TOOLS_DIR / f"{self.system.lower()}-{self.machine}"

    def path(self, tool: str) -> Path:
        if tool == "scrcpy" and self.system == "Linux":
            installed = shutil.which("scrcpy")
            if installed:
                return Path(installed)
        return self.base / executable_name(tool)

    def download(self, url: str, target: Path):
        self.log(f"Downloading: {url}")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "adb-cloud-dashboard"})
        try:
            with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as output:
                shutil.copyfileobj(source, output)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def make_executable(self, path: Path):
        if self.system != "Windows":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def install_platform_tools(self):
        archive = self.base / "platform-tools.zip"
        self.download(PLATFORM_TOOLS[self.system], archive)
        with zipfile.ZipFile(archive) as zf:
            member = next(n for n in zf.namelist() if n.endswith("/" + executable_name("adb")))
            prefix = member.rsplit("/", 1)[0] + "/"
            for name in zf.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    destination = self.base / Path(name).name
                    with zf.open(name) as src, destination.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        archive.unlink(missing_ok=True)
        self.make_executable(self.path("adb"))

    def install_cloudflared(self):
        try:
            url = CLOUDFLARED[(self.system, self.machine)]
        except KeyError as exc:
            raise RuntimeError(f"Unsupported platform: {self.system} {self.machine}") from exc
        target = self.path("cloudflared")
        if url.endswith(".tgz"):
            archive = self.base / "cloudflared.tgz"
            self.download(url, archive)
            with tarfile.open(archive, "r:gz") as tf:
                binary = next(m for m in tf.getmembers() if Path(m.name).name == "cloudflared")
                with tf.extractfile(binary) as src, target.open("wb") as dst:
                    assert src is not None
                    shutil.copyfileobj(src, dst)
            archive.unlink(missing_ok=True)
        else:
            self.download(url, target)
        self.make_executable(target)

    def install_scrcpy(self):
        if self.system == "Linux":
            raise RuntimeError("Install the scrcpy package on Linux, then try again. (Ubuntu/Debian: sudo apt install scrcpy)")
        data = json.loads(urllib.request.urlopen(
            urllib.request.Request(GITHUB_RELEASE, headers={"User-Agent": "adb-cloud-dashboard"}), timeout=30
        ).read())
        assets = data.get("assets", [])
        marker = "win64" if self.system == "Windows" else ("macos-aarch64" if self.machine == "arm64" else "macos-x86_64")
        asset = next((a for a in assets if marker in a["name"].lower() and a["name"].endswith((".zip", ".tar.gz"))), None)
        if not asset:
            raise RuntimeError(f"Could not find a {marker} asset in the latest scrcpy release.")
        archive = self.base / asset["name"]
        self.download(asset["browser_download_url"], archive)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                binary = next(n for n in zf.namelist() if n.endswith("/" + executable_name("scrcpy")))
                prefix = binary.rsplit("/", 1)[0] + "/"
                for name in zf.namelist():
                    if name.startswith(prefix) and not name.endswith("/"):
                        with zf.open(name) as src, (self.base / Path(name).name).open("wb") as dst:
                            shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                prefix = next(m.name.rsplit("/", 1)[0] + "/" for m in tf.getmembers() if Path(m.name).name == "scrcpy")
                for member in tf.getmembers():
                    if member.isfile() and member.name.startswith(prefix):
                        with tf.extractfile(member) as src, (self.base / Path(member.name).name).open("wb") as dst:
                            assert src is not None
                            shutil.copyfileobj(src, dst)
        archive.unlink(missing_ok=True)
        self.make_executable(self.path("scrcpy"))

    def ensure(self):
        self.base.mkdir(parents=True, exist_ok=True)
        if not self.path("cloudflared").exists(): self.install_cloudflared()
        if not self.path("adb").exists(): self.install_platform_tools()
        if not self.path("scrcpy").exists(): self.install_scrcpy()


class Dashboard:
    def __init__(self):
        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.minsize(720, 520)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.tunnel: subprocess.Popen | None = None
        self.scrcpy: subprocess.Popen | None = None
        self.supervisor = ProcessSupervisor()
        self.config = self.load_config()
        self.domain = StringVar(value=self.config.get("hostname", ""))
        self.port = StringVar(value=str(self.config.get("port", 5555)))
        self.status = StringVar(value="Ready")
        self.busy = False
        self.tools = ToolManager(self.log)
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        signal.signal(signal.SIGINT, self.handle_termination_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self.handle_termination_signal)
        self.root.after(100, self.consume_events)

    def handle_termination_signal(self, _signum, _frame):
        self.supervisor.stop_all()
        self.root.after(0, self.root.destroy)

    def load_config(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self, hostname: str, port: int):
        CONFIG_PATH.write_text(json.dumps({"hostname": hostname, "port": port}, indent=2), encoding="utf-8")

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Cloudflare Access -> adb -> scrcpy", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        form = ttk.Frame(frame)
        form.pack(fill="x", pady=(16, 8))
        ttk.Label(form, text="Access hostname").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.domain, width=48).grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ttk.Label(form, text="Local port").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.port, width=12).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=12)
        self.download_button = ttk.Button(buttons, text="Download / Check tools", command=self.ensure_tools)
        self.download_button.pack(side="left")
        self.connect_button = ttk.Button(buttons, text="Connect", command=self.connect)
        self.connect_button.pack(side="left", padx=8)
        self.disconnect_button = ttk.Button(buttons, text="Disconnect", command=self.disconnect)
        self.disconnect_button.pack(side="right")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w")
        self.log_widget = ScrolledText(frame, height=18, state="disabled", font=("Consolas", 10))
        self.log_widget.pack(fill="both", expand=True, pady=(8, 0))
        self.log("Tool location: " + str(self.tools.base))

    def log(self, message: str):
        self.events.put(("log", f"[{time.strftime('%H:%M:%S')}] {message}\n"))

    def consume_events(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "log":
                self.log_widget.configure(state="normal")
                self.log_widget.insert("end", value)
                self.log_widget.see("end")
                self.log_widget.configure(state="disabled")
            elif kind == "status": self.status.set(value)
            elif kind == "error": messagebox.showerror(APP_NAME, value)
            elif kind == "busy":
                state = "disabled" if value else "normal"
                for button in (self.download_button, self.connect_button, self.disconnect_button):
                    button.configure(state=state)
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
        for line in iter(process.stdout.readline, ""):
            self.log(f"{label}: {line.rstrip()}")

    def connect(self):
        hostname = self.domain.get().strip()
        port_text = self.port.get()
        self.worker("Connecting...", lambda: self._connect(hostname, port_text))

    def _connect(self, hostname: str, port_text: str):
        if not hostname: raise ValueError("Enter a Cloudflare Access hostname.")
        port = int(port_text)
        if not 1 <= port <= 65535: raise ValueError("Port must be between 1 and 65535.")
        self.save_config(hostname, port)
        self.tools.ensure()
        self.supervisor.stop(self.tunnel)
        command = [str(self.tools.path("cloudflared")), "access", "tcp", "--hostname", hostname, "--url", f"localhost:{port}"]
        self.log("Running: " + " ".join(command))
        self.tunnel = self.supervisor.spawn(command)
        threading.Thread(target=self.pipe_output, args=(self.tunnel, "cloudflared"), daemon=True).start()
        adb = str(self.tools.path("adb"))
        target = f"127.0.0.1:{port}"
        self.wait_for_adb_device(adb, target)
        self.log(f"adb connected: {target}. Starting scrcpy...")
        self._launch_scrcpy(target)
        self.events.put(("status", f"Connected and running scrcpy: {target}"))

    def wait_for_adb_device(self, adb: str, target: str, timeout: int = 60):
        """Allow time for the Access login and TCP listener to become ready."""
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            if self.tunnel is None or self.tunnel.poll() is not None:
                raise RuntimeError("cloudflared exited before the adb tunnel became ready. Check the log.")
            try:
                self.run_and_log([adb, "connect", target])
                devices = self.run_and_log([adb, "devices"])
                if any(line.split("\t", 1) == [target, "device"] for line in devices.splitlines()):
                    return
            except RuntimeError as exc:
                last_error = str(exc)
            self.events.put(("status", "Waiting for Cloudflare Access authentication..."))
            time.sleep(2)
        suffix = f" Last adb error: {last_error}" if last_error else ""
        raise RuntimeError("adb device was not found within 60 seconds. Check Access authentication and the cloudflared log." + suffix)

    def run_and_log(self, command: list[str]) -> str:
        self.log("Running: " + " ".join(command))
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        output = (completed.stdout + completed.stderr).strip()
        if output: self.log(output)
        if completed.returncode: raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")
        return output

    def _launch_scrcpy(self, target: str):
        self.supervisor.stop(self.scrcpy)
        command = [str(self.tools.path("scrcpy")), "-s", target]
        self.log("Running: " + " ".join(command))
        self.scrcpy = self.supervisor.spawn(command)
        threading.Thread(target=self.pipe_output, args=(self.scrcpy, "scrcpy"), daemon=True).start()
        self.events.put(("status", "scrcpy is running"))

    def disconnect(self):
        port_text = self.port.get()
        self.worker("Disconnecting...", lambda: self._disconnect(port_text))

    def _disconnect(self, port_text: str):
        port = int(port_text)
        self.supervisor.stop(self.scrcpy)
        if self.tools.path("adb").exists():
            try: self.run_and_log([str(self.tools.path("adb")), "disconnect", f"127.0.0.1:{port}"])
            except RuntimeError: pass
        self.supervisor.stop(self.tunnel)
        self.tunnel = self.scrcpy = None
        self.events.put(("status", "Disconnected"))

    def close(self):
        self.supervisor.stop_all()
        self.root.destroy()

    def run(self): self.root.mainloop()


if __name__ == "__main__":
    Dashboard().run()
