# scrcpy-anywhere

A simple desktop application that connects to an ADB TCP service protected by Cloudflare Access and launches `scrcpy`. The included Docker Compose configuration runs Android on the server: [Redroid](https://github.com/remote-android/redroid-doc) (Android in a container, with Play Store and ARM translation) by default, or the headless Android Emulator as an alternative profile.

## Architecture

```text
Redroid ─(internal Docker network)→ relay: ADB server / scrcpy stream mux :5555 → Cloudflare Access → cloudflared → tagged proxies → adb + scrcpy
```

- `connector.py`: A Tkinter desktop application that opens a Cloudflare Access TCP tunnel, then runs ADB and scrcpy.
- `redroid/`: Builds the Redroid 12 image with MindTheGapps (Play Store) and libndk_translation (ARM apps on x86_64).
- `relay/`, `relay.py`: A sidecar that keeps its ADB server connected to Redroid and multiplexes that ADB server and the scrcpy stream through port 5555.
- `Dockerfile`, `entrypoint.sh`: The Android Emulator container (`emulator` profile). It uses the same `relay.py`.
- `compose.yml`: Two instances of each by default (`redroid-1` + `relay-1`, `redroid-2` + `relay-2`).

## Tested environment

### Server

- Ubuntu 22.04 (kernel 5.15), x86_64
- Docker 29.8.1, Docker Compose v5.5.1

### Client

- Python 3.10
- Tkinter

> [!IMPORTANT]
> On Linux, install `scrcpy` separately before using the client.

## Prepare the host (Redroid)

Redroid shares the host kernel and needs the `binder_linux` module. KVM (`/dev/kvm`) is not required. On Ubuntu 22.04:

```bash
sudo apt install linux-modules-extra-$(uname -r)
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
```

Load it automatically after a reboot:

```bash
echo binder_linux | sudo tee /etc/modules-load.d/redroid.conf
echo 'options binder_linux devices="binder,hwbinder,vndbinder"' | sudo tee /etc/modprobe.d/redroid.conf
```

Check with `lsmod | grep binder_linux`. Redroid creates its binder devices through binderfs inside the container, so `/dev/binder` may not appear on the host; that is normal. `ashmem_linux` is not needed because the Compose file sets `androidboot.use_memfd=1`.

The host must also provide `/dev/dma_heap/system` (present on stock Ubuntu 22.04 kernels). It is needed for the Codec2 software encoders that scrcpy's default Opus audio uses (see [Notes on the image](#notes-on-the-image)).

> [!NOTE]
> After a kernel upgrade, install `linux-modules-extra` for the new kernel too, or `binder_linux` will be missing after the reboot.

## Build and run

On the server, run:

```bash
docker compose up -d --build
```

The first build downloads the Redroid base image (~1 GB) and the GApps/libndk archives (SHA256-verified). The first boot takes about 1–2 minutes, later boots about 30 seconds. To start only the first instance, run `docker compose up -d --build redroid-1 relay-1`.

Each instance has two containers:

| Container | Role | Host port |
| :--- | :--- | :--- |
| `redroid-1` / `redroid-2` | Android 12 (Redroid). ADB is exposed only on the internal Docker network. | none |
| `relay-1` / `relay-2` | ADB server + tagged ADB/scrcpy relay; runs `adb connect redroid-N:5555`. | `127.0.0.1:5555` / `127.0.0.1:5556` |

Settings are read from the environment or from a `.env` file next to `compose.yml`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `REDROID_WIDTH` / `REDROID_HEIGHT` | `720` / `1280` | Display resolution |
| `REDROID_DPI` | `320` | Display density |
| `REDROID_FPS` | `30` | Display frame rate (lower saves CPU) |
| `REDROID_CCODEC` | `1` | Codec2 mode; `0` restores Redroid's default (breaks scrcpy's default Opus audio) |
| `RELAY_1_PORT` / `RELAY_2_PORT` | `5555` / `5556` | Host port (always bound to `127.0.0.1`) |

Rendering is software-only (`androidboot.redroid_gpu_mode=guest`), so no GPU is needed. Android data (`/data`: installed apps, game data, Google account) is kept in the named volumes `redroid-1-data` and `redroid-2-data`, so it survives `docker compose down` and image rebuilds. **`docker compose down -v` deletes these volumes**; do not use `-v` unless you want to wipe the devices.

The relay's ADB traffic and the scrcpy stream share port `5555`. To connect from another machine, configure a Cloudflare Tunnel TCP public hostname for the service.

The client and server prefix every connection with an explicit `ADB` or `SCRCPY` marker, so routing does not depend on network timing. The Redroid setup uses the same marker protocol as the emulator setup, so existing clients work unchanged. When the protocol changes in a future release, deploy the server files and `scrcpy-anywhere.exe` from the same folder version together.

<details>
<summary>Example Cloudflare Tunnel configuration</summary>

| Subdomain / Domain | Path | Service Type | URL |
| :--- | :--- | :--- | :--- |
| `emulator-1.example.com` | `*` | `TCP` | `localhost:5555` |

- **Subdomain**: Enter the subdomain you want to use, for example `emulator-1`.
- **Service Type**: Select `TCP`.
- **URL**: Use `localhost:5555`, the default ADB TCP port.

</details>

The relay ports are bound to `127.0.0.1` only. The loopback-only binding prevents bypassing Cloudflare Access from another machine. A Linux host is required; Docker Desktop on Windows and macOS is not supported.

ADB authentication stays inside the server: the relay's ADB server talks to Redroid on the internal Docker network, and its key is kept in the `relay-N-adb` volume. The client talks to that ADB server, so no `adbkey` file is copied to or required on the client.

### Ready state and unattended operation

The relay applies these settings every time it (re)connects to a freshly booted Redroid, so the device stays awake 24/7:

```bash
adb shell svc power stayon true
adb shell settings put system screen_off_timeout 2147483647
adb shell dumpsys deviceidle disable
```

If Redroid restarts (crash, `docker restart`, host reboot), the relay detects the lost connection, reconnects, and applies the settings again; no manual step is needed. Both containers use `restart: unless-stopped`.

`docker compose logs -f relay-1` shows the state. The server is ready when the log contains both:

```text
Tagged ADB/scrcpy relay listening on ...
Android device is ready: redroid-1:5555 (stay-on, no screen timeout, doze disabled).
```

These replace the emulator's `Android emulator is ready.` message. `docker compose ps` also shows the health: `redroid-N` is healthy when `sys.boot_completed` is `1`, and `relay-N` is healthy when the relay answers an ADB request through port 5555 and the device is booted and configured.

### Notes on the image

- **Android version: 12.** `redroid-script` installs libndk only for Android 11 and 12 (its `-n` option is limited to `11.0.0`, `12.0.0` and `12.0.0_64only`); MindTheGapps supports 12 through 15. Android 12 is the newest version that supports both. The base image is pinned to `redroid/redroid:12.0.0-240527` (the current `12.0.0-latest`).
- **ARM translation.** The official image includes only 64-bit libndk. The build adds the libndk_translation prebuilt used by `redroid-script`, which also includes 32-bit ARM (`armeabi-v7a`). Both 32-bit and 64-bit ARM binaries were tested.
- **arm64 hosts.** The build detects the architecture: on arm64 it adds the arm64 MindTheGapps and skips libndk (ARM apps run natively). The relay uses Ubuntu's `adb` package there, because Google's platform-tools are x86_64-only. Only x86_64 has been tested.
- **Codec2.** Redroid disables Codec2 (`debug.stagefright.ccodec=0`), which leaves no Opus encoder. scrcpy captures Opus audio by default, so without Codec2 the session fails. The image re-enables Codec2 with `/system/etc/init/scrcpy-anywhere.rc`.
- The image skips Google's setup wizard (`ro.setupwizard.mode=DISABLED`), as `redroid-script` does for MindTheGapps.

## Set up the Play Store

Redroid is not a Google-certified device, so the Play Store may refuse to sign in ("Device is not Play Protect certified"). Register the device once:

1. **Get the GSF Android ID.** The image includes `sqlite3`:

   ```bash
   docker exec relay-1 adb -s redroid-1:5555 shell \
     'su 0 sqlite3 /data/data/com.google.android.gsf/databases/gservices.db "select value from main where name = \"android_id\";"'
   ```

   If `sqlite3` is unavailable, copy the database out and read it on the host:

   ```bash
   docker exec relay-1 sh -c 'adb -s redroid-1:5555 exec-out su 0 cat /data/data/com.google.android.gsf/databases/gservices.db > /tmp/gservices.db'
   docker cp relay-1:/tmp/gservices.db .
   python3 -c 'import sqlite3; print(sqlite3.connect("gservices.db").execute("select value from main where name=\"android_id\"").fetchone()[0])'
   ```

   If the query returns nothing, GMS has not checked in yet. Wait a few minutes after the first boot (the device needs internet access) and try again.

2. **Register it.** While signed in with the Google account you will use on the device, open <https://www.google.com/android/uncertified/>, enter the number from step 1, and submit. It can take from a few minutes to several hours to take effect. Then run `docker restart redroid-1` (the relay reconnects automatically). If the Play Store still complains, also clear its data: `docker exec relay-1 adb -s redroid-1:5555 shell pm clear com.android.vending`. Do not clear `com.google.android.gsf`, because that generates a new Android ID that must be registered again.

   The ID stays the same as long as the `redroid-N-data` volume is kept. A new or wiped volume needs a new registration, and each instance is registered separately.

3. **Install the app.** Connect with the client, open the Play Store, sign in, and install the target app. To turn on automatic updates, open the Play Store, then tap the profile icon → **Settings** → **Network preferences** → **Auto-update apps** → **Over any network**.

## Android Emulator (alternative)

The previous emulator setup is still available through the `emulator` Compose profile. It needs `/dev/kvm`, and it publishes the same host ports (`5555`, `5556`, `5557`) as the relays, so stop the Redroid services first:

```bash
docker compose stop relay-1 redroid-1
docker compose --profile emulator up -d --build android-1
docker compose logs -f android-1
```

The emulator is ready when its log contains both `Android emulator is ready.` and `Tagged ADB/scrcpy relay listening`. Emulator data is kept in `emulators/android-N/`. Data is not migrated between the emulator and Redroid.

## Update and verify the server

Replace the complete project folder on the server, then run these commands from that folder:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f relay-1
```

Pressing `Ctrl+C` stops following the log but leaves the containers running.

Do not use `docker system prune -a -f` for a normal update: it removes unused images and caches belonging to unrelated projects too. Use `docker compose build --no-cache` only if reuse of a stale image has actually been confirmed.

Verification checklist:

```bash
# Both containers report "healthy"
docker compose ps

# Only 127.0.0.1 listens on the relay ports; redroid-N has no published port
sudo ss -ltnp | grep -E ':555[5-7]\b'
docker port redroid-1

# Automatic recovery: the relay log shows "Lost connection" and then "Android device is ready" again
docker restart redroid-1
docker compose logs -f relay-1

# Idle resource usage
docker stats --no-stream
```

From another machine, `nc -vz <server-ip> 5555` must fail. Finally, connect with the client as described below.

## Connect from a client

Run the following on a client machine:

```bash
python connector.py
```

1. Enter the Cloudflare Access TCP application hostname in **Access hostname**.
2. Leave **Local port** at its default value, `5555`.
3. Click **Connect**.
4. On first use, the application downloads the required tools and may open a Cloudflare Access sign-in flow. The tools are stored in a `tools` folder in the install folder (next to the `scrcpy-anywhere.exe` launcher; on macOS, `~/Library/Application Support/scrcpy-anywhere`), so they persist across launches and updates.

### Install and update

Extract `scrcpy-anywhere-<windows|linux>.zip/.tar.gz` from [Releases](https://github.com/insanephin/scrcpy-anywhere/releases) into a writable folder, or on macOS drag the app from the `.dmg` into `/Applications`. The top-level `scrcpy-anywhere` executable is a launcher that runs the newest installed `app-<version>` folder.

When a newer release exists, an **Update to vX.Y.Z** button appears in the header. It downloads the new version, verifies its SHA256, and unpacks it into a new `app-<version>` folder; restart to apply it. The running version is never modified, and the launcher keeps only the two newest versions.

The server and client must use the same marker protocol. Using a client and server from releases with different protocols causes a `protocol fault` or a rejected marker.

To end the session, click **Disconnect** or close the application. The most recently used hostname and port are saved to `adb-cloud-dashboard.json` in the user's home directory.

## Security notes

- ADB grants device-control privileges; do not expose it directly to the internet.
- Configure Cloudflare Access policies so only authorized users can reach the TCP application.
- **Redroid runs `--privileged`** (`privileged: true`). Android's init has to mount binderfs, cgroups and other filesystems and to change kernel parameters, which ordinary containers are not allowed to do. A privileged container has all capabilities and access to host devices, so an escape from Android (for example through a malicious app) amounts to root on the host. Run it only on a trusted, dedicated host, and install only apps you trust.
- Redroid's ADB (port 5555 inside the container) does not require authentication. It is reachable only from containers on this project's Docker network and is never published to the host. Do not add `ports:` to the `redroid-N` services.
- The `emulator` profile maps `/dev/kvm` (and `/dev/dri`) for emulator acceleration. The Redroid path does not need them.
- This project downloads the Redroid image, MindTheGapps, libndk_translation, the Android Emulator image, and external tools such as `scrcpy` and `cloudflared`. Review their licenses and distribution policies.

## License

[MIT](LICENSE)
