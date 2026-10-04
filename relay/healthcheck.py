import os
import socket
import subprocess
import sys

ADB_MARKER = b"SCRCPY-ANYWHERE/1 ADB\n"


def adb_request(sock, request):
    sock.sendall(b"%04x%s" % (len(request), request))
    if sock.recv(4) != b"OKAY":
        raise RuntimeError(f"adb rejected {request!r}")
    # Drain the reply so closing does not reset the relay's connection.
    remaining = int(sock.recv(4), 16)
    while remaining > 0:
        remaining -= len(sock.recv(remaining))


def main():
    host = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
    with socket.create_connection((host, 5555), timeout=5) as sock:
        sock.sendall(ADB_MARKER)
        adb_request(sock, b"host:version")
    prop = subprocess.run(
        ["adb", "-s", os.environ["ADB_DEVICE"], "shell", "getprop", "debug.scrcpy_anywhere.configured"],
        capture_output=True, text=True, timeout=8,
    ).stdout.strip()
    if prop != "1":
        raise RuntimeError(f"{os.environ['ADB_DEVICE']} is not booted and configured")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc)
        sys.exit(1)
