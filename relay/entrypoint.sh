#!/bin/bash
set -u

DEVICE="${ADB_DEVICE:?set ADB_DEVICE, e.g. redroid-1:5555}"
CHECK_INTERVAL="${CHECK_INTERVAL:-5}"
CONFIGURED_PROP=debug.scrcpy_anywhere.configured
RELAY_PID=

device_shell() {
    timeout 15 adb -s "$DEVICE" shell "$@" 2>/dev/null | tr -d '\r'
}

apply_settings() {
    device_shell svc power stayon true >/dev/null &&
    device_shell settings put system screen_off_timeout 2147483647 >/dev/null &&
    device_shell dumpsys deviceidle disable >/dev/null &&
    device_shell setprop "$CONFIGURED_PROP" 1 >/dev/null &&
    [ "$(device_shell getprop "$CONFIGURED_PROP")" = "1" ]
}

shutdown() {
    trap - TERM INT
    [ -n "$RELAY_PID" ] && kill "$RELAY_PID" 2>/dev/null
    adb kill-server >/dev/null 2>&1
    exit 0
}
trap shutdown TERM INT

adb start-server

CONTAINER_IP=$(hostname -I | awk '{print $1}')
python3 /relay.py "$CONTAINER_IP" 5555 5037 27183 &
RELAY_PID=$!

state=
set_state() {
    [ "$state" = "$1" ] && return
    state=$1
    echo "$2"
}

while kill -0 "$RELAY_PID" 2>/dev/null; do
    if [ "$(device_shell echo ok)" != "ok" ]; then
        if [ "$state" = ready ]; then
            echo "Lost connection to $DEVICE; reconnecting..."
        fi
        set_state connecting "Connecting to $DEVICE..."
        adb disconnect "$DEVICE" >/dev/null 2>&1
        timeout 15 adb connect "$DEVICE" >/dev/null 2>&1
    elif [ "$(device_shell getprop sys.boot_completed)" != "1" ]; then
        set_state booting "Waiting for Android boot completion on $DEVICE..."
    elif [ "$(device_shell getprop "$CONFIGURED_PROP")" != "1" ]; then
        if apply_settings; then
            state=ready
            echo "Android device is ready: $DEVICE (stay-on, no screen timeout, doze disabled)."
        else
            echo "Applying unattended-operation settings to $DEVICE failed; retrying."
        fi
    fi
    sleep "$CHECK_INTERVAL" &
    wait $!
done

echo "Relay process exited."
exit 1
