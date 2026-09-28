from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "scrcpy-anywhere"
SYSTEM = platform.system()
VERSION_DIR = re.compile(r"^app-(\d+)\.(\d+)\.(\d+)$")
KEEP_VERSIONS = 2


def launcher_path() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()


def home_dir() -> Path:
    if SYSTEM == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return launcher_path().parent


def bundled_dir() -> Path | None:
    return launcher_path().parent.parent / "Resources" if SYSTEM == "Darwin" else None


def app_executable(version_dir: Path) -> Path:
    if SYSTEM == "Darwin":
        return version_dir / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME
    return version_dir / (f"{APP_NAME}.exe" if SYSTEM == "Windows" else APP_NAME)


def installed_versions(directory: Path | None) -> list[tuple[tuple[int, ...], Path]]:
    if directory is None or not directory.is_dir():
        return []
    found = []
    for child in directory.iterdir():
        match = VERSION_DIR.match(child.name)
        if match and app_executable(child).is_file():
            found.append((tuple(int(part) for part in match.groups()), child))
    return sorted(found, reverse=True)


def remove_old_versions(home: Path, versions: list[tuple[tuple[int, ...], Path]]):
    for _version, path in versions[KEEP_VERSIONS:]:
        # Renaming fails while a version is still running (Windows), which keeps it from being half-deleted.
        try:
            path.rename(home / f".trash-{path.name}")
        except OSError:
            pass
    for child in home.glob(".trash-*"):
        shutil.rmtree(child, ignore_errors=True)


def child_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if "LD_LIBRARY_PATH_ORIG" in environment:
        environment["LD_LIBRARY_PATH"] = environment.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        environment.pop("LD_LIBRARY_PATH", None)
    environment["SCRCPY_ANYWHERE_HOME"] = str(home)
    environment["SCRCPY_ANYWHERE_LAUNCHER"] = str(launcher_path())
    return environment


def launch(executable: Path, environment: dict[str, str]):
    arguments = [str(executable), *sys.argv[1:]]
    if SYSTEM == "Windows":
        subprocess.Popen(arguments, env=environment, close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        os.execve(executable, arguments, environment)


def show_error(message: str):
    if SYSTEM == "Windows":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    elif SYSTEM == "Darwin":
        subprocess.run(["osascript", "-e", f"display alert {json.dumps(APP_NAME)} message {json.dumps(message)} as critical"], check=False)
    print(message, file=sys.stderr)


def main() -> int:
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    local = installed_versions(home)
    remove_old_versions(home, local)
    candidates = sorted(installed_versions(home) + installed_versions(bundled_dir()), reverse=True)
    environment = child_environment(home)
    errors = []
    for _version, path in candidates:
        try:
            launch(app_executable(path), environment)
            return 0
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    show_error("No runnable scrcpy-anywhere version was found in " + str(home) + ("\n" + "\n".join(errors) if errors else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
