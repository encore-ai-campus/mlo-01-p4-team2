# Local Lakehouse 인프라 구축 설계안

## 1. 개요

### 1.1. 구축 목적

로컬 VM 환경에 Apache Iceberg 기반 데이터 레이크하우스를 구축하여 데이터 수집, 저장, 처리, 분석 및 운영을 테스트한다.

초기에는 로컬 S3 호환 Object Storage인 RustFS를 사용하고, 추후 AWS S3로 전환할 수 있도록 Storage, Compute, Catalog 계층을 분리한다.

### 1.2. 기본 환경

| 항목 | 구성 |
|---|---|
| Host | Apple Silicon Mac |
| Host RAM | 24GB |
| Architecture | AArch64 (ARM64) |
| Hypervisor | VMware Fusion |
| Guest OS | Ubuntu Server 24.04 LTS ARM64 |
| VM | 3대 |
| VM Network | Private Internal Network |
| Internet Access | Mac Tinyproxy 경유 |
| HTTPS Proxy | HTTP CONNECT |
| Container Runtime | Docker Engine |
| Deployment | Docker Compose |
| Object Storage | RustFS |
| Data Format | Apache Parquet |
| Table Format | Apache Iceberg |
| Catalog | Apache Polaris |
| Catalog Database | PostgreSQL |
| Compute Engine | Apache Spark / PySpark |

### 1.3. 설계 원칙

- VM 3대 모두 내부망에 배치한다.
- VM은 인터넷에 직접 연결하지 않는다.
- 외부 HTTPS 요청은 Mac의 Tinyproxy를 경유한다.
- VM 간 내부 서비스 통신에는 Proxy를 사용하지 않는다.
- Mac에서 각 VM으로 SSH 접속하여 관리한다.
- 각 VM은 Docker Compose를 통해 서비스를 운영한다.
- 데이터와 메타데이터는 영속 볼륨에 저장한다.
- S3 Endpoint 및 인증 정보를 외부 설정으로 관리한다.
- 초기 환경은 기능 검증용이며 고가용성(HA)은 범위에서 제외한다.

---

## 2. VM 구성

### 2.1. 리소스 할당

| 구분 | VM 01 | VM 02 | VM 03 |
|---|---|---|---|
| Hostname | lake-storage | lake-compute | lake-catalog |
| 역할 | Storage | Compute | Catalog |
| vCPU | 2 | 4 | 2 |
| RAM | 3GB | 7GB | 4GB |
| Disk | 80GB | 50GB | 30GB |
| OS | Ubuntu ARM64 | Ubuntu ARM64 | Ubuntu ARM64 |
| 서비스 | RustFS | Spark | Polaris, PostgreSQL |

### 2.2. 전체 리소스

| 리소스 | 할당량 |
|---|---:|
| VM vCPU 합계 | 8 vCPU |
| VM RAM 합계 | 14GB |
| Mac Host 메모리 예산 | 10GB |
| 가상 디스크 합계 | 160GB |

위 자원은 소규모 PoC용 초기 할당값이며 실제 부하 테스트 결과에 따라 조정한다.

macOS에 남겨둔 10GB는 Tinyproxy, VMware, IDE 및 기타 Host 프로세스가 공유한다.

---

## 3. 네트워크 아키텍처

### 3.1. 기본 네트워크 정책

모든 VM은 VMware 내부 가상망에 배치한다.

내부망은 Host-Only 또는 동등한 격리 네트워크를 사용하며, VM에서 외부 인터넷으로 직접 라우팅되는 경로를 제공하지 않는다.

Mac은 내부 가상망에 접근 가능한 인터페이스를 가지며 Tinyproxy를 통해 외부 HTTPS 요청을 중계한다.

**외부 통신**

```text
VM
 │
 │ HTTPS_PROXY
 ▼
Mac Internal Interface
 │
 ▼
Tinyproxy :8888
 │
 │ HTTP CONNECT
 ▼
Internet HTTPS :443
```

**내부 서비스 통신**

```text
VM 02 (Spark)
 │
 ├────► VM 01 (RustFS)
 │       TCP 9000
 │
 └────► VM 03 (Polaris)
         TCP 8181

VM 03 (Polaris)
 │
 ├────► VM 01 (RustFS)
 │       TCP 9000
 │
 └────► PostgreSQL
         TCP 5432 (같은 VM 내부)
```

내부 통신은 직접 연결하며 Mac Tinyproxy를 경유하지 않는다.

### 3.2. IP 할당 예시

실제 VMware 내부망의 네트워크 대역에 따라 변경한다.

| 구분 | IP | 용도 |
|---|---|---|
| Mac Internal | 192.168.100.1 | Tinyproxy / SSH Client |
| VM 01 | 192.168.100.11 | Storage |
| VM 02 | 192.168.100.12 | Compute |
| VM 03 | 192.168.100.13 | Catalog |

- Subnet: `192.168.100.0/24`
- Tinyproxy Port: `8888` (예시)
- VM IP: 고정 할당
- VM 외부 인터넷 직접 접근: 차단
- VM 간 직접 접근: 허용

내부망이 여러 서브넷으로 분리되어 있다면 VM 간 라우팅을 별도로 구성한다.

### 3.3. 네트워크 접근 정책

| 출발지 | 목적지 | 포트 | 정책 |
|---|---|---:|---|
| Mac | VM 01~03 | 22 | Allow |
| VM 01~03 | Mac Tinyproxy | 8888 | Allow |
| VM 02 | VM 01 | 9000 | Allow |
| VM 03 | VM 01 | 9000 | Allow |
| VM 02 | VM 03 | 8181 | Allow |
| VM 03 Polaris | PostgreSQL | 5432 | Allow (로컬) |
| VM → Internet | Direct | Any | Deny |
| VM → Internet | Tinyproxy 경유 | HTTPS | Allow |

Spark의 Driver와 Executor가 별도 호스트에서 실행되는 구성으로 확장할 경우 Spark 통신 포트를 추가로 정의한다.

---

## 4. Mac Tinyproxy 구성

### 4.1. 역할

Mac에서 실행되는 Tinyproxy는 내부 VM의 외부 HTTPS 통신을 중계한다.

주요 사용 대상:

- Docker Image Pull
- APT Repository 접근
- Python Package 다운로드
- Java/Maven 의존성 다운로드
- Git HTTPS 접근
- 외부 API 요청

HTTP 프록시로 처리할 수 없는 프로토콜은 Tinyproxy로 자동 중계되지 않는다.

### 4.2. Tinyproxy 설정 기준

기존에 설치된 Tinyproxy를 유지하며 다음 조건을 확인한다.

```conf
Port 8888

Listen 192.168.100.1

Allow 127.0.0.1
Allow 192.168.100.11
Allow 192.168.100.12
Allow 192.168.100.13

ConnectPort 443
```

IP 및 포트는 실제 환경에 맞게 설정한다.

- 내부망 인터페이스에서 요청 수신
- VM 3대만 Proxy 접근 허용
- HTTPS CONNECT 443 허용
- 외부 네트워크에 Proxy 포트 공개 금지

이미 설정되어 있는 Tinyproxy의 포트 및 접근 정책이 정상 동작한다면 재구성할 필요는 없다.

### 4.3. VM Proxy 환경변수

각 VM은 외부 HTTPS 통신에 다음 설정을 사용한다.

```bash
export HTTPS_PROXY=http://192.168.100.1:8888
export https_proxy="$HTTPS_PROXY"
```

HTTP Repository에 접근해야 한다면 Tinyproxy의 일반 HTTP 프록시 기능도 사용할 수 있도록 `HTTP_PROXY`를 추가한다.

내부 서비스 주소는 Proxy 예외로 설정한다.

```bash
export NO_PROXY="localhost,127.0.0.1,::1,192.168.100.1,192.168.100.11,192.168.100.12,192.168.100.13,lake-storage,lake-compute,lake-catalog"

export no_proxy="$NO_PROXY"
```

환경변수는 사용하는 Shell 또는 서비스의 실행 환경에 적용해야 한다.

### 4.4. Docker Daemon Proxy

Docker Image Pull은 Docker Daemon에서 실행되므로 Shell의 Proxy 환경변수만으로는 충분하지 않을 수 있다.

각 VM에서 다음 설정을 사용한다.

경로:

```text
/etc/systemd/system/docker.service.d/http-proxy.conf
```

내용:

```ini
[Service]
Environment="HTTPS_PROXY=http://192.168.100.1:8888"
Environment="NO_PROXY=localhost,127.0.0.1,::1,192.168.100.1,192.168.100.11,192.168.100.12,192.168.100.13,lake-storage,lake-compute,lake-catalog"
```

적용:

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

적용 확인:

```bash
systemctl show \
  --property=Environment docker
```

HTTP Repository도 Docker Daemon에서 이용해야 하는 경우 `HTTP_PROXY`를 추가할 수 있다.

기존 Docker 설정이 있는 경우 다른 프록시 설정과 충돌하지 않는지 확인한다.

### 4.5. Docker Container Proxy

Docker Daemon의 Proxy 설정은 컨테이너 내부 프로세스에 자동 적용되는 설정과 구분된다.

Spark에서 외부 Maven Repository에 접근하거나 컨테이너 내부에서 패키지를 다운로드하는 경우 별도의 환경변수를 지정한다.

Docker Compose 예시:

```yaml
services:
  spark:
    environment:
      HTTPS_PROXY: http://192.168.100.1:8888
      NO_PROXY: localhost,127.0.0.1,192.168.100.11,192.168.100.12,192.168.100.13,lake-storage,lake-compute,lake-catalog
```

불필요한 서비스에 Proxy를 일괄 적용하지 않고 외부 접근이 필요한 서비스에만 적용한다.

Docker Build 과정에서도 의존성 다운로드가 필요하다면 Build Proxy 설정을 별도로 적용한다.

### 4.6. 네트워크 제약

HTTP Proxy가 존재한다고 해서 모든 네트워크 통신이 자동으로 외부에 연결되는 것은 아니다.

특히 다음 항목을 별도로 고려한다.

- DNS: Proxy를 지원하지 않는 조회 방식은 별도 DNS 접근이 필요할 수 있다.
- NTP: HTTP Proxy를 통과하지 않으므로 내부 시간 동기화 또는 VMware 시간 동기화를 고려한다.
- ICMP: `ping`은 Tinyproxy로 전달되지 않는다.
- 비HTTP 프로토콜: 별도의 전송 경로가 없으면 외부 통신이 불가능하다.

---

## 5. 서비스 아키텍처

### 5.1. VM 01 — Storage

**서비스:** RustFS

목적:

- S3 호환 Object Storage 제공
- Raw 데이터 보관
- Parquet 데이터 파일 보관
- Iceberg Metadata 파일 보관

초기 운영 방식:

- RustFS Standalone
- 단일 Docker Container
- 영속 볼륨 사용
- 내부망 API 제공
- 외부 직접 접근 차단

Endpoint:

```text
http://192.168.100.11:9000
```

Management Console:

```text
http://192.168.100.11:9001
```

스토리지 구조 예시:

```text
lakehouse/
├── raw/
├── staging/
└── warehouse/
    └── Iceberg Tables
```

RustFS의 S3 Endpoint는 Spark 및 Polaris가 내부망을 통해 직접 접근한다.

### 5.2. VM 02 — Compute

**서비스:** Apache Spark

구성:

| 항목 | 설정 |
|---|---|
| Spark Master | 1개 |
| Spark Worker | 1개 |
| Worker Cores | 3 |
| Worker Memory | 4GB |
| Driver Memory | 1GB |
| Executor Memory | 2GB |

주요 역할:

- Data Ingestion
- ETL / ELT
- Parquet 처리
- Iceberg Table 관리
- Spark SQL 분석
- PySpark 실행

통신 대상:

```text
RustFS:
http://192.168.100.11:9000

Polaris:
http://192.168.100.13:8181/api/catalog
```

외부 의존성 다운로드는 Mac Tinyproxy를 통해 처리한다.

내부 RustFS 및 Polaris와의 연결에는 Proxy를 사용하지 않는다.

Spark Job은 초기에는 Compute VM 내부에서 실행하며, Mac은 SSH 또는 Remote Development 방식으로 작업한다.

### 5.3. VM 03 — Catalog

**서비스:** Apache Polaris + PostgreSQL

주요 역할:

- Iceberg REST Catalog 제공
- Namespace 관리
- Table 등록 및 관리
- Storage 설정 관리
- Catalog 메타데이터 영속화
- 인증 및 권한 관리

Endpoint:

```text
http://192.168.100.13:8181/api/catalog
```

PostgreSQL은 Polaris와 동일 VM에 배치한다.

PostgreSQL은 VM 내부에서만 접근하도록 구성하며 외부 포트를 공개하지 않는다.

Polaris는 PostgreSQL 기반 영속화 설정을 사용한다.

Iceberg 데이터 파일과 메타데이터 파일은 RustFS에 저장되며, Polaris는 테이블을 참조하고 관리하는 Catalog 역할을 수행한다.

---

## 6. 데이터 처리 흐름

```text
Data Sources
    │
    ▼
VM 02 — Apache Spark
    │
    ├────► VM 03 — Apache Polaris
    │           │
    │           ▼
    │       PostgreSQL
    │       (Catalog State)
    │
    ▼
VM 01 — RustFS
    │
    └── lakehouse/
        ├── raw/
        ├── staging/
        └── warehouse/
            ├── Data Files
            └── Iceberg Metadata
```

Spark는 Polaris를 통해 Iceberg Table을 관리하며, RustFS에 직접 데이터 파일을 읽고 쓴다.

외부 데이터 수집 시 HTTPS 요청은 Tinyproxy를 경유한다.

---

## 7. 배포 구조

각 VM은 독립적인 Docker Compose 프로젝트를 운영한다.

### VM 01

```text
/opt/lakehouse/storage/
├── compose.yaml
├── .env
└── data/
```

### VM 02

```text
/opt/lakehouse/compute/
├── compose.yaml
├── .env
├── config/
└── jobs/
```

### VM 03

```text
/opt/lakehouse/catalog/
├── compose.yaml
├── .env
└── data/
```

VM별로 Docker Compose를 실행하며, VM 간 통신은 내부망 IP를 사용한다.

인증 정보는 Git에 포함하지 않고 별도로 관리한다.

---

## 8. 구축 순서

### Phase 1 — VM 및 네트워크

1. Ubuntu ARM64 VM 3대 구성
2. 내부 가상망 연결
3. VM별 고정 IP 할당
4. Mac → VM SSH 접근 확인
5. VM 간 직접 통신 확인
6. VM의 외부 직접 접근 차단 확인

### Phase 2 — Proxy

1. Mac Tinyproxy 설정 확인
2. VM 3대의 Proxy 접근 허용
3. VM에서 HTTPS Proxy 통신 검증
4. 내부망 주소의 NO_PROXY 적용
5. APT 및 Docker Daemon Proxy 설정
6. Docker Image Pull 검증

### Phase 3 — Storage

1. VM 01 Docker Compose 구성
2. RustFS 배포
3. 데이터 볼륨 영속화
4. S3 Bucket 생성
5. 내부망 S3 API 접근 검증

### Phase 4 — Catalog

1. VM 03 PostgreSQL 배포
2. Polaris 배포
3. PostgreSQL Persistence 구성
4. RustFS Storage 연결
5. Iceberg Catalog 초기화
6. REST API 접근 검증

### Phase 5 — Compute

1. VM 02 Spark 배포
2. Spark Master/Worker 연결
3. Iceberg Runtime 설정
4. Polaris REST Catalog 연결
5. RustFS S3 Endpoint 설정
6. PySpark 테스트

### Phase 6 — 기능 검증

1. Namespace 생성
2. Iceberg Table 생성
3. Parquet 데이터 적재
4. Spark SQL 조회
5. UPDATE / DELETE 테스트
6. Snapshot 및 Time Travel 테스트
7. Schema Evolution 테스트
8. 서비스 재시작 및 데이터 영속성 검증

---

## 9. 네트워크 검증

### 9.1. Proxy HTTPS 접근

각 VM에서 실행:

```bash
curl -I \
  --proxy http://192.168.100.1:8888 \
  https://example.com
```

HTTP 응답이 정상적으로 반환되는지 확인한다.

### 9.2. Docker Image Pull

```bash
docker pull alpine:latest
```

Docker Daemon이 Proxy를 통해 외부 Registry에 접근할 수 있는지 확인한다.

### 9.3. VM 간 통신

VM 02에서 실행:

```bash
nc -zv 192.168.100.11 9000

nc -zv 192.168.100.13 8181
```

내부 서비스와 직접 연결되는지 확인한다.

### 9.4. Proxy 우회 검증

```bash
curl --noproxy '*' \
  http://192.168.100.11:9000/
```

S3 API의 인증 오류 응답이라도 서버가 정상적으로 응답한다면 네트워크 연결 자체는 성공한 것으로 볼 수 있다.

### 9.5. 검증 체크리스트

- [ ] Mac → VM SSH 접근 성공
- [ ] VM 간 Private IP 통신 성공
- [ ] VM → Internet 직접 접근 차단
- [ ] VM → Tinyproxy 접근 성공
- [ ] Tinyproxy HTTPS CONNECT 정상 동작
- [ ] Docker Image Pull 성공
- [ ] Spark → RustFS 직접 연결
- [ ] Spark → Polaris 직접 연결
- [ ] Polaris → RustFS 직접 연결
- [ ] PostgreSQL Persistence 정상 동작
- [ ] Iceberg Table 생성 및 조회 성공
- [ ] VM 및 서비스 재시작 후 데이터 유지

---

## 10. AWS S3 전환 전략

초기에는 RustFS를 사용하지만 스토리지 연결 설정을 외부화하여 AWS S3 전환을 고려한다.

| 항목 | 초기 환경 | 확장 환경 |
|---|---|---|
| Storage | RustFS | AWS S3 |
| Format | Parquet | Parquet |
| Table Format | Iceberg | Iceberg |
| Catalog | Polaris | Polaris 유지 가능 |
| Compute | Spark | Spark 유지 가능 |
| Catalog DB | PostgreSQL | PostgreSQL 유지 또는 이전 |

AWS S3 전환 시 다음 사항을 검토한다.

1. RustFS 데이터 파일 및 Iceberg 메타데이터 파일 복사
2. Iceberg 파일 경로 변경에 따른 메타데이터 처리
3. Polaris Storage 및 인증 설정 변경
4. Spark S3 연결 설정 변경
5. AWS S3 API 접근을 위한 Proxy 설정
6. 데이터 정합성 확인
7. 테이블 읽기/쓰기 테스트

로컬 VM이 계속 내부망에 위치하는 경우 AWS S3 API 접근도 Tinyproxy를 경유할 수 있다.

단, Storage Endpoint 변경만으로 전체 데이터 이전이 자동 완료되는 것은 아니다.

---

## 11. 제약 및 향후 확장

### 11.1. 현재 제약

- 물리 Host 1대에 모든 VM 종속
- RustFS 단일 노드
- Spark 단일 VM
- Polaris 단일 인스턴스
- PostgreSQL 단일 인스턴스
- 고가용성(HA) 미지원
- 외부 HTTPS 통신이 Mac Tinyproxy에 종속
- Mac 전원 종료 또는 네트워크 장애 시 외부 데이터 접근 불가

### 11.2. 확장 방향

- RustFS 분산 클러스터 구축
- Spark Worker 추가
- Polaris/PostgreSQL 분리
- Prometheus/Grafana 모니터링 도입
- Ansible 기반 VM 배포 자동화
- Proxy 설정 자동화
- Object Storage 데이터 백업
- AWS S3 이전 검증

---

## 12. 최종 구성 요약

**Host**

- Apple Silicon Mac
- RAM 24GB
- VMware Fusion
- Tinyproxy HTTPS Outbound Proxy

**VM**

- VM 01: RustFS — 2 vCPU / 3GB
- VM 02: Spark — 4 vCPU / 7GB
- VM 03: Polaris + PostgreSQL — 2 vCPU / 4GB

**Network**

- VM 3대 모두 내부망
- 외부 직접 통신 차단
- 외부 HTTPS → Mac Tinyproxy
- 내부 서비스 → Private IP 직접 연결
- Mac → VM SSH 관리

**목표**

로컬 환경에서 Iceberg 기반 레이크하우스를 구축하고, Storage와 Compute를 분리한 상태로 데이터 파이프라인 운영을 검증한다.

향후 필요에 따라 RustFS 분산화, Spark 확장 및 AWS S3 전환을 진행한다.
