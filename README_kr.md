# scrcpy-anywhere

Cloudflare Access로 보호된 ADB TCP 서비스에 연결하고, `scrcpy`를 실행하는 간단한 데스크톱 도구입니다. 
함께 제공되는 Docker Compose 구성으로 헤드리스 Android Emulator를 실행할 수도 있습니다.

## 구성

```text
Android Emulator → 컨테이너 ADB 서버/scrcpy 스트림 통합 :5555 → Cloudflare Access → cloudflared → 표식 프록시 → adb + scrcpy
```

- `connector.py`: Cloudflare Access TCP 터널을 열고 ADB 및 scrcpy를 실행하는 Tkinter 데스크톱 앱
- `compose.yml`, `Dockerfile`, `entrypoint.sh`: 인증된 ADB 서버와 scrcpy 스트림을 5555 포트 하나로 전달하는 Android Emulator 컨테이너

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

컨테이너가 부팅되면 ADB 제어 연결과 scrcpy 스트림이 5555 포트 하나를 함께 사용합니다.
외부에서 연결하는 것을 전재로 설계된 프로젝트로 Cloudflared tunnel 설정을 필요로 합니다.

클라이언트와 서버는 각 연결에 `ADB` 또는 `SCRCPY` 표식을 사용해 시간 지연에 관계없이 같은 포트에서 정확히 구분합니다. 이 표식 규칙이 맞아야 하므로, 업데이트할 때는 이 폴더의 서버 파일과 `scrcpy-anywhere.exe`를 함께 교체하세요.
<details>
<summary>Cloudflare Tunnel 설정 예시</summary>
| Subdomain / Domain | Path | Service Type | URL |
| :--- | :--- | :--- | :--- |
| `emulator-1.example.com` | `*` | `TCP` | `localhost:5555` |

- **Subdomain**: 원하는 도메인 입력 (예: `emulator-1`)
- **Service Type**: `TCP` 선택
- **URL**: `localhost:5555` (ADB 기본 포트)
</details>

Compose 설정은 에뮬레이터 1을 `127.0.0.1:5555`, 에뮬레이터 2를 `127.0.0.1:5556`에 공개합니다. 로컬 루프백으로만 바인딩하여 다른 장비가 Cloudflare Access를 우회하지 못하게 합니다. `/dev/kvm`을 사용할 수 있는 Linux 호스트가 필요하며 Windows 및 macOS용 Docker Desktop은 지원 대상이 아닙니다.

ADB 인증은 컨테이너의 ADB 서버와 에뮬레이터 사이에서만 처리됩니다. 클라이언트는 이미 인증된 서버 ADB를 사용하므로 클라이언트에 `adbkey`를 복사하거나 생성할 필요가 없습니다.

## 서버 업데이트 및 확인

서버에 이 폴더 전체를 교체한 뒤, 폴더 안에서 다음을 실행합니다.

```bash
docker compose down
docker compose up -d --build
docker compose logs -f android-1
```

로그에 `Android emulator is ready.`와 `Tagged ADB/scrcpy relay listening` 두 문구가 나오면 준비된 것입니다. `Ctrl+C`로 로그 보기만 종료해도 컨테이너는 계속 실행됩니다. 에뮬레이터 1만 필요하면 두 번째 명령 끝에 `android-1`을 붙이세요.

`docker system prune -a -f`는 이 프로젝트와 관계없는 사용 중이 아닌 이미지와 캐시까지 삭제하므로 일반 업데이트에는 필요하지 않습니다. 이전 이미지가 정말 재사용되는 문제가 확인된 경우에만 `docker compose build --no-cache`를 사용하세요.

## 클라이언트 연결

제공된 `scrcpy-anywhere.exe`를 실행하거나 다음을 입력합니다.
```bash
python connector.py
```

> [!IMPORTANT]
> Linux에서는 `scrcpy`를 별도로 설치해야 합니다.

1. **Access hostname**에 Cloudflare Access TCP 애플리케이션 hostname을 입력합니다.
2. **Local port**는 기본값 `5555`를 사용합니다.
3. **Connect**를 누릅니다.
4. 최초 실행 시 필요한 도구 다운로드 및 Cloudflare Access 로그인이 진행될 수 있습니다. 내려받은 도구는 실행한 애플리케이션(예: `scrcpy-anywhere.exe`)과 같은 폴더의 `tools`에 저장됩니다.

이 수정본은 서버와 클라이언트가 세트입니다. 기존 서버에 새 EXE만 교체하거나, 새 서버에 기존 EXE를 사용하면 `protocol fault` 또는 표식 거부가 발생합니다.

연결을 종료하려면 앱에서 **Disconnect**를 누르거나 창을 닫습니다. 마지막 hostname과 포트는 사용자 홈 디렉터리의 `adb-cloud-dashboard.json`에 저장됩니다.

## 주의 사항

- ADB는 기기 제어 권한을 제공하므로 인터넷에 평문으로 직접 노출하면 안 됩니다.
- Cloudflare Access 정책에서 허용된 사용자만 TCP 애플리케이션에 접근하도록 설정하세요.
- 현재 컨테이너는 가속을 위해 `/dev/kvm`을 매핑합니다. 신뢰하는 Linux 호스트에서만 실행하세요.
- 이 프로젝트는 Android Emulator 이미지와 `scrcpy`, `cloudflared` 등 외부 도구를 내려받습니다. 각 도구의 라이선스와 배포 정책을 확인하세요.

## 라이선스

[MIT](LICENSE)
