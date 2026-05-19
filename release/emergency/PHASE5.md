# Phase 5 — Emergency Decider + Alert Dispatcher + API Gateway

계획서 §Emergency / §Alert 와 §UI 의 백엔드 측면 구현. 프론트엔드는 별도 팀이
구현하므로 본 문서는 **백엔드가 FE 에 노출하는 API 계약과 내부 흐름** 에 집중.

## 1. 컴포넌트

```
ROS 토픽
  /vision/state          (Phase 1/2)
  /vision/fall_state     (Phase 4)
  /audio/transcript      (mind_care_vision STT)
        │
        ▼
┌────────────────────┐    /emergency/alert    ┌─────────────────────┐
│ emergency_decider  │ ───────────────────►   │ alert_dispatcher    │
│  (상태 머신 + 30s) │                        │  3중 채널 + 큐       │
└────────┬───────────┘                        └─────────────────────┘
         │ /dialogue/proactive_speech                    │
         ▼                                                ▼
   dialogue 노드                                /emergency/delivery
                                                          │
                                                          ▼
                                              ┌─────────────────────┐
                                              │ api_gateway_node    │
                                              │ (FastAPI + WS)      │
                                              └────────┬────────────┘
                                                       │
                                            HTTP / WS / FCM Push
                                                       │
                                                       ▼
                                              📱 보호자 모바일 앱
```

## 2. 패키지

```
release/emergency/
├── PHASE5.md
├── requirements.txt
├── patches/
│   └── 0001-add-proactive-speech.diff      ← mind_care_vision 패치
├── mind_care_emergency/                    ← Decider + Dispatcher
│   ├── package.xml / setup.py
│   ├── mind_care_emergency/
│   │   ├── decider_states.py               ← 순수 Python 상태 머신 (테스트 가능)
│   │   ├── emergency_decider_node.py
│   │   ├── alert_dispatcher_node.py
│   │   ├── alerts_db.py                    ← SQLite 큐
│   │   └── channels/
│   │       ├── base.py
│   │       ├── local_buzzer.py             ← 항상 시도 (오프라인 보장)
│   │       ├── fcm.py                      ← Firebase Admin SDK
│   │       ├── twilio_sms.py
│   │       └── mock.py                     ← 시연용
│   ├── config/emergency_params.yaml
│   ├── launch/emergency.launch.py
│   └── tests/test_decider_states.py        ← pytest 7 시나리오
└── mind_care_api/                          ← FE 노출 게이트웨이
    ├── package.xml / setup.py
    ├── mind_care_api/
    │   ├── schemas.py                      ← Pydantic = OpenAPI 계약
    │   ├── db.py                           ← SQLAlchemy 2.x (같은 SQLite 공유)
    │   ├── auth.py                         ← X-Api-Key
    │   ├── ws.py                           ← WebSocket 브로드캐스터
    │   ├── ros_bridge.py                   ← 별도 thread 에서 rclpy.spin
    │   ├── routes/
    │   │   ├── status.py
    │   │   ├── alerts.py
    │   │   ├── guardians.py
    │   │   └── reports.py
    │   ├── app.py                          ← FastAPI 팩토리
    │   └── api_gateway_node.py             ← uvicorn 진입점 (ros2 entry)
    ├── config/api_params.yaml
    └── launch/api_gateway.launch.py
```

## 3. FE 와의 계약 (요약)

상세 스키마는 `schemas.py` + 자동 생성되는 `/openapi.json` 참조.

| Method | Path | 응답 모델 |
|---|---|---|
| GET   | `/api/v1/health` | `Health` |
| GET   | `/api/v1/elders/{id}/status` | `Status` |
| GET   | `/api/v1/elders/{id}/daily-report?date=YYYY-MM-DD` | `DailyReport` |
| GET   | `/api/v1/alerts?elder_id=&since=&status=&limit=` | `Alert[]` |
| GET   | `/api/v1/alerts/{alert_id}` | `Alert` |
| POST  | `/api/v1/alerts/{alert_id}/ack` (body: `AckRequest`) | `{ok:true}` |
| GET   | `/api/v1/guardians?elder_id=` | `Guardian[]` |
| POST  | `/api/v1/guardians` (body: `GuardianCreate`) | `Guardian` (api_key 1회 노출) |
| DELETE| `/api/v1/guardians/{id}` | `204` |
| WS    | `/api/v1/stream?elder_id=...&token=...` | `WSMessage` 스트림 |

**WebSocket 메시지** (모두 `{type, data}`):
- `status`         — 어르신 상태 갱신 (1 Hz 또는 변경 시)
- `alert`          — 새 알림 발생
- `delivery`       — 채널별 발송 결과
- `alert_status`   — 알림 상태 변경 (다른 보호자가 ACK 등)
- `ping`           — keep-alive

**FCM 푸시 페이로드** (Dispatcher → Firebase → 앱):
```json
{
  "notification": {"title": "🚨 마음돌봄 응급 알림", "body": "..."},
  "data": {"alert_id":"...","type":"fall","severity":"critical",
           "deeplink":"mindcare://alerts/<uuid>"}
}
```
앱이 알림 탭 → deeplink 로 alert 상세 화면 → "확인" 버튼이 `POST /alerts/{id}/ack` 호출.

## 4. 빌드 + 실행

```bash
# 0. 의존성
source ~/마음돌봄/.venv-ros/bin/activate
pip install -r ~/마음돌봄/release/emergency/requirements.txt

# 1. ROS 워크스페이스에 심링크
ln -sfn ~/마음돌봄/release/emergency/mind_care_emergency  ~/ros2_ws/src/mind_care_emergency
ln -sfn ~/마음돌봄/release/emergency/mind_care_api        ~/ros2_ws/src/mind_care_api
cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select mind_care_emergency mind_care_api
source install/setup.bash

# 2. dialogue 패치 (Phase 5 능동 발화 채널)
cd ~/마음돌봄/mind_care_vision
git apply ~/마음돌봄/release/emergency/patches/0001-add-proactive-speech.diff

# 3. 단위 테스트 (decider 상태 머신)
pytest ~/마음돌봄/release/emergency/mind_care_emergency/tests/ -v

# 4. 실행 (시연: mock 모드)
ros2 launch mind_care_emergency emergency.launch.py dispatch_mode:=mock
ros2 launch mind_care_api       api_gateway.launch.py dev_open:=true
# → http://localhost:8000/docs 에서 Swagger UI 확인

# 5. 실행 (운영: FCM/Twilio 자격증명)
export FIREBASE_CRED_PATH=/path/to/firebase-credentials.json
export TWILIO_ACCOUNT_SID=AC...
export TWILIO_AUTH_TOKEN=...
export TWILIO_FROM_PHONE=+1...
ros2 launch mind_care_emergency emergency.launch.py dispatch_mode:=auto
ros2 launch mind_care_api       api_gateway.launch.py
```

## 5. KPI 매핑

| 계획서 KPI | 구현 위치 |
|---|---|
| E2E p95 ≤ 30 s (낙상→알림) | `decider_states.DeciderConfig.query_timeout_s=30.0` + dispatcher 즉시 발송 |
| Alert Delivery 성공률 ≥ 99.9% | 3중 채널 + SQLite 재시도 큐 (백오프 1s/5s/30s/5min, 6회) |
| 로컬 알람 (네트워크 단절) | `LocalBuzzerChannel` 가 항상 시도 — FCM/SMS 실패해도 부저는 동작 |
| FMEA Vision 미탐지 (RPN=240) | Voice 응급어 `panic_word` + `fall_confirmed` 의 AND 결합 |
| FMEA TV/가족 음성 (RPN=168) | `_on_transcript` 에서 `speaker_match=False` 시 panic_word 무시 |

## 6. 다음

- [ ] 실제 Firebase 프로젝트 등록 + service account JSON
- [ ] 보호자 등록 UI (FE 팀)
- [ ] daily-report 의 presence_ratio 정확 계산 — `ros_bridge` 가 vision_state 도 events 누적
- [ ] HTTPS / Let's Encrypt — uvicorn 앞에 nginx
- [ ] Burn-in 168시간 + Network Chaos 24시간 (계획서 stress test)
