"""Builds the launcher + versioned app layout and the release packages for the current platform.

    python build.py --version 1.2.3

dist/ receives two files:
    scrcpy-anywhere-<platform>.zip|tar.gz|dmg    first install (launcher + app-<version>)
    scrcpy-anywhere-update-<platform>.zip|tar.gz the app folder alone, downloaded by the in-app updater
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "scrcpy-anywhere"
ICON = ROOT / "scrcpy-anywhere.ico"
SYSTEM = platform.system()
PLATFORM = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}[SYSTEM]


def pyinstaller(script: str, target: str, *options: str) -> Path:
    output = BUILD / target
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--icon", str(ICON), "--name", NAME,
        "--distpath", str(output / "dist"), "--workpath", str(output / "work"), "--specpath", str(output),
        *options, str(ROOT / script),
    ], check=True)
    return output / "dist" / (f"{NAME}.app" if SYSTEM == "Darwin" else NAME)


def package_portable(app: Path, launcher: Path, version: str):
    stage = BUILD / "stage" / NAME
    shutil.copytree(launcher, stage, symlinks=True)
    shutil.copytree(app, stage / f"app-{version}", symlinks=True)
    archive_format = "zip" if SYSTEM == "Windows" else "gztar"
    shutil.make_archive(str(DIST / f"{NAME}-{PLATFORM}"), archive_format, root_dir=stage.parent, base_dir=NAME)
    shutil.make_archive(str(DIST / f"{NAME}-update-{PLATFORM}"), archive_format, root_dir=app)


def package_macos(app: Path, launcher: Path, version: str):
    dmg_root = BUILD / "dmg"
    bundle = dmg_root / f"{NAME}.app"
    shutil.copytree(launcher, bundle, symlinks=True)
    shutil.copytree(app, bundle / "Contents" / "Resources" / f"app-{version}" / app.name, symlinks=True)
    # Adding the app changed the launcher bundle, so its ad-hoc signature has to be redone.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)
    (dmg_root / "Applications").symlink_to("/Applications")
    subprocess.run(["hdiutil", "create", "-volname", NAME, "-srcfolder", str(dmg_root), "-ov", "-format", "UDZO",
                    str(DIST / f"{NAME}-{PLATFORM}.dmg")], check=True)
    shutil.make_archive(str(DIST / f"{NAME}-update-{PLATFORM}"), "gztar", root_dir=app.parent, base_dir=app.name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.0.0", help="release version, e.g. 1.2.3 or v1.2.3")
    version = parser.parse_args().version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        parser.error(f"version must look like 1.2.3 (launcher.py only runs app-<major.minor.patch> folders): {version}")

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    generated = BUILD / "generated"
    generated.mkdir(parents=True)
    (generated / "_version.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")

    app = pyinstaller("connector.py", "app", "--paths", str(generated), "--add-data", f"{ICON}{os.pathsep}.")
    launcher = pyinstaller("launcher.py", "launcher", "--exclude-module", "tkinter")
    DIST.mkdir()
    if SYSTEM == "Darwin":
        package_macos(app, launcher, version)
    else:
        package_portable(app, launcher, version)
    for path in sorted(DIST.iterdir()):
        print(path)


if __name__ == "__main__":
    main()
