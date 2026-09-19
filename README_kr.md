# scrcpy-anywhere

Cloudflare Access로 보호된 ADB TCP 서비스에 연결하고, `scrcpy`를 실행하는 간단한 데스크톱 도구입니다. 
함께 제공되는 Docker Compose 구성으로 헤드리스 Android Emulator를 실행할 수도 있습니다.

## 구성

```text
Android Emulator (Docker) → ADB TCP :5555 → Cloudflare Access → cloudflared → adb → scrcpy
```

- `connector.py`: Cloudflare Access TCP 터널을 열고 ADB 및 scrcpy를 실행하는 Tkinter 데스크톱 앱
- `compose.yml`, `Dockerfile`, `entrypoint.sh`: ADB TCP(5555)를 활성화한 Android Emulator 컨테이너

## 테스트 환경

### 에뮬레이터 서버

- ubuntu 22.04
- Docker version 29.8.1

### 연결 클라이언트

- Windows 11 Pro 25H2
- python 3.10, Tkinter

## Android Emulator 실행

에뮬레이터 서버에서 다음을 실행합니다.
```bash
docker compose up --build -d
```

컨테이너가 부팅되면 내부 에뮬레이터의 ADB TCP 서비스가 5555 포트에서 활성화됩니다. 
외부에서 연결하는 것을 전재로 설계된 프로젝트로 Cloudflared tunnel 설정을 필요로 합니다.
<details>
<summary>Cloudflare Tunnel 설정 예시</summary>
| Subdomain / Domain | Path | Service Type | URL |
| :--- | :--- | :--- | :--- |
| `emulator-1.example.com` | `*` | `TCP` | `localhost:5555` |

- **Subdomain**: 원하는 도메인 입력 (예: `emulator-1`)
- **Service Type**: `TCP` 선택
- **URL**: `localhost:5555` (ADB 기본 포트)
</details>

> [!IMPORTANT]
> 현재 Compose 설정은 Linux의 host networking을 사용합니다.
> 따라서 Windows 및 macOS용 Docker Desktop 환경은 지원 대상이 아닙니다.

## 클라이언트 연결

빌드파일 또는 다음을 입력하여 실행합니다. 
```bash
python connector.py
```

> [!IMPORTANT]
> Linux에서는 `scrcpy`를 별도로 설치해야 합니다.

1. **Access hostname**에 Cloudflare Access TCP 애플리케이션 hostname을 입력합니다.
2. **Local port**는 기본값 `5555`를 사용합니다.
3. **Connect**를 누릅니다.
4. 최초 실행 시 필요한 도구 다운로드 및 Cloudflare Access 로그인이 진행될 수 있습니다. 내려받은 도구는 실행한 애플리케이션(예: `scrcpy-anywhere.exe`)과 같은 폴더의 `tools`에 저장됩니다.

연결을 종료하려면 앱에서 **Disconnect**를 누르거나 창을 닫습니다. 마지막 hostname과 포트는 사용자 홈 디렉터리의 `adb-cloud-dashboard.json`에 저장됩니다.

## 주의 사항

- ADB는 기기 제어 권한을 제공하므로 인터넷에 평문으로 직접 노출하면 안 됩니다.
- Cloudflare Access 정책에서 허용된 사용자만 TCP 애플리케이션에 접근하도록 설정하세요.
- 현재 컨테이너는 KVM 접근을 위해 privileged mode를 사용합니다. 신뢰하는 Linux 호스트에서만 실행하세요.
- 이 프로젝트는 Android Emulator 이미지와 `scrcpy`, `cloudflared` 등 외부 도구를 내려받습니다. 각 도구의 라이선스와 배포 정책을 확인하세요.

## 라이선스

[MIT](LICENSE)
