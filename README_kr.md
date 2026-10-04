# scrcpy-anywhere

Cloudflare Access로 보호된 ADB TCP 서비스에 연결하고, `scrcpy`를 실행하는 간단한 데스크톱 도구입니다. 
함께 제공되는 Docker Compose 구성으로 서버에서 Android를 실행합니다. 기본은 [Redroid](https://github.com/remote-android/redroid-doc)(컨테이너 Android, Play 스토어 및 ARM 번역 포함)을 사용합니다.

## 구성

```text
Redroid ─(내부 Docker 네트워크)→ relay: ADB 서버/scrcpy 스트림 통합 :5555 → Cloudflare Access → cloudflared → 표식 프록시 → adb + scrcpy
```

- `connector.py`: Cloudflare Access TCP 터널을 열고 ADB 및 scrcpy를 실행하는 Tkinter 데스크톱 앱
- `redroid/`: MindTheGapps(Play 스토어)를 포함한 Redroid 12 이미지 빌드. arm64 앱은 베이스 이미지의 libndk_translation으로 x86_64에서 실행
- `relay/`, `relay.py`: Redroid에 연결된 ADB 서버를 유지하고, ADB와 scrcpy 스트림을 5555 포트 하나로 전달하는 사이드카
- `compose.yml`: 기본으로 2세트(`redroid-1` + `relay-1`, `redroid-2` + `relay-2`)

## 테스트 환경

### 서버

- Ubuntu 22.04 (커널 5.15), x86_64
- Docker 29.8.1, Docker Compose v5.5.1

### 연결 클라이언트

- Windows 11 Pro 25H2
- python 3.10, Tkinter

## 호스트 준비 (Redroid)

Redroid는 호스트 커널을 공유하며 `binder_linux` 모듈이 필요합니다. KVM(`/dev/kvm`)은 필요하지 않습니다. Ubuntu 22.04 기준:

```bash
sudo apt install linux-modules-extra-$(uname -r)
sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"
```

재부팅 후 자동으로 로드되게 설정합니다.

```bash
echo binder_linux | sudo tee /etc/modules-load.d/redroid.conf
echo 'options binder_linux devices="binder,hwbinder,vndbinder"' | sudo tee /etc/modprobe.d/redroid.conf
```

`lsmod | grep binder_linux`로 확인합니다. Redroid는 컨테이너 안에서 binderfs로 binder 장치를 만들기 때문에 호스트에 `/dev/binder`가 보이지 않아도 정상입니다. Compose가 `androidboot.use_memfd=1`을 사용하므로 `ashmem_linux`는 필요 없습니다.

호스트에 `/dev/dma_heap/system`도 있어야 합니다(Ubuntu 22.04 기본 커널에 있음). scrcpy 기본 오디오(Opus)에 쓰이는 Codec2 소프트웨어 인코더가 이 장치를 사용합니다([이미지 관련 참고](#이미지-관련-참고) 참조).

> [!NOTE]
> 커널을 업그레이드하면 새 커널용 `linux-modules-extra`도 설치해야 합니다. 설치하지 않으면 재부팅 후 `binder_linux`가 없어 Redroid가 부팅하지 못합니다.

## 빌드 및 실행

서버에서 다음을 실행합니다.
```bash
docker compose up -d --build
```

첫 빌드 때 Redroid 베이스 이미지(약 1GB)와 MindTheGapps 압축 파일(SHA256 검증)을 내려받습니다. 첫 부팅은 1~2분, 이후 부팅은 30초 정도 걸립니다. 첫 번째 인스턴스만 필요하면 `docker compose up -d --build redroid-1 relay-1`을 실행하세요.

인스턴스마다 컨테이너 2개로 구성됩니다.

| 컨테이너 | 역할 | 호스트 포트 |
| :--- | :--- | :--- |
| `redroid-1` / `redroid-2` | Android 12(Redroid). ADB는 내부 Docker 네트워크에만 노출 | 없음 |
| `relay-1` / `relay-2` | ADB 서버 + 표식 기반 ADB/scrcpy 릴레이. `adb connect redroid-N:5555`로 연결 | `127.0.0.1:5555` / `127.0.0.1:5556` |

설정은 환경 변수 또는 `compose.yml` 옆의 `.env` 파일로 바꿀 수 있습니다.

| 변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `REDROID_WIDTH` / `REDROID_HEIGHT` | `720` / `1280` | 화면 해상도 |
| `REDROID_DPI` | `320` | 화면 밀도 |
| `REDROID_GPU_MODE` | `guest` | `guest`: CPU 소프트웨어 렌더링, `host`: 호스트 GPU(`/dev/dri`, Mesa를 통한 AMD/Intel)로 렌더링 |
| `REDROID_FPS` | `30` | 화면 FPS(낮출수록 CPU 절약) |
| `REDROID_CCODEC` | `1` | Codec2 모드. `0`이면 Redroid 기본값이지만 scrcpy 기본 Opus 오디오가 실패함 |
| `RELAY_1_PORT` / `RELAY_2_PORT` | `5555` / `5556` | 호스트 포트(항상 `127.0.0.1`에만 바인딩) |

기본 렌더링은 소프트웨어 방식(`REDROID_GPU_MODE=guest`)이라 GPU가 필요 없습니다. 호스트에 AMD나 Intel GPU가 있으면 `REDROID_GPU_MODE=host`로 렌더링을 GPU에 넘겨 CPU 사용량을 크게 줄일 수 있습니다. Ryzen 5 6600H(Radeon 680M)에서 UI를 스크롤했을 때 `guest`는 CPU 2개 상한에 걸려 22~28fps였고, `host`는 CPU 약 0.85개로 30fps, 약 1.2~1.45개로 60fps가 나왔습니다. `host` 모드에서 남는 CPU 사용량은 대부분 scrcpy용 소프트웨어 영상 인코딩이며, 클라이언트가 접속해 있을 때만 발생합니다. 인스턴스별 데이터는 로컬 `emulators/redroid-N/` 폴더에 저장되므로 `docker compose down`이나 이미지를 다시 빌드해도 유지됩니다.

- `emulators/redroid-N/data/`: Android `/data`(설치한 앱, 게임 데이터, Google 계정, GSF Android ID)
- `emulators/redroid-N/adb/`: 릴레이의 ADB 키

파일 소유자가 root와 Android 사용자 ID이므로 백업하거나 지울 때는 `sudo`가 필요합니다. 백업할 때는 먼저 인스턴스를 멈추고(`docker compose stop relay-1 redroid-1`) 소유자와 확장 속성을 유지해 복사하세요(예: `sudo tar --xattrs -czpf redroid-1.tgz -C emulators redroid-1`). `emulators/redroid-N/data/`를 지우면 기기가 초기화됩니다. `emulators/`는 Git과 Docker 빌드 컨텍스트에서 제외됩니다.

릴레이의 ADB 제어 연결과 scrcpy 스트림이 5555 포트 하나를 함께 사용합니다.
외부에서 연결하는 것을 전제로 설계된 프로젝트로 Cloudflared tunnel 설정을 필요로 합니다.

클라이언트와 서버는 각 연결에 `ADB` 또는 `SCRCPY` 표식을 사용해 시간 지연에 관계없이 같은 포트에서 정확히 구분합니다. Redroid 구성도 이전 Android Emulator 구성과 같은 표식 규칙을 사용하므로 기존 클라이언트를 그대로 쓸 수 있습니다. 앞으로 표식 규칙이 바뀌는 릴리스에서는 이 폴더의 서버 파일과 `scrcpy-anywhere.exe`를 함께 교체하세요.
<details>
<summary>Cloudflare Tunnel 설정 예시</summary>

| Subdomain / Domain | Path | Service Type | URL |
| :--- | :--- | :--- | :--- |
| `emulator-1.example.com` | `*` | `TCP` | `localhost:5555` |

- **Subdomain**: 원하는 도메인 입력 (예: `emulator-1`)
- **Service Type**: `TCP` 선택
- **URL**: `localhost:5555` (ADB 기본 포트)
</details>

릴레이 포트는 `127.0.0.1`에만 바인딩됩니다. 로컬 루프백으로만 바인딩하여 다른 장비가 Cloudflare Access를 우회하지 못하게 합니다. Linux 호스트가 필요하며 Windows 및 macOS용 Docker Desktop은 지원 대상이 아닙니다.

ADB 인증은 서버 안에서만 처리됩니다. 릴레이의 ADB 서버가 내부 Docker 네트워크로 Redroid와 통신하며, 키는 `emulators/redroid-N/adb/`에 보관됩니다. 클라이언트는 이 ADB 서버를 사용하므로 클라이언트에 `adbkey`를 복사하거나 생성할 필요가 없습니다.

### 준비 완료 확인과 24시간 무인 운영

릴레이는 새로 부팅된 Redroid에 (재)연결할 때마다 다음 설정을 적용해 기기가 계속 깨어 있게 합니다.

```bash
adb shell svc power stayon true
adb shell settings put system screen_off_timeout 2147483647
adb shell dumpsys deviceidle disable
```

Redroid가 재시작되면(크래시, `docker restart`, 호스트 재부팅) 릴레이가 연결 끊김을 감지해 다시 연결하고 설정을 다시 적용합니다. 수동 작업은 필요 없습니다. 두 컨테이너 모두 `restart: unless-stopped`입니다.

`docker compose logs -f relay-1`로 상태를 볼 수 있습니다. 로그에 다음 두 문구가 모두 나오면 준비된 것입니다.

```text
Tagged ADB/scrcpy relay listening on ...
Android device is ready: redroid-1:5555 (stay-on, no screen timeout, doze disabled).
```

이전 Android Emulator 구성의 `Android emulator is ready.` 대신 두 번째 문구를 확인하세요. `docker compose ps`에서도 상태를 볼 수 있습니다. `redroid-N`은 `sys.boot_completed`가 `1`이면 healthy입니다. `relay-N`은 5555 포트로 ADB 요청에 응답하고 기기가 부팅·설정 완료 상태이면 healthy입니다.

### 이미지 관련 참고

- **Android 버전: 12.** `redroid-script`가 MindTheGapps와 libndk를 모두 지원하는 최신 버전이고(`-n` 옵션이 `11.0.0`, `12.0.0`, `12.0.0_64only`로 제한), 공식 이미지에 동작하는 arm64 libndk_translation이 들어 있습니다. 베이스 이미지는 `redroid/redroid:12.0.0-240527`(현재 `12.0.0-latest`와 동일)로 고정했습니다.
- **ARM 번역: arm64 전용.** 공식 이미지에 들어 있는 libndk_translation을 그대로 쓰며, arm64(`arm64-v8a`)만 번역합니다. 32비트 ARM(`armeabi-v7a`) 라이브러리만 들어 있는 앱은 실행되지 않습니다. `redroid-script`의 libndk 프리빌트(`-n`)는 추가하지 마세요. Android 12에서 이미지의 라이브러리를 덮어써서 arm64 앱이 시작하자마자 `ndk_translation::AppProcessPostInit()`에서 크래시합니다(Grow Castle로 확인).
- **arm64 호스트.** 빌드가 아키텍처를 감지합니다. arm64에서는 arm64용 MindTheGapps를 넣으며, ARM 앱은 네이티브로 실행됩니다. Google platform-tools는 x86_64 전용이라 릴레이는 Ubuntu의 `adb` 패키지를 사용합니다. 테스트는 x86_64에서만 했습니다.
- **Codec2.** Redroid는 Codec2를 끄는데(`debug.stagefright.ccodec=0`), 그러면 Opus 인코더가 없습니다. scrcpy는 기본으로 Opus 오디오를 캡처하므로 Codec2 없이는 세션이 실패합니다. 이미지에서 `/system/etc/init/scrcpy-anywhere.rc`로 Codec2를 다시 켭니다.
- MindTheGapps에 대해 `redroid-script`가 하는 것처럼 Google 설정 마법사를 건너뜁니다(`ro.setupwizard.mode=DISABLED`).

## Play 스토어 초기 설정

Redroid는 Google 인증을 받지 않은 기기라 Play 스토어 로그인이 막힐 수 있습니다("Device is not Play Protect certified"). 다음 절차로 기기를 한 번 등록합니다.

1. **GSF Android ID 확인.** 이미지에 `sqlite3`가 들어 있습니다.

   ```bash
   docker exec relay-1 adb -s redroid-1:5555 shell \
     'su 0 sqlite3 /data/data/com.google.android.gsf/databases/gservices.db "select value from main where name = \"android_id\";"'
   ```

   `sqlite3`를 쓸 수 없으면 DB를 꺼내 호스트에서 읽습니다.

   ```bash
   docker exec relay-1 sh -c 'adb -s redroid-1:5555 exec-out su 0 cat /data/data/com.google.android.gsf/databases/gservices.db > /tmp/gservices.db'
   docker cp relay-1:/tmp/gservices.db .
   python3 -c 'import sqlite3; print(sqlite3.connect("gservices.db").execute("select value from main where name=\"android_id\"").fetchone()[0])'
   ```

   결과가 비어 있으면 아직 GMS 체크인이 되지 않은 것입니다. 첫 부팅 후 몇 분(인터넷 연결 필요) 기다렸다가 다시 시도하세요.

2. **등록.** 기기에서 쓸 Google 계정으로 로그인한 브라우저에서 <https://www.google.com/android/uncertified/>를 열고, 1번에서 확인한 숫자를 입력해 등록합니다. 반영까지 몇 분에서 몇 시간이 걸릴 수 있습니다. 반영된 뒤 `docker restart redroid-1`을 실행합니다(릴레이는 자동으로 다시 연결됨). 그래도 Play 스토어가 거부하면 Play 스토어 데이터를 지웁니다: `docker exec relay-1 adb -s redroid-1:5555 shell pm clear com.android.vending`. `com.google.android.gsf`의 데이터는 지우지 마세요. Android ID가 새로 생성되어 다시 등록해야 합니다.

   ID는 `emulators/redroid-N/data/`가 유지되는 한 바뀌지 않습니다. 데이터 폴더를 새로 만들거나 지웠다면 다시 등록해야 하며, 인스턴스마다 따로 등록합니다.

3. **앱 설치.** 클라이언트로 접속해 Play 스토어를 열고 로그인한 뒤 대상 앱을 설치합니다. 자동 업데이트는 Play 스토어 → 프로필 아이콘 → **설정** → **네트워크 환경설정** → **앱 자동 업데이트** → **모든 네트워크 사용**으로 켭니다.

## 서버 업데이트 및 확인

서버에 이 폴더 전체를 교체한 뒤, 폴더 안에서 다음을 실행합니다.

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f relay-1
```

`Ctrl+C`로 로그 보기만 종료해도 컨테이너는 계속 실행됩니다.

`docker system prune -a -f`는 이 프로젝트와 관계없는 사용 중이 아닌 이미지와 캐시까지 삭제하므로 일반 업데이트에는 필요하지 않습니다. 이전 이미지가 정말 재사용되는 문제가 확인된 경우에만 `docker compose build --no-cache`를 사용하세요.

검증 체크리스트:

```bash
# 두 컨테이너가 모두 "healthy"
docker compose ps

# 릴레이 포트는 127.0.0.1에서만 열리고, redroid-N은 공개된 포트가 없음
sudo ss -ltnp | grep -E ':555[5-7]\b'
docker port redroid-1

# 자동 복구: 릴레이 로그에 "Lost connection" 후 다시 "Android device is ready"가 나와야 함
docker restart redroid-1
docker compose logs -f relay-1

# 대기 상태 리소스 사용량
docker stats --no-stream
```

다른 장비에서 `nc -vz <서버 IP> 5555`는 실패해야 합니다. 마지막으로 아래 방법대로 클라이언트로 접속해 확인합니다.

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
4. 최초 실행 시 필요한 도구 다운로드 및 Cloudflare Access 로그인이 진행될 수 있습니다. 내려받은 도구는 설치 폴더(`scrcpy-anywhere.exe` 런처가 있는 폴더, macOS는 `~/Library/Application Support/scrcpy-anywhere`)의 `tools`에 저장됩니다.

### 설치와 업데이트

[Releases](https://github.com/insanephin/scrcpy-anywhere/releases)에서 `scrcpy-anywhere-<windows|linux>.zip/.tar.gz`를 쓰기 가능한 폴더에 풀거나, macOS는 `.dmg`의 앱을 `/Applications`로 옮깁니다. 최상위 `scrcpy-anywhere` 실행 파일은 런처이며, 설치된 `app-<버전>` 폴더 중 가장 최신 버전을 실행합니다.

새 릴리스가 있으면 앱 상단에 **Update to vX.Y.Z** 버튼이 나타납니다. 누르면 새 버전을 받아 SHA256을 확인한 뒤 새 `app-<버전>` 폴더에 풀고, 재시작하면 적용됩니다. 실행 중인 버전은 건드리지 않으며, 최근 두 버전만 남기고 나머지는 런처가 정리합니다.

서버와 클라이언트는 같은 표식 규칙을 사용해야 합니다. 표식 규칙이 다른 릴리스의 서버와 클라이언트를 섞어 쓰면 `protocol fault` 또는 표식 거부가 발생합니다.

연결을 종료하려면 앱에서 **Disconnect**를 누르거나 창을 닫습니다. 마지막 hostname과 포트는 사용자 홈 디렉터리의 `adb-cloud-dashboard.json`에 저장됩니다.

## 주의 사항

- ADB는 기기 제어 권한을 제공하므로 인터넷에 평문으로 직접 노출하면 안 됩니다.
- Cloudflare Access 정책에서 허용된 사용자만 TCP 애플리케이션에 접근하도록 설정하세요.
- **Redroid는 `--privileged`(`privileged: true`)로 실행됩니다.** Android init이 binderfs, cgroup 등 파일시스템을 마운트하고 커널 파라미터를 바꿔야 하는데, 일반 컨테이너에서는 허용되지 않기 때문입니다. privileged 컨테이너는 모든 capability와 호스트 장치 접근 권한을 가지므로, Android 내부에서 탈출하면(예: 악성 앱) 호스트 root 권한을 얻는 것과 같습니다. 신뢰할 수 있는 전용 호스트에서만 실행하고, 신뢰할 수 있는 앱만 설치하세요.
- Redroid의 ADB(컨테이너 내부 5555)는 인증을 요구하지 않습니다. 이 프로젝트의 Docker 네트워크에 있는 컨테이너에서만 접근할 수 있고 호스트에는 공개되지 않습니다. `redroid-N` 서비스에 `ports:`를 추가하지 마세요.
- 이 프로젝트는 Redroid 이미지, MindTheGapps, libndk_translation과 `scrcpy`, `cloudflared` 등 외부 도구를 내려받습니다. 각 도구의 라이선스와 배포 정책을 확인하세요.

## 라이선스

[MIT](LICENSE)
