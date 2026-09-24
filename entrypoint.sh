#!/bin/bash
set -e
echo "Starting Android Emulator..."

# WSL or Docker can leave stale AVD lock files or directories after an abrupt shutdown.
find /root/.android/avd/pixel.avd -maxdepth 1 -mindepth 1 -name '*.lock*' -exec rm -rf -- {} +

emulator \
    -avd pixel \
    -no-window \
    -no-audio \
    -no-boot-anim \
    -no-snapshot \
    -no-metrics \
    -skip-adb-auth \
    -gpu swiftshader_indirect \
    -port 5554 \
    &

EMULATOR_PID=$!
echo "Waiting for emulator..."

adb start-server
until adb devices | grep -q "emulator-5554.*device"; do
    if ! kill -0 "$EMULATOR_PID" 2>/dev/null; then
        wait "$EMULATOR_PID"
        exit $?
    fi
    sleep 2
    echo "Waiting for Android..."
done

until [ "$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
    if ! kill -0 "$EMULATOR_PID" 2>/dev/null; then
        wait "$EMULATOR_PID"
        exit $?
    fi
    sleep 2
    echo "Waiting for Android boot completion..."
done
echo "Android emulator is ready."

CONTAINER_IP=$(hostname -I | awk '{print $1}')
cat >/tmp/scrcpy-anywhere-relay.py <<'PY'
import select
import socket
import sys
import threading
import time

ADB_MARKER = b"SCRCPY-ANYWHERE/1 ADB\n"
SCRCPY_PREFIX = b"SCRCPY-ANYWHERE/1 SCRCPY "
scrcpy_order = threading.Condition()
scrcpy_next_sequence = {}


def read_marker(connection):
    marker = bytearray()
    connection.settimeout(30)
    while len(marker) < 64 and not marker.endswith(b"\n"):
        data = connection.recv(1)
        if not data:
            return None
        marker.extend(data)
    connection.settimeout(None)
    return bytes(marker)


def connect_backend(marker, adb_port, scrcpy_port):
    if marker == ADB_MARKER:
        return socket.create_connection(("127.0.0.1", adb_port), timeout=10)

    fields = marker.rstrip(b"\n").split(b" ")
    if len(fields) != 4 or b" ".join(fields[:2]) + b" " != SCRCPY_PREFIX:
        raise ValueError(f"unknown tunnel marker: {marker!r}")
    session = fields[2]
    sequence = int(fields[3])
    deadline = time.monotonic() + 30
    with scrcpy_order:
        while sequence > scrcpy_next_sequence.get(session, 0):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for an earlier scrcpy stream")
            scrcpy_order.wait(remaining)
        if sequence < scrcpy_next_sequence.get(session, 0):
            raise ValueError("duplicate scrcpy stream sequence")
        backend = socket.create_connection(("127.0.0.1", scrcpy_port), timeout=10)
        scrcpy_next_sequence[session] = sequence + 1
        scrcpy_order.notify_all()
        return backend


def relay(client, adb_port, scrcpy_port):
    backend = None
    try:
        marker = read_marker(client)
        backend = connect_backend(marker, adb_port, scrcpy_port)
        backend.settimeout(None)
        while True:
            readable, _, _ = select.select((client, backend), (), ())
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                destination = backend if source is client else client
                destination.sendall(data)
    except (OSError, ValueError) as exc:
        print(f"Relay connection closed: {exc}", flush=True)
    finally:
        client.close()
        if backend is not None:
            backend.close()


def main():
    listen_host = sys.argv[1]
    listen_port = int(sys.argv[2])
    adb_port = int(sys.argv[3])
    scrcpy_port = int(sys.argv[4])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((listen_host, listen_port))
        listener.listen()
        print(
            f"Tagged ADB/scrcpy relay listening on {listen_host}:{listen_port} "
            f"(ADB {adb_port}, scrcpy {scrcpy_port}).",
            flush=True,
        )
        while True:
            client, _ = listener.accept()
            threading.Thread(
                target=relay, args=(client, adb_port, scrcpy_port), daemon=True
            ).start()


if __name__ == "__main__":
    main()
PY

python3 /tmp/scrcpy-anywhere-relay.py "$CONTAINER_IP" 5555 5037 27183 &
RELAY_PID=$!

wait -n "$EMULATOR_PID" "$RELAY_PID"
