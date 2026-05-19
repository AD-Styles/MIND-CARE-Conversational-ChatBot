# 마음돌봄 Vision 통합 (Phase 1 — 에뮬레이터)

`mind_care_vision` 의 LLM 대화 노드(`llm_dialogue_node`) 가 **카메라 인식 결과(표정·존재·인식 ID·낙상)** 를
시스템 프롬프트로 받을 수 있게 만드는 비전 파이프라인 통합 자산입니다.

> 사용자(`~/마음돌봄/mind_care_vision`)의 **작업 트리는 건드리지 않는** 방식으로 배포합니다.
> - 새 ROS 2 패키지(`mind_care_perception`) 는 별도 디렉터리에 들어가고,
> - 기존 `llm_dialogue_node.py` 변경분은 **`.diff` 패치 파일**로 제공합니다.

---

## 1. 디렉터리 구조

```
release/vision/
├── README.md                                  ← 이 문서
├── mind_care_perception/                      ← 새 ROS 2 (ament_python) 패키지
│   ├── package.xml
│   ├── setup.py / setup.cfg
│   ├── resource/mind_care_perception
│   ├── mind_care_perception/
│   │   ├── __init__.py
│   │   └── vision_emulator_node.py            ← /vision/state 더미 발행
│   └── launch/
│       └── perception_dryrun.launch.py
└── patches/
    └── 0001-add-vision-context-to-dialogue.diff   ← dialogue_node 패치
```

---

## 2. Phase 로드맵

| Phase | 목표 | 핵심 컴포넌트 | 상태 |
|---|---|---|---|
| **1. 에뮬레이터** | DeepStream/카메라 없이 dialogue_node 와 통합 검증 | `vision_emulator_node` (JSON publish) + dialogue 패치 | **현재** |
| 2. 실비전 | USB 카메라 → DS 8.0 → YOLOv8n-face + MiniXception | `~/proj/deepstream_emotion_hri` 자산 + pyds | 준비 |
| 3. 얼굴 식별 | 등록 얼굴 ↔ 어르신 ID 매핑 | InsightFace (SCRFD + ArcFace) | 계획 |
| 4. 낙상(선택) | 자세 추정 기반 낙상 감지 | YOLOv8-pose 또는 ST-GCN | 계획 |

Phase 1 의 토픽 스키마(`/vision/state`)는 Phase 2+ 에서도 동일하게 유지되므로,
대화 노드 측은 **Phase 2 로 넘어가도 코드 변경이 필요 없습니다**.

---

## 3. /vision/state 토픽 스키마

`std_msgs/String` 에 담긴 JSON. (Phase 1 에뮬레이터 / Phase 2 DeepStream 모두 동일)

```json
{
  "ts": 1714000000.0,
  "presence": true,
  "face_id": "elder_01",
  "face_name": "철수 어르신",
  "emotion": "sad",
  "emotion_conf": 0.74,
  "emotion_scores": [0.02, 0.01, 0.05, 0.05, 0.10, 0.74, 0.03],
  "track_count": 1,
  "fall_detected": false
}
```

| 필드 | 의미 |
|---|---|
| `presence` | 카메라 앞에 누군가 있는지 |
| `face_id` / `face_name` | 등록 얼굴 매칭 결과 (없으면 `null`/`""`) |
| `emotion` | 7-class — `angry / disgust / fear / happy / neutral / sad / surprise / unknown` |
| `emotion_conf` | softmax top-1 확률 (0~1) |
| `fall_detected` | 낙상 추정 (Phase 4 활성화 후 의미 있음) |

---

## 4. 적용 절차 (Phase 1)

### 4-1. mind_care_perception 패키지를 워크스페이스에 추가

```bash
# 사용자 워크스페이스(예: ~/ros2_ws/src) 로 패키지를 복사 (또는 심링크)
cp -r ~/마음돌봄/release/vision/mind_care_perception ~/ros2_ws/src/

cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select mind_care_perception
source install/setup.bash
```

### 4-2. dialogue_node 패치 적용

```bash
cd ~/마음돌봄/mind_care_vision
git apply ~/마음돌봄/release/vision/patches/0001-add-vision-context-to-dialogue.diff
# 또는: patch -p1 < ~/마음돌봄/release/vision/patches/0001-add-vision-context-to-dialogue.diff
```

패치는 다음 3가지를 추가합니다:

1. 새 파라미터 `vision_enabled` (기본 False), `vision_topic`, `vision_stale_s`
2. `/vision/state` 구독 + 콜백
3. `_respond()` 안 — RAG 다음, history 앞에 vision 컨텍스트 system 메시지 삽입

### 4-3. 비전 켜기 — `config/hri_params.yaml`

```yaml
vision_enabled: true
vision_topic: "/vision/state"
vision_stale_s: 3.0      # 이 시간 이상 오래된 데이터는 무시
```

### 4-4. 실행 (터미널 2개)

```bash
# 터미널 1 — 비전 에뮬레이터 (시나리오 골라서)
source ~/ros2_ws/install/setup.bash
ros2 launch mind_care_perception perception_dryrun.launch.py scenario:=rotating

# 터미널 2 — 기존 HRI 시스템 (대화 노드 + STT/TTS …)
source ~/마음돌봄/mind_care_vision/install/setup.bash
ros2 launch mind_care_vision hri_system.launch.py
```

---

## 5. 시나리오

`scenario` 인자로 에뮬레이터 동작 변경:

| 값 | 동작 |
|---|---|
| `rotating` (기본) | 7 단계 순환 — 부재 → 미인식 입장 → 인식 → happy → sad → neutral → 부재 |
| `absent` | 항상 부재 (가벼운 안부 위주 대답을 유도) |
| `happy` | 항상 인식 + happy |
| `sad` | 항상 인식 + sad (공감 응답을 유도) |
| `fall` | 인식 + fear + `fall_detected=true` (보호자/119 권유 트리거) |
| `random` | 랜덤 감정 |

```bash
ros2 launch mind_care_perception perception_dryrun.launch.py scenario:=sad
ros2 launch mind_care_perception perception_dryrun.launch.py scenario:=fall
```

직접 토픽 확인:
```bash
ros2 topic echo /vision/state
```

---

## 6. 검증 포인트

다음을 눈으로 확인하면 통합이 잘 된 것입니다:

1. **에뮬레이터 발행 확인**
   ```bash
   ros2 topic hz /vision/state   # ~0.5 Hz (period 2s)
   ros2 topic echo /vision/state | head -n 30
   ```

2. **dialogue_node 로그**
   - 시작 시: `Vision context enabled. topic=/vision/state`
   - 시작 시 한 줄: `vision=True` 가 hooks 라인에 포함됨

3. **응답 톤 변화** (수동 비교)
   - 같은 질문 "오늘 좀 우울해" 를 — `scenario:=happy` 일 때와 `scenario:=sad` 일 때 던져 보면
     모델이 공감 강도/길이를 다르게 가져갑니다.
   - `scenario:=absent` 에서는 답이 짧고 안부 위주.
   - `scenario:=fall` 에서는 보호자/119 안내가 부드럽게 끼어 듭니다.

4. **stale 처리** — 에뮬레이터를 끄고 3초 뒤 사용자 발화를 보내면 vision system 메시지는 빠지고
   순수 RAG + history 만으로 응답합니다.

---

## 7. 패치 미리보기 (요약)

```python
# llm_dialogue_node.py 안에 추가되는 핵심 흐름
def _on_vision_state(self, msg):
    state = json.loads(msg.data)
    self._vision_state = state
    self._vision_ts    = time.time()

# _respond() 안에서:
if vision_enabled:
    block = _format_vision_for_prompt(self._vision_state)
    if block:
        messages.append({"role": "system", "content": block})

# 예시 출력:
# "[감지 정보] 표정은 슬퍼 보이시는 모습 (신뢰도 74%), 인식: 철수 어르신.
#  이 정보를 자연스럽게 반영해 답하되, 분석 결과를 그대로 언급하지는 마세요."
```

낙상이 켜지면:
```
"⚠️ 낙상이 감지되어 있음 — 즉시 안부와 함께 보호자/119 연락을 부드럽게 권유"
```

부재 상태:
```
"[감지 정보] 지금 카메라 앞에 어르신이 보이지 않습니다.
 응답은 가볍고 짧게, 부담 없는 안부 위주로 해 주세요."
```

---

## 8. Phase 2 로 가는 길

Phase 2 에서는 `vision_emulator_node` 가 **DeepStream 파이프라인 노드** 로 교체됩니다.
이미 자산이 준비되어 있어 통합 비용이 적습니다:

- DS 8.0 SDK: `/opt/nvidia/deepstream/deepstream-8.0/`
- 기준 파이프라인: `~/proj/deepstream_emotion_hri/`
  - YOLOv8n-face (PGIE) + NvDCF tracker + MiniXception emotion (SGIE)
- pyds: `~/proj/pyds-1.2.2-cp312-cp312-linux_x86_64.whl`

대화 노드(`llm_dialogue_node.py`)는 **수정할 필요 없음** — 토픽 스키마가 동일하기 때문.
새 노드만 만들고 `vision_topic` 만 바꿔 끼우면 됩니다.

---

## 9. 롤백

비전 통합을 끄거나 되돌리려면:

```bash
# yaml 끄기
vision_enabled: false

# 또는 패치 되돌리기
cd ~/마음돌봄/mind_care_vision
git apply -R ~/마음돌봄/release/vision/patches/0001-add-vision-context-to-dialogue.diff
```
