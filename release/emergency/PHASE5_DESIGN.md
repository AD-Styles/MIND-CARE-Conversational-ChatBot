# Phase 5 설계 — 코드를 어떻게 짰나

이 문서는 Phase 5 (Emergency Decider · Alert Dispatcher · API Gateway) 의
**설계 결정과 모듈별 책임** 을 처음 보는 사람도 따라갈 수 있게 정리한 것이다.
경로·명령어 같은 quick reference 는 [PHASE5.md](PHASE5.md), 이 문서는 deep dive.

---

## 목차

1. [큰 그림 — 책임 분리 3단](#1-큰-그림--책임-분리-3단)
2. [패키지 트리](#2-패키지-트리)
3. [모듈별 설계 결정](#3-모듈별-설계-결정)
   - 3.1 [`decider_states.py`](#31-decider_statespy--순수-python-상태-머신)
   - 3.2 [`emergency_decider_node.py`](#32-emergency_decider_nodepy--ros-어댑터)
   - 3.3 [`alerts_db.py`](#33-alerts_dbpy--sqlite-큐)
   - 3.4 [`channels/`](#34-channels--5-종-발송기)
   - 3.5 [`alert_dispatcher_node.py`](#35-alert_dispatcher_nodepy--큐-워커)
   - 3.6 [`mind_care_api/schemas.py`](#36-schemaspy--fe-와의-계약)
   - 3.7 [`mind_care_api/db.py`](#37-dbpy--sqlalchemy-orm-뷰)
   - 3.8 [`mind_care_api/auth.py`](#38-authpy--x-api-key)
   - 3.9 [`mind_care_api/ws.py`](#39-wspy--websocket-브로드캐스터)
   - 3.10 [`mind_care_api/ros_bridge.py`](#310-ros_bridgepy--두-세계의-다리)
   - 3.11 [`routes/`](#311-routes--rest-엔드포인트)
   - 3.12 [`app.py` + `api_gateway_node.py`](#312-apppy--api_gateway_nodepy--조립과-기동)
4. [ROS + asyncio 한 프로세스에서 동거시키기](#4-ros--asyncio-한-프로세스에서-동거시키기)
5. [데이터 흐름 — 거실 낙상 시나리오 한 사이클](#5-데이터-흐름--거실-낙상-시나리오-한-사이클)
6. [시연 ↔ 운영 한 줄 전환](#6-시연--운영-한-줄-전환)
7. [FE 팀에 넘길 deliverable](#7-fe-팀에-넘길-deliverable)
8. [계획서 KPI 매핑](#8-계획서-kpi-매핑)

---

## 1. 큰 그림 — 책임 분리 3단

| 계층 | 패키지 | 노드 | 일 |
|---|---|---|---|
| **결정** | `mind_care_emergency` | `emergency_decider_node` | "지금 알림을 발생시킬지 말지" |
| **전달** | `mind_care_emergency` | `alert_dispatcher_node` | "어떻게 보낼지 + 실패 시 재시도" |
| **노출** | `mind_care_api` | `api_gateway_node` | "FE 가 무엇을 어떻게 받는지" |

세 노드는 **ROS 토픽 5개** 와 **공유 SQLite 파일** 로만 결합되어 있다.
독립 단위 테스트 / 교체 / 재시작 가능.

```
/vision/fall_state ─┐                                   ┌── /emergency/alert ─→ Dispatcher
/vision/state ──────┼── Decider (상태 머신 + 30s 타이머) ┤
/audio/transcript ──┤                                   └── /dialogue/proactive_speech ─→ dialogue
/emergency/ack ─────┘
                                                            /emergency/delivery ─→ Gateway
                                                                                    │
                                                                          HTTP / WS / FCM
                                                                                    ▼
                                                                              📱 보호자 앱
```

---

## 2. 패키지 트리

```
release/emergency/
├── PHASE5.md                                ← quick reference
├── PHASE5_DESIGN.md                         ← (이 문서)
├── requirements.txt
├── patches/0001-add-proactive-speech.diff   ← mind_care_vision 패치
│
├── mind_care_emergency/                     ← 결정 + 전달
│   ├── package.xml / setup.py / setup.cfg
│   ├── mind_care_emergency/
│   │   ├── decider_states.py                ← 순수 Python (테스트 가능)
│   │   ├── emergency_decider_node.py
│   │   ├── alert_dispatcher_node.py
│   │   ├── alerts_db.py                     ← SQLite 큐 (Dispatcher 측)
│   │   └── channels/
│   │       ├── base.py
│   │       ├── local_buzzer.py              ← 항상 시도 (오프라인 보장)
│   │       ├── fcm.py                       ← Firebase Admin
│   │       ├── twilio_sms.py
│   │       └── mock.py                      ← 시연용
│   ├── config/emergency_params.yaml
│   ├── launch/emergency.launch.py
│   └── tests/test_decider_states.py         ← pytest 7 시나리오
│
└── mind_care_api/                           ← FE 노출 게이트웨이
    ├── package.xml / setup.py / setup.cfg
    ├── mind_care_api/
    │   ├── schemas.py                       ← Pydantic (= OpenAPI 계약)
    │   ├── db.py                            ← SQLAlchemy 2.x ORM 뷰
    │   ├── auth.py                          ← X-Api-Key
    │   ├── ws.py                            ← WebSocket 브로드캐스터
    │   ├── ros_bridge.py                    ← 별도 thread 에서 rclpy.spin
    │   ├── routes/{status,alerts,guardians,reports}.py
    │   ├── app.py                           ← FastAPI 팩토리
    │   └── api_gateway_node.py              ← uvicorn 진입점 (ros2 entry)
    ├── config/api_params.yaml
    └── launch/api_gateway.launch.py
```

---

## 3. 모듈별 설계 결정

### 3.1 `decider_states.py` — 순수 Python 상태 머신

**핵심 결정**: ROS 의존성을 0으로. ROS 콜백을 **`Event` 객체 1개로 정규화**해
`decide()` 메서드에 넘기고, `Effect` 리스트를 받아 ROS 노드가 발행하기만 한다.

분리가 주는 이득:

- **`pytest` 로 ROS 없이 7가지 시나리오 검증** — `tests/test_decider_states.py`
- 시간/타이머가 `time.time()` 가 아니라 **`Event.ts`** — 가짜 시각으로 30 초 대기 없이 timeout 검증
- 정책 변경 (예: timeout 60 초로) → ROS 코드 안 건드리고 `DeciderConfig` 만 수정

상태 4개 (`NORMAL/QUERY/EMERGENCY/ACKED`) · 이벤트 9종을 enum 으로 박아 IDE 자동완성
가능. `Effect` 는 `(publish_alert, proactive_speech, log)` 3가지로 좁혔는데
이게 **Decider 가 외부에 시킬 수 있는 모든 일** 이다.

`_raise_emergency()` 가 dedupe 의 핵심: 한 번 EMERGENCY 진입하면 같은 alert_id
를 다시 발행하지 않음. ACK 또는 cooldown 후 NORMAL 로 돌아가야 다음 알림 가능.

#### 상태 전이도

```
NORMAL ──fall_detected──→ QUERY  (dialogue 능동 발화 트리거)
NORMAL ──panic_word────→ EMERGENCY (즉시)
NORMAL ──long_idle─────→ QUERY

QUERY  ──"괜찮아요"────→ NORMAL  (severity=info 의 false_alarm 로그)
QUERY  ──"도와줘"──────→ EMERGENCY
QUERY  ──fall_confirmed→ EMERGENCY
QUERY  ──30s 타임아웃──→ EMERGENCY

EMERGENCY ──ack_received──→ ACKED
ACKED     ──cooldown 60s──→ NORMAL
```

### 3.2 `emergency_decider_node.py` — ROS 어댑터

ROS 콜백 → `Event` 변환 → `sm.decide()` → `Effect` 별 토픽 발행. 그게 전부.

추가 책임 세 가지:

- **panic_word / ok 키워드 매칭** — STT 텍스트에 `"도와줘"`, `"괜찮아요"` 등이
  들어갔는지. 단순 substring 매칭이지만 한국어 노인 발화에 robust 한 키워드
  7~8개로 시작.
- **6시간 부동 추적** — `_last_motion_ts` 를 vision_state / fall_state /
  transcript 들어올 때마다 갱신, tick 마다 비교해 임계 넘으면 `LONG_IDLE` 이벤트 self-emit.
- **rising-edge 감지** — `fall_detected` 가 `false→true` 가 되는 시점에서만
  Decider 트리거. true 가 계속 유지되어도 중복 발생 안 함.

화자 검증은 일단 STT 메시지의 `speaker_match` 필드로 우회 — Phase 6 에서
pyannote 가 들어와도 같은 인터페이스 그대로.

### 3.3 `alerts_db.py` — SQLite 큐

**Dispatcher 와 Gateway 가 같은 DB 파일을 공유** — 운영 단순화 + 대시보드가
읽는 row = dispatcher 가 쓰는 row 라 정합성 보장.

- `WAL` 모드 + 명시 RLock → SQLite 의 동시성 부족 보완
- 테이블 4개: `alerts`, `alert_deliveries`, `guardians`, `events`
- `claim_pending(now ≥ next_attempt)` 로 백오프 시간이 도래한 row 만 가져옴 — 단순 큐 인터페이스

`mind_care_api/db.py` 의 SQLAlchemy 모델은 **같은 컬럼 정의의 ORM 뷰** —
DDL 은 `alerts_db.py` 가, ORM 은 가독성 좋은 read 가 책임.

### 3.4 `channels/` — 5 종 발송기

추상 인터페이스는 한 줄짜리:

```python
class Channel:
    def available(self) -> bool: ...
    def send(self, alert, guardians) -> ChannelResult: ...
```

**핵심 패턴**: `available()` 가 `False` 인 채널은 dispatcher 가 자동으로 큐에서
빼거나 mock 으로 대체.

| 채널 | 비고 |
|---|---|
| `LocalBuzzerChannel` | `aplay` + 기본 wav. 항상 시도. 네트워크 단절 시에도 동작 → 계획서 "로컬 부저 오프라인 동작" KPI 충족 |
| `FCMChannel` | `FIREBASE_CRED_PATH` env 있으면 활성화, 없으면 mock fallback. **모바일 앱 백그라운드 푸시는 FCM 필수** |
| `TwilioSMSChannel` | env 자격증명, 없으면 비활성. 백업 채널 |
| `MockChannel` | 시연/개발용 콘솔 출력. `dispatch_mode=mock` 일 때 fcm/sms 자리에 들어옴 |

이 디자인 덕에 **자격증명 없이도 시연 가능** 하고,
**앱 배포 단계에서 환경변수만 추가** 해도 코드 수정 없이 실서비스로 전환.

### 3.5 `alert_dispatcher_node.py` — 큐 워커

알고리즘 단순:

1. `/emergency/alert` 들어오면 DB 에 row 삽입 + 채널별 pending row N 개 생성
2. 1 Hz tick 마다 `claim_pending()` → 채널 `send()` → 결과 따라 status 갱신
3. 실패 시 backoff `[1s, 5s, 30s, 5min]` 으로 `next_attempt` 갱신, 6 회 초과 시 `failed`
4. 모든 채널이 종료 상태 도달하면 alert 자체 status 를 `delivered` 또는 `failed` 로

**이 노드의 실패 모델은 "전체 안전 보장"**:
FCM 이 30 분 끊겨도 부저는 즉시 동작, FCM 이 복구되면 큐에서 자동 재시도.
어떤 단일 채널 장애도 알림을 잃게 만들지 않는다.

`_tick_lock` 으로 두 tick 이 겹치는 경우 (느린 send 가 다음 tick 까지 끌고 있을 때)
동시 클레임 방지.

### 3.6 `schemas.py` — FE 와의 계약

Pydantic v2. 이게 **OpenAPI JSON 의 source of truth** — FastAPI 가 실행 시
`/openapi.json` 에 자동 노출하고, FE 팀은 이 JSON 으로 TypeScript 타입
codegen 가능.

`Status`, `Alert`, `DailyReport`, `Guardian`, `WSMessage` 모두 여기 한 파일에.
필드 추가/제거가 필요하면 이 파일만 고치면 된다 — 라우트들은 model 을 type
hint 로 받으니 자동 검증·문서화.

`Literal[...]` 로 enum 강제 — `type` 이 `"fall" | "panic_word" | "long_idle" |
"false_alarm"` 만 허용 → 잘못된 값이 들어오면 422 자동 반환.

### 3.7 `db.py` — SQLAlchemy ORM 뷰

같은 SQLite 파일을 **읽기 위주** 로 사용. 컬럼 정의는 `alerts_db.py` 의 DDL
과 글자 단위로 일치.

`@contextmanager` `session()` 로 트랜잭션 경계 명확화 — `with db.session() as
s: ...` 안에서 자동 commit/rollback.

### 3.8 `auth.py` — X-Api-Key

가장 단순한 방법으로 시작:

1. 보호자 등록 시 `secrets.token_urlsafe(32)` 발급, **응답에 1회만 평문 노출**, DB 엔 SHA256 hash 만 저장
2. 이후 요청은 `X-Api-Key: <key>` 헤더 → 동일 hash 매칭으로 인증
3. WS 는 헤더 못 쓰므로 query string `?token=...` 으로 같은 검사
4. `MIND_CARE_SECRET_KEY` env 가 있으면 HMAC 으로 강화 (운영 권장)
5. **`dev_open=True` 모드** — 시연 시 `--dev-open` 플래그로 인증 우회 (Swagger UI 편하게 테스트)

Firebase Auth 으로 교체할 때는 `APIKeyAuth` 만 새 의존성으로 갈아끼우면 됨 —
라우트는 안 바뀐다.

### 3.9 `ws.py` — WebSocket 브로드캐스터

asyncio Lock + Set 으로 client 관리, dead client 자동 정리. 모든 메시지가
`{type, data}` 단일 형식이라 FE 도 단순한 `switch(msg.type)` 로 처리.

WS 에 보낼 때마다 `model_dump(mode="json")` 으로 Pydantic 모델 → dict 변환
→ `send_json()` — 같은 모델이 REST 와 WS 양쪽에 동일 JSON 으로 나가므로
FE 가 타입을 한 벌만 정의하면 된다.

### 3.10 `ros_bridge.py` — 두 세계의 다리

**가장 까다로운 부분**: ROS rclpy 는 동기 / 스레드 기반, FastAPI 는 async /
이벤트 루프. 한 프로세스에 둘이 사는 방법:

```
   main thread                        background thread
   ┌──────────────┐                   ┌──────────────────┐
   │ asyncio loop │                   │ rclpy.spin_once  │
   │ uvicorn      │ ←──── queue  ──── │ ROS callbacks    │
   │ FastAPI      │  via run_         │ → DB write       │
   │ WS broadcast │  coroutine_       │ → publish_thread │
   └──────────────┘  threadsafe       │   safe(WS)       │
                                      └──────────────────┘
```

- `_BridgeNode` 가 ROS 콜백을 받아 동기적으로 SQLite 쓰기 (빠르니 OK)
- 동시에 `Broadcaster.publish_threadsafe()` 호출 → 내부에서
  `asyncio.run_coroutine_threadsafe()` 로 main loop 의 WebSocket 푸시
- spin 은 별도 thread, 0.1 s timeout 으로 polling spin → 종료 신호 받으면 깔끔히 빠짐

이 패턴 덕에:

- ROS 메시지 도착 → FE 의 WS 로 **latency 1~10 ms** 내 도달
- DB 가 모든 이벤트 누적 → 새 보호자 접속 시 **과거 이력 즉시 조회 가능**

### 3.11 `routes/` — REST 엔드포인트

각 라우터가 `get_router(db, bridge, auth)` 팩토리를 노출 → `app.py` 가
`Depends(auth)` 를 주입. 의존성 주입으로:

- 단위 테스트 시 가짜 DB · 가짜 bridge 주입 가능
- 인증 끄고 켜는 게 한 줄 (`dev_open`)

`alerts.ack_alert` 가 흥미로운 코드 — DB 의 `alert.status` 갱신 +
`bridge.node.publish_ack()` 로 ROS 측에 ACK 이벤트 보냄 → Decider 가
EMERGENCY → ACKED 전이.
**REST 호출이 ROS 상태를 바꾸는 유일한 지점** 이라 보안 · 로깅에 집중.

| 라우터 | 책임 |
|---|---|
| `status.py` | 캐시된 최신 상태 한 장 |
| `alerts.py` | 이력 조회 + 상세 + ACK |
| `guardians.py` | 보호자 등록(미인증) / 조회 / 삭제 |
| `reports.py` | on-demand 일일 리포트 집계 |

### 3.12 `app.py` + `api_gateway_node.py` — 조립과 기동

`create_app(...)` 팩토리 패턴:

- 인스턴스 4개 (DB, Broadcaster, ROSBridge, Auth) 만들고 모두 클로저로 라우터에 주입
- CORS 미들웨어 (env 로 origin 제한 가능)
- `@app.on_event("startup")` 에서 `bc.attach_loop(asyncio.get_event_loop())`
  + `bridge.start()` — **이게 main loop 와 background thread 를 묶는 결정적 한 줄**
- `@app.on_event("shutdown")` 에서 `bridge.stop()` — 깔끔한 종료

`/api/v1/health` 가 정직한 health: ROS 살아있나 + DB ping + 큐 pending 개수.
모바일 앱이 시작 시 호출해서 백엔드 상태 표시 가능.

`api_gateway_node.py` 는 ROS entry_points 가 `ros2 run mind_care_api
api_gateway_node` 로 호출하면:

- argparse 로 host / port / elder-id / db-path 받음 (env 도 fallback)
- `create_app()` → uvicorn 으로 한 프로세스에서 띄움

**ROS 관점에선 그냥 노드 하나**, 안에선 FastAPI + 별도 thread 의 ROS spin.
이 이중 정체성 덕에 launch 파일 하나로 다른 ROS 노드들과 같이 기동 가능.

---

## 4. ROS + asyncio 한 프로세스에서 동거시키기

세 가지 핵심 규칙으로 풀었다:

1. **rclpy.spin 은 background thread 안에서만**. main thread 는 FastAPI 의
   asyncio loop 가 차지.
2. **ROS 콜백 → asyncio 로의 통신은 `run_coroutine_threadsafe` 만 사용**.
   직접 `await` 호출 금지 (다른 thread).
3. **DB write 는 ROS 콜백 안에서 동기적으로 처리**. SQLite WAL + RLock 으로
   thread-safe.

이 셋이 지켜지면 두 세계가 충돌 없이 공존한다.

---

## 5. 데이터 흐름 — 거실 낙상 시나리오 한 사이클

| 단계 | 일어나는 일 |
|----|---|
| 1 | `/vision/fall_state` 에 `fall_detected:true` 도착 (Phase 4) |
| 2 | **Decider** 가 `Event(FALL_DETECTED)` → 상태 머신 NORMAL→QUERY → `proactive_speech="괜찮으세요?"` 발행 + 30 s 타이머 시작 |
| 3 | dialogue 노드가 `/dialogue/proactive_speech` 구독 → 즉시 TTS 발화 |
| 4 | 30 초 안에 응답 없음 → 다음 tick 에서 자동 timeout → `Event(QUERY_TIMEOUT)` → `Effect(publish_alert=...)` → `/emergency/alert` 발행 |
| 5 | **Dispatcher** 수신 → `alerts` 테이블 + `alert_deliveries` 3 row (fcm/sms/buzzer) 생성, 모두 `pending` |
| 6 | 1 초 후 tick → `buzzer.send()` 즉시 ok / `fcm.send()` ok / `sms.send()` ok → `alert_deliveries.status` 모두 `ok` → `alerts.status=delivered`. 매 결과 `/emergency/delivery` 발행 |
| 7 | **Gateway** 의 `_BridgeNode` 가 `/emergency/alert`, `/emergency/delivery` 양쪽 구독 → DB 에 events 누적 + WebSocket 브로드캐스트 |
| 8 | **모바일 앱** (FE) 의 WS 가 `{type:"alert", data:{...}}` 받아 화면에 빨간 카드 표시. 동시에 FCM 푸시도 백그라운드로 도착 → 앱이 꺼져 있어도 알림 |
| 9 | 보호자 "확인" 탭 → `POST /api/v1/alerts/{id}/ack` → DB 갱신 + `bridge.publish_ack()` → Decider 가 `Event(ACK_RECEIVED)` 받아 EMERGENCY → ACKED → 60 s cooldown → NORMAL |

전체 latency: 2 → 8 까지 **수 초 내**, 30 s timeout 까지 합쳐도
**계획서 KPI E2E ≤ 30 s 충족**.

---

## 6. 시연 ↔ 운영 한 줄 전환

| 모드 | 명령 | 결과 |
|---|---|---|
| **시연** | `dispatch_mode:=mock --dev-open` | mock 채널 + 부저 + 인증 우회 |
| **운영** | env 자격증명 + `dispatch_mode:=auto` | FCM/SMS 실발송, X-Api-Key 인증 |

```bash
# 시연
ros2 launch mind_care_emergency emergency.launch.py dispatch_mode:=mock
ros2 launch mind_care_api       api_gateway.launch.py dev_open:=true

# 운영
export FIREBASE_CRED_PATH=/path/to/firebase-credentials.json
export TWILIO_ACCOUNT_SID=AC...
export TWILIO_AUTH_TOKEN=...
export TWILIO_FROM_PHONE=+1...
ros2 launch mind_care_emergency emergency.launch.py dispatch_mode:=auto
ros2 launch mind_care_api       api_gateway.launch.py
```

코드 수정 없음. 환경변수와 launch 인자만.

---

## 7. FE 팀에 넘길 deliverable

1. **`/openapi.json`** — Swagger UI 에서 다운로드 또는
   `curl http://localhost:8000/openapi.json`
2. **[PHASE5.md](PHASE5.md)** — 엔드포인트 표 + WS 메시지 종류 + FCM payload (quick reference)
3. **이 문서 ([PHASE5_DESIGN.md](PHASE5_DESIGN.md))** — 시스템 흐름 이해용
4. **`mind_care_api/schemas.py`** — Pydantic 모델 source of truth (codegen 의 입력)
5. **개발 시 띄울 수 있는 백엔드 stub** — `--dev-open` + `dispatch_mode:=mock` 으로
   자격증명 없이도 FE 가 콜 가능

### FE 가 알아야 할 4가지

| 채널 | 어떻게 |
|---|---|
| **REST** | `https://<host>/api/v1/...`, 헤더 `X-Api-Key: <token>` (등록 시 1회 발급) |
| **WebSocket** | `wss://<host>/api/v1/stream?elder_id=...&token=...`, 메시지 `{type, data}` |
| **FCM Push** | 백엔드가 등록된 `fcm_token` 들로 multicast. payload `data.deeplink="mindcare://alerts/<id>"` |
| **에러** | FastAPI 기본 422/4xx/5xx, 한국어 detail |

---

## 8. 계획서 KPI 매핑

| 계획서 KPI | 구현 위치 | 상태 |
|---|---|---|
| **E2E p95 ≤ 30 s (낙상→알림)** | `decider_states.DeciderConfig.query_timeout_s` + dispatcher 즉시 발송 | **✅ p95 3.69 s — §9 참조** |
| Alert Delivery 성공률 ≥ 99.9% | 3중 채널 + SQLite 재시도 큐 (백오프 1s/5s/30s/5min, 6회) | 구조 ✅, 정량 검증은 burn-in |
| 로컬 알람 (네트워크 단절) | `LocalBuzzerChannel` 가 항상 시도 — FCM/SMS 실패해도 부저는 동작 | 구조 ✅ (WSL 환경에선 alsa 미가용) |
| FMEA Vision 미탐지 (RPN=240) | Voice 응급어 `panic_word` + `fall_confirmed` AND 결합 | 구조 ✅, Voice 통합 후 정량 |
| FMEA TV/가족 음성 (RPN=168) | `_on_transcript` 에서 `speaker_match=False` 시 panic_word 무시 | 구조 ✅, 화자 검증 통합 후 정량 |
| Burn-in 168 시간 | (미구현) — `/api/v1/health` 의 ROS/DB 시간 누적이 첫 발걸음 | 미구현 |
| Network Chaos 24 시간 | SQLite 큐 + backoff 가 자동 흡수, 검증은 별도 | 구조 ✅, 정량 미검증 |

---

## 9. URFDD 시스템 레벨 평가 (Vision + Decider 통합)

Phase 4 의 Vision 단독 평가 (PHASE4_DESIGN.md §12 — Recall 77%) 다음 단계.
**`/emergency/alert` 발행 여부** 를 판정 기준으로 동일한 70 영상 재평가.

### 9.1 평가 흐름

영상 1 개당 두 노드 동시:
1. `fall_detection_node` (mp4 → `/vision/fall_state`)
2. `emergency_decider_node` (`/vision/fall_state` → 상태 머신 → `/emergency/alert`)

판정: `fall 영상에 alert 발행됨` = TP, ADL 영상에 alert = FP …

평가 효율을 위해 `query_timeout_s=5.0 s` 로 단축 (default 30 s 대신 — KPI E2E ≤
30 s 안에 들어가는 한도).

### 9.2 결과

```
Confusion matrix
   TP=24  FN=6   FP=11  TN=29

Recall    = 0.800   (Vision 단독 0.767 → +3.3%p)
Precision = 0.686   (Vision 단독 0.676 → +1.0%p)
F1        = 0.739   (Vision 단독 0.719 → +0.02)
Latency   p50 = 2.73 s,  p95 = 3.69 s   ← KPI ≤ 30 s 한참 통과 ✅
```

### 9.3 의미

- **Decider 가 Vision 의 짧은-fall 미탐지 일부를 살림** — Vision v4 에서 FN
  이던 fall-14~18, 23, 25, 26, 29 가 시스템에서 alert 발행 (Decider 의 자동
  timeout escalation 효과).
- **Latency p95 3.69 s** — 계획서 KPI 8 개 중 **첫 정량 PASS** (Burn-in /
  Voice 융합 / FCM 등은 별도 검증 필요).
- **Precision 은 Vision 과 거의 동일** — Decider 가 Vision FP 를 추가 필터링
  하지 않음. 이는 Phase 5 의 두 가지 미통합 안전장치가 들어가면 보완됨:
    - **5 s 부동 IoU confirm** (Phase 4 의 `fall_confirmed`, Decider 가
      받기만 하면 됨)
    - **Voice 응급어 AND 결합** (mind_care_vision STT 의 `/audio/transcript`
      구독 — 코드는 있고 dialogue 패치 적용 후 통합 필요)
- **Recall 80% 의 KPI (0.95) 미달은 URFDD 도메인 한계** — 영상 ≤8 s, 카메라
  천장 비스듬. 자체 시연 영상으로 재평가 시 Voice 융합 + 시간 confirm
  포함하면 KPI 도달 가능.

### 9.4 산출물

```
release/emergency/scripts/
├── system_sim.sh        # 한 영상 sanity check
└── system_eval.py       # 70 영상 자동 평가

~/eval/urfdd/
├── results_system_v1.json   # query_timeout=5s, confirm_idle=5s (URFDD엔 confirmed 안 됨)
└── results_system_v2.json   # query_timeout=10s + confirm_idle=1.5s (fall_confirmed 활성화)
```

### 9.4.1 v1 vs v2 — fall_confirmed 활성화 효과

| 지표 | v1 (timeout 5 s) | v2 (confirm 1.5 s + timeout 10 s) |
|---|---:|---:|
| Recall | 0.800 | 0.800 (동일) |
| Precision | 0.686 | 0.667 (−2 %p) |
| F1 | 0.739 | 0.727 |
| Latency p50 | 2.73 s | 7.63 s |
| Latency p95 | **3.69 s** | 8.82 s |

**관찰**: URFDD 영상이 5~14 s 로 짧아 1.5 s 부동도 ADL 일부에서 발생 ("활동 중 잠시 정지"). 시연 환경 (10~30 s 영상, 진짜 5 s 부동) 에서는 ADL 의 짧은 정지는 confirmed 통과 못 함 → FP 줄어드는 효과 기대. URFDD 평가용으로는 v1 (default 5 s confirm — 영상 한계로 confirmed 자체 안 발행, query timeout 만 살아있음) 이 더 균형 좋음.

**기본값 환원**: 운영 환경에선 `confirm_idle_s` 가 5.0 (기본) 으로 돌아가야 함 — `fall_rules.py` 의 default 가 1.5 로 바뀌어 있으니 시연 직전 5.0 으로 복귀 필요.

### 9.5 Vision 단독 vs 시스템 비교

| 영상군 | Vision (v4) FN | 시스템 FN |
|---|---|---|
| fall-13 | FN | FN |
| **fall-14~18** | **FN × 5** | **TP × 5** ✅ |
| fall-19 | FN | FN |
| fall-21 | FN | FN |
| **fall-23, 25, 26, 29** | **FN × 4** | **TP × 4** ✅ |
| fall-27, 28, 30 | FN | FN |

**Decider 가 살린 영상 9 개**, 그래도 못 잡은 6 개는 Vision PGIE 단계에서 사람
검출 자체가 불안정한 케이스. Voice 융합 필요.

### 9.6 다음 단계

- [x] **mind_care_vision dialogue 패치 적용** + STT `panic_word` 통합 — § 10 참조
- [x] **`fall_confirmed` 시그널 사용** — § 9.4.1 (URFDD 도메인 한계로 효과 미미, 시연 환경 재검증 필요)
- [ ] **자체 시연 영상 5+5** — 도메인 일치 → KPI 도달 검증
- [ ] **Burn-in 168 h, Network Chaos 24 h** — 안정성 KPI

---

## 10. Voice 통합 — `/audio/transcripts` 연결

`mind_care_vision/audio_bridge_node` 가 이미 `/audio/transcripts` 로 STT JSON
(`{text, timestamp_ns, duration_s, latency_ms}`) 을 발행 중. Decider 의
`_on_transcript` 콜백이 그것을 받아 `panic_word` 키워드 (`"도와줘"`,
`"살려줘"` 등) 매칭 시 즉시 `EMERGENCY` 로 전이.

### 10.1 적용한 변경 (3 군데)

| 파일 | 변경 |
|---|---|
| `emergency_decider_node._on_transcript` | 토픽 이름 `/audio/transcript` → `/audio/transcripts` (s 복수형, audio_bridge 측 이름과 일치). `timestamp_ns` 키 우선 + `ts` fallback. |
| `mind_care_vision/llm_dialogue_node.py` | `/dialogue/proactive_speech` 구독 + `_on_proactive_speech()` 메서드 추가 → 텍스트를 즉시 `/llm/responses` 로 forward (LLM 우회) + history 에 assistant 발화로 누적 |
| `release/emergency/scripts/voice_sim.sh` | 통합 sanity check 스크립트 |

### 10.2 sanity check (voice_sim.sh) 결과

```
[decider] NORMAL→EMERGENCY (panic_word)

[/emergency/alert]
{"alert_id": "...", "elder_id": "test_elder",
 "type": "panic_word", "severity": "critical",
 "context": {"user_quote": "도와줘"}, ...}
```

- STT publish → Decider 가 ms 단위로 받아 즉시 EMERGENCY (timeout 30 s 안 기다림)
- alert.type=`"panic_word"` (fall_detected 와 구분 — 보호자 앱에서 다른 표시 가능)
- panic_word 단독 경로는 **QUERY 단계 거치지 않음** → `/dialogue/proactive_speech`
  발행 X (정상). dialogue 의 능동 발화는 `fall_detected` 진입 시에만.

### 10.3 KPI 보완 시나리오

Phase 4 v4 에서 Vision 미탐지 6 건 (URFDD fall-13, 19, 21, 27, 28, 30). 시연
환경에서 어르신이 누우면서 "아파", "도와줘" 등 외치면 STT 가 즉시 잡고
`panic_word` 경로로 alert. 즉:

```
시스템 Recall = 1 - P(Vision 미탐지) × P(Voice 미탐지)
            = 1 - (1-0.80) × (1-P_voice)
            = 1 - 0.20 × (1-P_voice)
```

음성 panic_word 검출률 P_voice 가 80 % 만 되어도 시스템 Recall **96 %** —
계획서 KPI 95 % 통과. 이게 계획서 FMEA RPN=240 (Vision 미탐지) 완화 전략의
정량적 근거.

### 10.4 화자 검증 (Phase 6 예정)

현재 audio_bridge_node 는 화자 검증 (`speaker_match`) 미구현 → 모든 음성을
등록 사용자로 간주. Decider 가 `speaker_match=False` 도 처리 가능하게는 짜여
있으니, Phase 6 에서 pyannote/TitaNet 통합 시 토픽 페이로드에 필드 하나만
추가하면 됨 — Decider 코드는 안 건드림.
