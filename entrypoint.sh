#!/bin/bash
set -e

AVD_HOME=/root/.android/avd
EMULATOR_GPU="${EMULATOR_GPU:-auto}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-600}"
SWIFTSHADER_ICD=/opt/android-sdk/emulator/lib64/vulkan/vk_swiftshader_icd.json
EMULATOR_PID=
RELAY_PID=

if [ ! -f "$AVD_HOME/pixel.ini" ]; then
    echo "Initializing AVD data from template..."
    cp -a /opt/avd-template/. "$AVD_HOME/"
fi

find "$AVD_HOME/pixel.avd" -maxdepth 1 -mindepth 1 -name '*.lock*' -exec rm -rf -- {} +

select_gpu_mode() {
    [ -f "$SWIFTSHADER_ICD.orig" ] || cp "$SWIFTSHADER_ICD" "$SWIFTSHADER_ICD.orig"
    cp "$SWIFTSHADER_ICD.orig" "$SWIFTSHADER_ICD"

    if [ "$1" != "auto" ]; then
        echo "$1"
        return
    fi

    local vendor icd=
    vendor=$(cat /sys/class/drm/renderD128/device/vendor 2>/dev/null || true)
    case "$vendor" in
        0x1002) icd=/usr/share/vulkan/icd.d/radeon_icd.json ;;
        0x8086) icd=/usr/share/vulkan/icd.d/intel_icd.json ;;
    esac
    if [ -n "$icd" ] && [ -f "$icd" ] && [ -r /dev/dri/renderD128 ]; then
        cp "$icd" "$SWIFTSHADER_ICD"
        echo "swangle_indirect"
    else
        echo "swiftshader_indirect"
    fi
}

start_emulator() {
    echo "Starting Android Emulator (gpu: $1)..."
    emulator \
        -feature -ModemSimulator \
        -avd pixel \
        -no-window \
        -no-audio \
        -no-boot-anim \
        -no-snapshot \
        -no-metrics \
        -skip-adb-auth \
        -gpu "$1" \
        -port 5554 \
        &
    EMULATOR_PID=$!
}

wait_for_boot() {
    local deadline=$((SECONDS + BOOT_TIMEOUT))
    echo "Waiting for emulator..."
    until adb devices | grep -q "emulator-5554.*device"; do
        kill -0 "$EMULATOR_PID" 2>/dev/null || return 1
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 2
        echo "Waiting for Android..."
    done

    until [ "$(adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
        kill -0 "$EMULATOR_PID" 2>/dev/null || return 1
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 2
        echo "Waiting for Android boot completion..."
    done
}

stop_failed_emulator() {
    kill -TERM "$EMULATOR_PID" 2>/dev/null || true
    wait "$EMULATOR_PID" 2>/dev/null || true
}

shutdown() {
    trap - TERM INT
    echo "Stopping Android emulator..."
    if [ -n "$EMULATOR_PID" ] && kill -0 "$EMULATOR_PID" 2>/dev/null; then
        adb -s emulator-5554 shell reboot -p >/dev/null 2>&1 || true
        for _ in $(seq 30); do
            kill -0 "$EMULATOR_PID" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$EMULATOR_PID" 2>/dev/null; then
            adb -s emulator-5554 emu kill >/dev/null 2>&1 || kill -TERM "$EMULATOR_PID" 2>/dev/null || true
        fi
        wait "$EMULATOR_PID" 2>/dev/null || true
    fi
    [ -n "$RELAY_PID" ] && kill "$RELAY_PID" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

if [ -f "$AVD_HOME/adbkey" ]; then
    cp "$AVD_HOME/adbkey" "$AVD_HOME/adbkey.pub" /root/.android/
fi
adb start-server
if [ ! -f "$AVD_HOME/adbkey" ]; then
    cp /root/.android/adbkey /root/.android/adbkey.pub "$AVD_HOME/"
fi
GPU_MODE=$(select_gpu_mode "$EMULATOR_GPU")
start_emulator "$GPU_MODE"
if ! wait_for_boot; then
    stop_failed_emulator
    if [ "$GPU_MODE" = "swiftshader_indirect" ]; then
        exit 1
    fi
    echo "Emulator failed with gpu '$GPU_MODE'; falling back to swiftshader_indirect."
    select_gpu_mode swiftshader_indirect >/dev/null
    find "$AVD_HOME/pixel.avd" -maxdepth 1 -mindepth 1 -name '*.lock*' -exec rm -rf -- {} +
    start_emulator swiftshader_indirect
    wait_for_boot || { stop_failed_emulator; exit 1; }
fi
echo "Android emulator is ready."

CONTAINER_IP=$(hostname -I | awk '{print $1}')
python3 /relay.py "$CONTAINER_IP" 5555 5037 27183 &
RELAY_PID=$!

wait -n "$EMULATOR_PID" "$RELAY_PID" || true
shutdown
