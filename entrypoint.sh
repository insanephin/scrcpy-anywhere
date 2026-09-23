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
cat >/tmp/haproxy.cfg <<EOF
global
    log stdout format raw local0

defaults
    mode tcp
    log global
    option tcplog
    timeout connect 10s
    timeout client 1h
    timeout server 1h

frontend cloudflare_access
    bind ${CONTAINER_IP}:5555
    tcp-request inspect-delay 5ms
    tcp-request content accept if { req.len gt 0 }
    use_backend adb_server if { req.len gt 0 }
    default_backend scrcpy_stream

backend adb_server
    server adb 127.0.0.1:5037

backend scrcpy_stream
    server scrcpy 127.0.0.1:27183
EOF

haproxy -W -db -f /tmp/haproxy.cfg &
HAPROXY_PID=$!
echo "ADB/scrcpy relay listening on $CONTAINER_IP:5555 (ADB 5037, scrcpy 27183)."

wait -n "$EMULATOR_PID" "$HAPROXY_PID"
