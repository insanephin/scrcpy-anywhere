#!/bin/bash
set -e
echo "Starting Android Emulator..."

emulator \
    -avd pixel \
    -no-window \
    -no-audio \
    -no-boot-anim \
    -gpu swiftshader_indirect \
    -port 5554 \
    &

EMULATOR_PID=$!
echo "Waiting for emulator..."

adb start-server
until adb devices | grep -q "emulator-5554.*device"; do
    sleep 2
    echo "Waiting for Android..."
done
echo "Android emulator is ready."

adb -s emulator-5554 tcpip 5555
echo "adb TCP/IP enabled on port 5555."

wait $EMULATOR_PID
