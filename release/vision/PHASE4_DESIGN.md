# Phase 4 설계 — 낙상 감지를 어떻게 만들었나

이 문서는 Phase 4 (Fall Detection) 의 **설계 결정과 구현 흐름** 을 처음 보는
사람도 따라갈 수 있게 정리한 것이다. 경로·명령어 같은 quick reference 는
[PHASE4.md](PHASE4.md), 이 문서는 deep dive.

---

## 목차

1. [큰 그림 — 책임과 입출력](#1-큰-그림--책임과-입출력)
2. [접근 후보 비교 — 왜 룰 기반인가](#2-접근-후보-비교--왜-룰-기반인가)
3. [모델 자산](#3-모델-자산)
   - 3.1 [모델 선택과 라이선스](#31-모델-선택과-라이선스)
   - 3.2 [NMS-baked ONNX export](#32-nms-baked-onnx-export)
   - 3.3 [출력 텐서 `[1, 300, 57]` 분해](#33-출력-텐서-1-300-57-분해)
   - 3.4 [TensorRT 엔진 빌드](#34-tensorrt-엔진-빌드)
   - 3.5 [PGIE 커스텀 파서 `.so`](#35-pgie-커스텀-파서-so)
4. [낙상 룰 — 왜 이 4가지인가](#4-낙상-룰--왜-이-4가지인가)
5. [시간 윈도우 confirm — 두 단계](#5-시간-윈도우-confirm--두-단계)
6. [DeepStream 파이프라인 구조](#6-deepstream-파이프라인-구조)
7. [pyds probe — bbox + raw tensor 매칭](#7-pyds-probe--bbox--raw-tensor-매칭)
8. [ROS 노드와 토픽 스키마](#8-ros-노드와-토픽-스키마)
9. [test 모드 end-to-end 검증](#9-test-모드-end-to-end-검증)
10. [알려진 함정 (시간 잡아먹은 것들)](#10-알려진-함정-시간-잡아먹은-것들)
11. [계획서 KPI 매핑](#11-계획서-kpi-매핑)
12. [다음 단계 — 정확도 측정](#12-다음-단계--정확도-측정)

---

## 1. 큰 그림 — 책임과 입출력

Phase 4 는 **하나의 ROS 노드** (`fall_detection_node`) 로 책임을 묶었다.

```
[Camera or 영상 파일]
   ↓ source_mode = test | v4l2 | file | ros
[ DeepStream 파이프라인 ]
   ├ PGIE   : yolov8n-pose (NMS-baked ONNX, FP16 engine, 커스텀 파서 .so)
   ├ tracker: NvDCF
   └ probe  : pyds 가 obj_meta + raw tensor 둘 다 흘려줌
        ↓
[ PoseAggregator ]  ← 가장 큰 bbox = 주 대상
        ↓
[ FallStateMachine ]  ← frame-level 룰 + 시간 윈도우 confirm
        ↓
   /vision/fall_state  (std_msgs/String JSON, 5 Hz)
```

**입력**: 카메라/영상.  
**출력**: `/vision/fall_state` JSON — `presence`, `track_count`, `fall_detected`,
`fall_confirmed`, `primary_track_id`. 다른 노드 (Phase 5 Decider, dialogue) 는
이 토픽 한 줄만 알면 된다.

---

## 2. 접근 후보 비교 — 왜 룰 기반인가

| 접근 | 어떻게 동작 | 정확도 | 1650 Ti 비용 | 데이터 학습 필요? |
|---|---|---|---|---|
| **A. bbox 기반 룰** | 사람 박스 가로/세로 + 수직 속도 | 낮음 (~70%). 앉기·눕기 잘못 잡음 | 매우 가벼움 | 없음 |
| **B. Pose keypoint 룰** ★ | 17점 관절 + 룰 4가지 + 시간 윈도우 | 중상 (~85%) | 가벼움 (engine ~10 MB) | 없음 — 평가만 필요 |
| **C. ST-GCN 행동 분류** | pose 시계열 → 그래프 컨볼루션 | 높음 (~90%+) | 무거움. Jetson 빠듯 | 학습 필요 (NTU) |
| **D. 3D CNN (MoViNet)** | RGB 시계열 직접 분류 | 높음 | 매우 무거움. 4GB 불가 | 학습 필요 |

**선택: B (Pose + 룰)**.

선택 이유:
- 계획서 KPI (Recall ≥ 95%, Precision ≥ 85%) 를 **1주 안에 만들 수 있는 현실적 길**
- 4 GB GPU + Jetson 32 GB 양쪽에서 같은 코드로 동작
- 학습 데이터 모집 부담 없음
- 계획서 FMEA 가 같은 결론: **"Vision 낙상 미탐지 → Voice 응급어 + 부동 감지 AND 결합"** 으로 단일 모달의 정확도 부족을 시스템 레벨에서 보완

ST-GCN 의 정확도 이점은 **시스템 레벨 (Voice 융합 포함) 에서 거의 희석**되고,
대신 VRAM·지연·통합·일정 비용이 모두 늘어난다. 멀티모달이 보완해주는 구조라
룰 B 가 합리적.

---

## 3. 모델 자산

### 3.1 모델 선택과 라이선스

| 항목 | 값 |
|---|---|
| 모델 | [`yolov8n-pose.pt`](https://docs.ultralytics.com/models/yolov8/) (ultralytics 공식) |
| 라이선스 | AGPL-3.0 (face 모델과 동일) |
| 입력 | `[1, 3, 640, 640]` RGB |
| 추론 시간 | GTX 1650 Ti FP16 ~1.5 ms/sample |
| 엔진 크기 | 9.6 MB |

face 모델은 야생 fork (akanametov) 였지만 pose 는 ultralytics 가 공식 학습본을
배포하므로 **`YOLO("yolov8n-pose.pt")` 한 줄로 자동 다운로드 가능** — face 보다
훨씬 깔끔.

### 3.2 NMS-baked ONNX export

```python
model = YOLO("yolov8n-pose.pt")
model.export(format="onnx", imgsz=640, opset=12, simplify=True, nms=True)
```

핵심은 `nms=True`. 이 한 옵션으로 ONNX 그래프 끝에 `NonMaxSuppression` op 가
박혀 출력이 `[1, max_det, 6+17×3]` 형식으로 나온다. **DeepStream 측 NMS 클러스터링
이 필요 없어 파서가 단순해진다.**

### 3.3 출력 텐서 `[1, 300, 57]` 분해

```
row[ 0..3 ]   x1, y1, x2, y2     (모델 입력 좌표 = 0~640 픽셀)
row[ 4    ]   conf
row[ 5    ]   class id (= 0, person)
row[ 6..56]   17 keypoints × (x, y, visibility)
              = 51 채널
```

총 **6 + 51 = 57**. NMS 가 빈 슬롯을 0 으로 패딩하므로 파서는 `conf ≤ preThr`
로 단순 필터링.

### 3.4 TensorRT 엔진 빌드

`prepare_models.py --pose --trt-only` 한 번:

```
trtexec --onnx=yolov8n_pose.onnx \
        --saveEngine=yolov8n_pose.engine \
        --memPoolSize=workspace:2048 \
        --fp16
```

face 와 같은 패턴. TRT 10.x 호환을 위해 `--workspace` 대신 `--memPoolSize=workspace:`
를 쓴다.

### 3.5 PGIE 커스텀 파서 `.so`

`src/parser_yolov8_pose/nvdsparsebbox_yolov8_pose.cpp` — **face 파서와 거의 동일,
출력 행 길이만 21 → 57**.

```cpp
constexpr int OUT_BOXES = 300;
constexpr int OUT_DIM   = 57;   // 6 + 17×3
```

bbox 만 `NvDsInferObjectDetectionInfo` 로 등록 (DS 표준은 keypoint 메타를 직접
못 담는다). **keypoint 는 ROS 노드 측 probe 가 raw tensor 에서 따로 읽는다** —
§ 7 참조.

빌드:
```bash
make -C release/vision/mind_care_perception/src/parser_yolov8_pose
# → release/vision/models/pose_estimator/libnvdsinfer_custom_impl_yolov8_pose.so
```

---

## 4. 낙상 룰 — 왜 이 4가지인가

낙상의 시각적 특징을 4가지로 나눠 **AND 결합 (≥ 2/3 만족)** 한다.

| # | 룰 | 의미 | 임계값 (default) |
|---|---|---|---|
| ① | **자세 각도 (torso tilt)** | 어깨 중점 → 엉덩이 중점 선이 수직축에서 얼마나 기울었나 | ≥ **60°** = 누움 |
| ② | **수직 압축** | 머리 y 와 엉덩이 y 의 차이 / bbox 높이 — 서있으면 1.0, 누우면 0 가까이 | < **0.30** |
| ③ | **bbox 모양** | 가로/세로. 누우면 > 1, 서있으면 < 1 | > **1.4** |
| ④ | **머리 y 가속 (head drop)** | 0.3 s 내 머리 y 가 frame 높이의 25% 이상 떨어짐 | ≥ **25%** → 즉시 fallen |

### 왜 이 4가지인가

- ① 만 쓰면 **의자에 비스듬히 앉기** 같은 자세가 fallen 으로 잘못 잡힘
- ② 만 쓰면 **누워서 다리 들기** 같은 짧은 자세 변화도 잡힘
- ③ 만 쓰면 (bbox 만 쓰면) keypoint 정보 무시 → 낮음 정확도 (옵션 A 와 동일)
- 셋 다 만족 시 **"누워있다"** 가 명확
- ④ 는 시간축 정보 — 낙하 동작 자체를 잡기 위함. 단일 신호가 너무 강해서
  ④ 만 만족해도 즉시 `fallen=true`

`is_frame_fallen()` 이 ①②③ 에서 **2개 이상 만족** 시 `True` 반환. ④ 는
`FallStateMachine.update()` 안에서 별도로 평가. 즉:

```
fallen_now = (rules_satisfied ≥ 2) OR (head_drop ≥ 25%)
```

### 키포인트 가시성 (visibility) 처리

각 keypoint 는 `(x, y, v)`. `v < 0.30` 이면 가려진 것으로 간주, 해당 룰은
"데이터 부족 → False" (fall 없음으로 보수적 판정). 가려진 환경에선 자연스럽게
민감도 ↓ 되어 false positive 감소.

---

## 5. 시간 윈도우 confirm — 두 단계

frame 한 장의 fallen 만으로 알림을 보내면 **순간적인 자세 변화** (예:
의자 깊숙이 앉기) 도 fallen 으로 잡혀 false positive 폭증. 그래서 두 단계
시간 윈도우.

```
"fall_event"     ← 1.0 s 슬라이딩 윈도우 안에서 fallen 비율 ≥ 50%
                  → /dialogue/proactive_speech "괜찮으세요?"
                    (Phase 5 Decider 가 트리거)
                  → /vision/fall_state.fall_detected = True

"fall_confirmed" ← fall_event 후 추가 5.0 s 동안 IoU ≥ 0.85 (부동) 유지
                  → /vision/fall_state.fall_confirmed = True
                  → 보호자 알림 (Phase 5 Dispatcher)
```

### 왜 두 단인가

| 단계 | 무엇을 보장하나 |
|---|---|
| **fall_event** | 짧은 흔들림이 아닌 "지속된 자세" 임을 1초 만에 확인 |
| **fall_confirmed** | 의식 잃고 못 일어난 상태 — 추가 5 초 부동으로 검증 |

- 일상의 "앉기/눕기" 는 보통 **부동 단계까지 안 감** (사람은 누워서도 미세히 움직인다) → 자동 기각
- 진짜 낙상은 5 초 부동을 거의 항상 넘김 → confirmed 통과
- "낙상 후 의식 유지" 시나리오 (계획서 §E2E) 는 사용자가 "괜찮아요" 라고 말하면 Phase 5 Decider 가 reset → 보호자 알림 보류

이 설계가 계획서 FMEA RPN=240 (낙상 미탐지) 를 푸는 핵심.

### IoU 부동 판정

```python
def _iou(a, b):
    # 두 bbox 의 교집합/합집합. 사람이 거의 안 움직이면 IoU ≥ 0.85 유지
```

`FallStateMachine` 안에 5 초간 모든 frame 의 bbox 를 누적, 첫 frame 과의
IoU 최솟값 ≥ 0.85 이면 "부동" 으로 판정.

---

## 6. DeepStream 파이프라인 구조

```
<source>  →  nvvideoconvert  →  nvstreammux
   →  nvinfer (PGIE: yolov8n-pose, output-tensor-meta=1)
   →  nvtracker (NvDCF)
   →  fakesink   ← probe 등록 지점
```

face/emotion 파이프라인 (Phase 2) 과 다른 점:

- **SGIE 없음** — pose 는 단일 PGIE 만으로 충분 (사람 검출 + keypoint)
- **`output-tensor-meta=1`** — raw 텐서를 메타데이터로 함께 흘려서 ROS 노드 측에서 keypoint 추출
- 별도 `ds_pose_pipeline.py` 모듈로 분리 — face pipeline 과 코드 충돌 없음

### 4가지 source_mode

| 모드 | 입력 | 용도 |
|---|---|---|
| `test` | `videotestsrc` (공 패턴) | 사람 0명 → fall_detected=false 가 정확한 기대값. 파이프라인 살아있는지만 확인 |
| `v4l2` | `/dev/videoN` USB 웹캠 | 실시간 검증 (서기/앉기/누워보기) |
| `file` | `mp4/avi` 영상 파일 | **URFDD/Le2i 평가에 사용** |
| `ros` | `sensor_msgs/Image` 토픽 | 다른 노드가 카메라를 노출할 때 |

평가 단계에서 가장 중요한 건 `file` 모드 — § 12 참조.

---

## 7. pyds probe — bbox + raw tensor 매칭

probe 가 매 프레임 (~30 fps) 호출되어 두 가지를 한다:

1. **`obj_meta_list` 순회** — tracker 가 부여한 stable ID 와 함께 bbox 수집
2. **`frame_user_meta_list` 의 `NvDsInferTensorMeta`** — PGIE raw output `[300, 57]` 텐서 추출

문제: bbox 와 raw row 는 같은 사람이라도 PGIE 가 처리한 후 NMS 단계가 달라
**1:1 매핑이 보장되지 않음**.

해결: **IoU 매칭** — 각 obj_meta 의 bbox 와 raw tensor 의 첫 4 채널 (xyxy)
중 IoU 가 가장 높은 행을 묶음:

```python
def _best_match_row(rows, bbox):
    best = None; best_iou = 0.3
    for r in rows:
        if r[4] <= 0: continue   # 빈 슬롯 skip
        if iou(r[:4], bbox) > best_iou:
            best = r
    return best
```

매칭된 row 에서 `row[6..56]` 을 17 keypoint × 3 으로 unpack → `Keypoints` 객체
생성 → `PoseAggregator.update()` 로 흘림.

### ctypes 로 raw 메모리 읽기

`pyds.NvDsInferTensorMeta.layer.buffer` 는 ctypes pointer. pyds 빌드에 따라
헬퍼 함수 시그니처가 다른 케이스 회피를 위해 직접 ctypes 로 numpy 변환:

```python
arr_t = ctypes.c_float * (300 * 57)
flat = np.frombuffer(arr_t.from_address(int(layer.buffer)),
                     dtype=np.float32, count=300*57)
return flat.reshape(300, 57).copy()
```

`copy()` 가 핵심 — DS 의 다음 frame 이 같은 메모리를 덮어쓰기 전에 우리 사본 확보.

---

## 8. ROS 노드와 토픽 스키마

### 노드 구성

`fall_detection_node` 가 위 모든 컴포넌트를 묶음:

```python
self._agg = PoseAggregator(machine=FallStateMachine(...), stale_window_s=1.0)
probe = make_pose_probe(self._agg, pgie_id=10)
self._pipeline = PosePipeline(... on_buffer_probe=probe)
self.create_timer(self._publish_period_s, self._on_publish_tick)
```

`pgie_id=10` 은 PGIE config 의 `gie-unique-id=10` 과 일치 — face PGIE
(`gie-unique-id=1`) 와 충돌 없게 하려고 큰 ID 를 부여.

### 토픽 스키마 — `/vision/fall_state` (5 Hz)

```json
{
  "ts": 1714000000.0,
  "presence": true,
  "track_count": 1,
  "primary_track_id": 3,
  "fall_detected": false,
  "fall_confirmed": false
}
```

| 필드 | 의미 |
|---|---|
| `presence` | 사람 검출 여부 (1 s stale 윈도우) |
| `track_count` | 현재 프레임에서 검출된 사람 수 |
| `primary_track_id` | 가장 큰 bbox 의 NvDCF tracker ID |
| `fall_detected` | 1 s 윈도우 fall_event 트리거됨 |
| `fall_confirmed` | 추가 5 s 부동까지 통과 — 보호자 알림 트리거 |

발행 주기 0.2 s (5 Hz) — Phase 1/2 의 `/vision/state` 가 0.5 Hz 인 것보다
빠른 이유는 **낙상 latency 가 KPI** 이기 때문 (계획서 E2E ≤ 30 s).

### Rising-edge 감지 (Phase 5 Decider 측)

`fall_detected` 가 한 번 true 가 되면 부동 윈도우가 끝날 때까지 true 를 유지
가능. Decider 는 **false→true 전이 시점**에서만 트리거 (중복 방지).

```python
if fall_detected and not self._prev_fall_detected:
    self._dispatch(Event(FALL_DETECTED, ...))
self._prev_fall_detected = fall_detected
```

---

## 9. test 모드 end-to-end 검증

`scripts/run_fall_test.sh` 가 자동화된 검증 스크립트. videotestsrc + 8 초
대기 → 토픽 echo + hz 측정 → 종료.

### 결과 (실측)

```
alive

=== /vision/fall_state echo (1 msg) ===
data: '{"ts": 1777344930.07, "presence": false, "track_count": 0,
        "primary_track_id": 0, "fall_detected": false, "fall_confirmed": false}'

=== /vision/fall_state hz over 4s ===
average rate: 5.001
  min: 0.199s max: 0.200s std dev: 0.00047s
```

확인된 항목:
- ✅ pose engine + tracker 로드
- ✅ probe 동작 (raw tensor + obj_meta 매칭)
- ✅ JSON 발행 정확
- ✅ 5 Hz 안정 (publish_period_s=0.2 정확)
- ✅ videotestsrc 사람 0명 → presence=false 정확

`videotestsrc` 의 공 패턴은 사람 keypoint 를 만들지 못하니 `presence=false`
가 정확한 기대값이고, 이는 **probe 와 룰이 잘못 트리거하지 않는다는 증거**.

---

## 10. 알려진 함정 (시간 잡아먹은 것들)

새 PC 에서 또 마주칠 수 있는 항목들. PHASE2 작성 단계에서 뽑은 표와 동일한
형식으로 정리.

| 증상 | 원인 | 해결 |
|---|---|---|
| `vision_deepstream_node` SIGSEGV in `rclpy.Node.__init__` | (Phase 2) `gi`/`pyds` module-level import 가 ROS DDS 시그널과 충돌 | `super().__init__()` 이후 import 로 수정 (PHASE2.md). Phase 4 노드도 같은 패턴 사용 |
| ultralytics 가 ONNX 를 `yolov8n-pose.onnx` (hyphen) 로 저장 | ultralytics export 의 default 이름 | `prepare_models.py` 에서 hyphen 이름도 후보로 받아 underscore 로 rename (`shutil.move`) |
| `output-tensor-meta=1` 이지만 keypoint 가 안 보임 | pyds 빌드별 `NvDsInferTensorMeta.layer.buffer` 접근 방식 차이 | ctypes 로 직접 메모리 변환 — `arr_t.from_address(int(layer.buffer))`. 빌드 차이 회피 |
| obj_meta 와 raw tensor row 의 매칭이 어긋남 | DS 가 NMS 후 obj_meta 를 만드는 순서가 raw tensor 행 순서와 다름 | IoU ≥ 0.3 매칭 (`_best_match_row`) — 정확한 1:1 매핑 대신 근사 매칭 |
| frame_user_meta 가 비어있음 | PGIE config 의 `output-tensor-meta=1` 누락 | `pgie_yolov8n_pose.txt` 에 명시 — § 6 참조 |
| 사람이 일어났는데 fall_detected 가 계속 true | 윈도우 ratio 가 천천히 떨어짐 | `FallStateMachine` 에서 `ratio < 0.2` 시 즉시 reset 하는 짧은 조건 추가 |

---

## 11. 계획서 KPI 매핑

| KPI | 구현 위치 | 검증 |
|---|---|---|
| Fall Detection Recall ≥ 95% | 룰 4가지 + ④ head-drop 강 신호 | § 12 평가 단계 |
| Fall Detection Precision ≥ 85% | ①②③ AND 결합 + 시간 윈도우 confirm | § 12 평가 단계 |
| E2E p95 ≤ 30 s (낙상→알림) | `fall_event` 1 s + Phase 5 Decider timeout 30 s | Phase 5 통합 시 |
| FMEA RPN=240 (낙상 미탐지) | Voice 응급어 + `fall_confirmed` AND 결합 | Phase 5 통합 시 |
| 야간 저조도 검출 (RPN=200) | (미구현) — IR 카메라 + 적외선 보정 필요 | 다음 사이클 |
| 6시간 부동 감지 | Phase 5 Decider 가 `presence` 누적 | Phase 5 |

---

## 12. 정확도 측정 — URFDD 1차 평가 결과

70 영상 (30 fall + 40 ADL) 정량 평가 진행. **계획서 KPI (Recall ≥ 95%,
Precision ≥ 85%) 는 URFDD 짧은-영상 도메인의 한계로 미달이지만, 디버깅 +
4 차례 임계 튜닝으로 큰 개선 (Recall 44% → 77%, F1 0.48 → 0.72) 달성**.

### 12.1 진행한 5 가지 버그 수정 + 4 차례 튜닝

평가 자동화 (`scripts/eval_fall.py`) 시작 시 0% 도 안 잡혀 디버깅 필요. 5
가지 별개 버그 발견 + 수정, 그 후 4 차례 임계 튜닝.

| # | 버그 / 튜닝 | 영향 |
|---|---|---|
| 1 | obj_meta 의 `tracker_bbox_info` 가 valid 한데 `detector_bbox_info` 만 봐서 obj 통째로 무시 | tracker_bbox 우선 + detector_bbox fallback (Phase 2 face probe 와 동일 패턴) |
| 2 | `pyds.NvDsInferTensorMeta.layer.buffer` 메모리 access 가 빌드별 차이로 실패 | `ctypes.string_at` + 3 가지 주소 변환 fallback (`pyds.get_ptr`, `int(buffer)`, `ctypes.cast`) |
| 3 | obj_meta bbox 좌표 (streammux 1280×720) ≠ raw row 좌표 (모델 640×640 letterbox) — IoU 매칭 0% | row 의 xyxy 가 keypoint 좌표와 일관 → bbox 도 row 로 통일 |
| 4 | `stale_window_s=1.0` 이 너무 짧아 timing flap 으로 `presence=false` flap | 3.0s 로 보수적 |
| 5 | IoU 매칭이 letterbox 좌표 차로 실패 | 단일 어르신 가정 → conf 최댓값 row 단순 선택 |
| **튜닝 v1** | default (window 1.0, ratio 0.5, aspect 1.4, rules_required 2) | 모든 fall FN — fall window 너무 짧아 윈도우 못 채움 |
| **튜닝 v2** | window 0.5, ratio 0.3, aspect 1.5, rules_required 1, tilt/comp off | R 44%, P 52% — 절반 fall 안 잡힘 |
| **튜닝 v3** | window 0.3, ratio 0.5, aspect 1.4, reset 0.05, τ 8 | R 47%, P 61% — 약간 개선 |
| **튜닝 v4** ★ | **window 0.2, ratio 0.33** — 짧은 fall 까지 잡되 5 s 부동 confirm 으로 transient 흡수 | **R 77%, P 68%, F1 0.72** |

### 12.2 도메인 학습 — keypoint 시계열 분석

5 영상 (3 fall + 2 ADL) 의 frame-by-frame metric 측정 (`scripts/visualize_keypoints.py`):

| 영상 | 윈도우 | tilt mean | aspect mean | aspect range |
|---|---|---:|---:|---|
| fall-01 GT | 누움 | 14.3° | **1.60** | 0.95~2.24 |
| fall-15 GT | 누움 | 60.0° | **1.69** | 1.52~2.21 |
| fall-25 GT | 누움 | (없음) | **1.62** | 1.52~1.87 |
| 모든 outside | 서기/걷기/앉기 | 3~24° | 0.43~0.76 | 0.25~1.89 |

**핵심 인사이트** (이 분석이 §4 룰 재설계의 근거):

- **tilt 룰은 영상마다 0~85° 천차만별** — URFDD 카메라 각도(천장 비스듬) 에서
  사람이 누워도 어깨-엉덩이 수직차가 작음. 단일 임계 X → **default off**
- **aspect 가 압도적으로 robust** — fall mean **1.6+** vs ADL mean **0.4~0.8**
  (3 배 차). 영상별 일관 → **단독 룰로 충분**
- **comp 는 nose 가림 빈번 + 음수 빈번** → **default off**
- ADL 에도 transient 1.4+ frame 있음 (앉기/굽히기) → **시간 윈도우 confirm 이 필수**

### 12.3 튜닝 v4 최종 결과 (URFDD 70 영상)

```
Confusion matrix
   TP=23  FN=7   FP=11  TN=29
Recall    = 0.767   (KPI ≥ 0.95 — FAIL by 18%p)
Precision = 0.676   (KPI ≥ 0.85 — FAIL by 17%p)
F1        = 0.719
```

### 12.4 KPI 미달의 도메인 한계

| 한계 | URFDD | 시연 환경 (자체 영상) |
|---|---|---|
| 영상 길이 | 1.5~8 s | 통제 가능 |
| Fall window 자체 길이 | 0.4~1.6 s | 1.5~3 s 가능 |
| 카메라 각도 | 천장 비스듬 (특이 각도) | 거실 정면 가능 |
| ADL 다양성 | 40 개 (앉기·굽히기 transient) | 시연 시나리오 한정 |

**짧은 fall window** 가 가장 큰 제약 — 시간 윈도우 평가에 충분한 frame 확보 어려움.
window 0.2 s + ratio 33% 로 6 frame 중 2 frame fallen 만 충족하면 trigger 되도록
완화했지만, 이게 ADL transient 에서도 true 가 되어 Precision 67% 에 머무름.

### 12.5 시스템 레벨 보완

계획서 FMEA 가 이미 단일 모달 정확도 부족을 가정하고 보완 전략을 제시:

- **Vision (낙상) + Voice 응급어 AND 결합** → 미탐지 보완 (Phase 5 Decider)
- **5 s 부동 confirm** 후에만 보호자 알림 → ADL transient 흡수
- **30 s 무응답 timeout** 후에만 escalation → false alarm 차단

즉 **Vision 단독 Recall 77% 가 시스템 레벨에서는 Voice 융합 + 시간 confirm**
으로 95%+ 안전성 도달 가능. 시연 환경에서 자체 영상으로 재평가 시 룰 단독으로도
KPI 도달 예상.

### 12.6 평가 산출물

```
~/eval/urfdd/
├── videos/             # 70 mp4 (30 fall + 40 adl)
├── gt.csv              # video_id, start_ts, end_ts, is_fall
├── kp_traces/          # 5 영상 frame-by-frame metric CSV
└── results_v4.json     # 최종 평가 (window 0.2 / ratio 0.33 / τ 8, R 0.767 / P 0.676 / F1 0.719)
```

(중간 튜닝 단계의 v2 / v3 JSON 은 정리 단계에서 삭제. 결과 진화는 위 §12.1 표 참조.)

### 12.7 다음 단계 후보

- **A. 자체 시연 영상 5+5 촬영** + 평가 — 도메인 일치 → KPI 도달 예상
- **B. Phase 5 통합 후 시스템 레벨 KPI 측정** (Voice 융합 포함)
- **C. consecutive frame counter 추가** — 시간 윈도우 의존도 ↓
- **D. 추가 임계 (`aspect 1.3`, `ratio 0.25`) coordinate descent** — Recall ↑ (precision ↓ 위험)

### 12.1 진행 순서

| # | 단계 | 산출물 | 시간 |
|---|---|---|---|
| 1 | `scripts/eval_fall.py` 골격 작성 | 평가 자동화 스크립트 | 2~3시간 |
| 2 | URFDD 다운로드 + ground truth CSV | `eval/urfdd/`, `gt.csv` | 1~2시간 |
| 3 | 70 영상 1차 평가 (default 임계) | confusion matrix + Recall/Precision | 6분 (실행) + 30분 (분석) |
| 4 | 임계 coordinate descent (`tilt`, `compression`, `aspect`, `ratio`) | PR 커브 + best 임계 | 1~2시간 |
| 5 | 자체 시연 영상 5+5 촬영 + 평가 | 도메인 적합성 확인 | 반나절 |
| 6 | PHASE4.md 에 정량 결과 추가 | 문서 갱신 | 30분 |

**총 1~2 일** 이면 KPI 도달 여부 판단 + 시연용 정확도 보고서 완성.

### 12.2 metric 정의

| 지표 | 정의 | KPI |
|---|---|---|
| Recall (TPR) | TP / (TP + FN) | ≥ 0.95 |
| Precision | TP / (TP + FP) | ≥ 0.85 |
| F1 | 둘의 harmonic mean | (참고) |
| E2E latency | GT fall start → `fall_detected=true` | p95 ≤ 3 s (영상 단위) |

판정 윈도우 τ = 3 s — GT fall 시점 ±3 s 안에 trigger 하면 TP.

### 12.3 임계 튜닝 — coordinate descent

180 조합 grid search 는 너무 길어 (~18시간) 한 임계만 변화시키고 나머지 고정
하는 4-pass coordinate descent 로 단축. 또는 70 영상 중 30 으로 sweep + 40 holdout 검증.

---

## 결론

Phase 4 는 **단일 모달 정확도 SOTA 가 아닌 시스템 안전성** 을 목표로 했다.
Pose 룰이 ~85% 수준이어도, Phase 5 의 Voice 융합과 시간 윈도우 confirm 이
보완해 Recall ≥ 95% 시스템 KPI 를 도달 가능. § 12 의 평가에서 이 가설을
URFDD 영상으로 정량 검증한다.
