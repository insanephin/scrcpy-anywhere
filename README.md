# scrcpy-anywhere

A simple desktop application that connects to an ADB TCP service protected by Cloudflare Access and launches `scrcpy`. The included Docker Compose configuration can also run a headless Android Emulator.

## Architecture

```text
Android Emulator (Docker) → ADB TCP :5555 → Cloudflare Access → cloudflared → adb → scrcpy
```

- `connector.py`: A Tkinter desktop application that opens a Cloudflare Access TCP tunnel, then runs ADB and scrcpy.
- `compose.yml`, `Dockerfile`, `entrypoint.sh`: An Android Emulator container with ADB TCP enabled on port 5555.

## Tested environment

### Emulator server

- Ubuntu 22.04
- Docker 29.8.1

### Client

- Python 3.10
- Tkinter

> [!IMPORTANT]
> On Linux, install `scrcpy` separately before using the client.

## Run the Android Emulator

On the emulator server, run:

```bash
docker compose up --build -d
```

Once the container has started, ADB TCP is enabled on port `5555` for the emulator inside it. To connect from another machine, configure a Cloudflare Tunnel TCP public hostname for the service.

<details>
<summary>Example Cloudflare Tunnel configuration</summary>

| Subdomain / Domain | Path | Service Type | URL |
| :--- | :--- | :--- | :--- |
| `emulator-1.example.com` | `*` | `TCP` | `localhost:5555` |

- **Subdomain**: Enter the subdomain you want to use, for example `emulator-1`.
- **Service Type**: Select `TCP`.
- **URL**: Use `localhost:5555`, the default ADB TCP port.

</details>

The Compose configuration publishes emulator 1 at `127.0.0.1:5555` and emulator 2 at `127.0.0.1:5556`. The loopback-only binding prevents bypassing Cloudflare Access from another machine. A Linux host with `/dev/kvm` is required; Docker Desktop on Windows and macOS is not supported.

ADB authentication is still active. Copy the matching server-side ADB private key to `tools/adbkey` on the client using a secure channel before connecting. Never commit or share that private key publicly.

## Connect from a client

Run the following on a client machine:

```bash
python connector.py
```

1. Enter the Cloudflare Access TCP application hostname in **Access hostname**.
2. Leave **Local port** at its default value, `5555`.
3. Click **Connect**.
4. On first use, the application downloads the required tools and may open a Cloudflare Access sign-in flow. The tools are stored in a `tools` folder next to the launched application (for example, next to `scrcpy-anywhere.exe`), so they persist across launches.

To end the session, click **Disconnect** or close the application. The most recently used hostname and port are saved to `adb-cloud-dashboard.json` in the user's home directory.

## Security notes

- ADB grants device-control privileges; do not expose it directly to the internet.
- Configure Cloudflare Access policies so only authorized users can reach the TCP application.
- The container maps `/dev/kvm` for emulator acceleration. Run it only on a trusted Linux host.
- This project downloads an Android Emulator image and external tools such as `scrcpy` and `cloudflared`. Review their licenses and distribution policies.

## License

[MIT](LICENSE)
