#!/bin/bash
set -e
echo "Starting Android Emulator..."

emulator \
    -avd pixel \
    -no-window \
    -no-audio \
    -no-boot-anim \
    -gpu host \
    -port 5554 \
    &

EMULATOR_PID=$!
echo "Waiting for emulator..."

adb start-server
until adb devices | grep -q "emulator-5554.*device"; do
    sleep 2
    echo "Waiting for Android..."
done

if adb shell getprop sys.boot_completed | grep -q 1; then
    echo "Android emulator is fully booted."
fi

wait $EMULATOR_PID
