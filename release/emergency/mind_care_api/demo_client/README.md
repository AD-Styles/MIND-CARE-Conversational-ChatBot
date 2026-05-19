# Demo Client — 시연용 HTML WebSocket 알람 뷰어

`urgent_alarm_app` Flutter 통합 전, 시연 D-day 에서 노트북 브라우저로
`/emergency/alert` 를 받아 빨간 풀스크린 알람을 띄우는 단일 파일 클라이언트.

## 사용

### 1) Xavier 에서 API 띄우기

```bash
cd ~/마음돌봄/release/emergency/mind_care_api
MIND_CARE_DEV_OPEN=1 \
  uvicorn mind_care_api.app:create_app --factory \
    --host 0.0.0.0 --port 8000
```

- `MIND_CARE_DEV_OPEN=1` → `?token=` 인증 생략 (시연 편의).
- `0.0.0.0` → LAN 안의 노트북에서 접속 가능.

### 2) 노트북 브라우저에서 열기

API 가 정적 파일로 마운트하므로 (`app.py` 수정 후):

```
http://<xavier-LAN-IP>:8000/demo/
```

직접 파일을 열고 싶다면 URL 파라미터로 호스트 지정:

```
file:///home/user/마음돌봄/release/emergency/mind_care_api/demo_client/index.html?host=192.168.1.42:8000&elder_id=elder_01
```

### URL 파라미터

| 키 | 기본 | 설명 |
|---|---|---|
| `host` | 페이지 호스트 | API 서버 `host:port` |
| `elder_id` | `elder_01` | 모니터링할 어르신 ID |
| `token` | (없음) | `dev_open=0` 일 때 보호자 API key |

## 동작

- 상단 dot — 초록 = 연결됨, 빨강 = 끊김 (자동 재연결)
- 상단 **🔔 소리 켜기** 버튼 — 브라우저 autoplay 정책상 첫 진입 후 한 번 클릭 필요.
  클릭하면 0.15s 1kHz 비프 (unlock 확인) + 초록 "🔔 소리 켜짐".
- "현재 상태" 카드 — 최신 status / alert
- "최근 알림" — 최대 20건 시간 역순
- `type=fall` 또는 `panic_word` 또는 `severity=critical` 수신 시
  → 빨간 풀스크린 깜빡임 + 사이렌 아이콘 + **Web Audio 합성 사이렌 (900↔600 Hz, 200ms 주기)** + "확인" 버튼
- "확인" 클릭 시 사이렌 정지 + 오버레이 닫힘

## 사이렌 음

브라우저 측 사이렌은 **Web Audio API (OscillatorNode)** 로 합성 — `<audio>` 태그
+ autoplay 정책이 Firefox 등에서 불안정해서 합성으로 전환. 주파수/주기는
`index.html` 안의 `playSiren()` 함수 상수 (`900`, `600`, `200ms`) 에서 조정.

`alarm.wav` 는 사이렌 톤 미리듣기 (paplay/aplay 로 Xavier 스피커 직접 검증) 및
참고용. 시연 클라이언트는 더 이상 사용하지 않음.

```bash
# Xavier 스피커로 미리듣기
paplay release/emergency/mind_care_api/demo_client/alarm.wav

# 톤 다시 만들기 (F_HI, F_LO, PERIOD, AMP 조정 후)
python release/emergency/mind_care_api/demo_client/gen_alarm.py \
  release/emergency/mind_care_api/demo_client/alarm.wav
```

## E2E 시연 흐름

1. Xavier: `mindcare-llama` + `mindcare-hri` + `mind_care_api` 기동
2. 노트북 브라우저: `http://<xavier>:8000/demo/`
3. "도와줘" 발화 → 1–3초 내 빨간 알람 + 히스토리에 `panic_word` 추가
4. 거실 낙상 → 동일하게 `fall` 알람

## TODO (Flutter 통합 시)

FE 팀이 `urgent_alarm_app/lib/main.dart` 에서 동일 WS endpoint 로 연결하면
완전한 모바일 푸시 (FCM) 와 LAN WebSocket 양쪽 모두 사용 가능.
