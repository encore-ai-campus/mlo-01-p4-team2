# WSL2 운영 준비

Windows 두 PC의 WSL2·Ubuntu에서 Python·Nginx·Redis를 직접 실행한다.
서버 실행은 [README](README.md), 외부 입구 연결은 [ingress.md](ingress.md)를 따른다.

## 1. Windows와 Ubuntu

관리자 PowerShell에서 설치 가능한 배포판을 확인한다.

```powershell
wsl --list --online
wsl --install -d Ubuntu-24.04
wsl --list --verbose
wsl --version
```

- Ubuntu의 WSL 버전이 `2`인지 확인한다.
- 배포판 이름은 실제 설치한 이름을 사용한다.
- 코드는 Ubuntu의 `~/mlo-01-p4-team2/platform_server`처럼 Linux 파일 시스템에 둔다.
- Redis 데이터와 수집한 정적 파일도 Linux 파일 시스템에 둔다.
- 설치·패키지 업데이트는 운영자가 수행한다. 이 저장소는 Windows 설정을 자동 변경하지 않는다.

근거: [WSL 설치](https://learn.microsoft.com/en-us/windows/wsl/install),
[Linux 파일 시스템 사용](https://learn.microsoft.com/en-us/windows/wsl/filesystems).

## 2. systemd와 서버 시작

Ubuntu에서 `ps -p 1 -o comm=`으로 systemd 사용 여부를 확인한다.
필요하면 `/etc/wsl.conf`에 다음 설정을 추가한다. 기존 설정은 보존한다.

```ini
[boot]
systemd=true
```

변경 후 PowerShell의 `wsl --shutdown`으로 종료하고 Ubuntu를 다시 시작한다.
실행 중인 다른 WSL 작업도 종료되므로 운영 중에는 계획한 시간에 수행한다.

Ubuntu에서 [systemd 예시](playdata-platform@.service)의 경로·사용자를 수정한 뒤 등록한다.

```bash
sudo systemctl enable redis-server
sudo systemctl enable playdata-platform@movement  # PC 1
# PC 2에서는 movement 대신 service
```

- `enable`은 해당 Ubuntu가 시작될 때 서비스를 시작한다.
- **Windows 부팅 시 WSL을 시작하는 작업은 별도다.**
- systemd 서비스만으로 WSL 인스턴스가 계속 실행된다고 가정하지 않는다.

Windows 작업 스케줄러에는 다음 항목을 운영자가 등록한다.

| 항목 | 값 |
|---|---|
| 실행 계정 | 해당 Ubuntu 배포판을 설치한 Windows 계정 |
| 트리거 | Windows 시작 시 |
| 프로그램 | `wsl.exe` |
| 인수 예시 | `-d Ubuntu-24.04 --exec /usr/bin/sleep infinity` |
| 운영 설정 | 시작 지연·실패 재시도·실행 시간 제한 해제 |

이 작업은 WSL 실행을 유지한다. 앱의 재시작은 systemd가 담당한다.
재부팅·로그아웃·절전 복귀 후 앱과 Redis 상태를 실제로 확인한다.
절전·최대 절전·자동 업데이트 재부팅 정책은 서버 운영 시간에 맞춘다.

근거: [WSL systemd 지원과 수명 제한](https://learn.microsoft.com/en-us/windows/wsl/systemd).

## 3. 두 PC 사이의 연결

| 네트워크 방식 | 확인할 항목 |
|---|---|
| Mirrored | Windows 11 22H2 이상, WSL 버전, Hyper-V·Windows 방화벽 |
| 기본 NAT | Windows 사설 IP → 해당 WSL IP로 필요한 포트 전달 |

- Mirrored를 사용해도 앱의 바인딩 주소와 방화벽 허용이 필요하다.
- NAT의 WSL IP는 재시작 후 바뀔 수 있다. 포트 전달 대상도 갱신해야 한다.
- Windows 10과 다른 지원 범위는 설치된 OS·WSL 버전에서 확인한다.
- 전체 방화벽을 끄거나 모든 포트를 허용하지 않는다.

현재 코드에는 Redis 교차 접근이 남아 있다.
PC 1·PC 2 모두 같은 이동 Redis와 서비스 Redis에 연결해야 한다.

| 연결 | 허용 범위 |
|---|---|
| 같은 WSL의 Front → 뒤 Nginx → Backend | `127.0.0.1` |
| 다른 PC의 Backend·jobs → Redis | 상대 PC의 사설 주소·Redis 포트만 |
| 후속 외부 연결 프로그램 → Front·정적 파일 | [입구 명세](ingress.md)의 포트만 |
| 두 PC → 외부 MySQL | 기존 클러스터 또는 Router의 허용 주소·포트 |

Redis는 [설정 예시](redis.conf.example)를 참고한다.
인증·보호 모드·사설 바인딩을 유지하고, 상대 PC의 주소만 방화벽으로 허용한다.
사설망을 신뢰할 수 없다면 기존 보안 통신 경로를 사용한다.

근거: [WSL 네트워크·NAT·Mirrored 조건](https://learn.microsoft.com/en-us/windows/wsl/networking).

## 4. 재부팅 후 점검

- 해당 Windows 계정의 Ubuntu 배포판과 작업 스케줄러 상태 확인.
- `systemctl status redis-server playdata-platform@movement` 확인. PC 2는 `service` 사용.
- 같은 WSL에서 Front `/health/` 응답 확인.
- 두 PC에서 이동·서비스 Redis 인증 접속 확인.
- 외부 MySQL 연결과 실제 인증 HTTP 요청 확인.
- 이동·채팅 연결과 정적 파일 응답 확인.

WSL·Windows·방화벽·두 PC의 실제 네트워크는 이 작업 환경에서 검증하지 않았다.
150명 성능과 장비 장애 시 복구는 실제 PC에서 검증해야 한다.

## 5. 변경 이력

| 날짜 | 변경 | 변경한 절 |
|---|---|---|
| 2026-10-23 | WSL2 직접 실행과 운영 확인 절차 작성 | 1. Windows와 Ubuntu · 2. systemd와 서버 시작 · 3. 두 PC 사이의 연결 · 4. 재부팅 후 점검 · 5. 변경 이력 |
