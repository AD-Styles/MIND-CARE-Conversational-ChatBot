# Phase 4 — 낙상 감지 (Fall Detection)

계획서 핵심 KPI **Fall Recall ≥ 95% & Precision ≥ 85%, E2E ≤ 30 s, FMEA RPN=240 항목 완화** 를 위한 모듈.

## 1. 접근 (B 룰 기반)

선택지 비교 후 **YOLOv8-pose + 룰 + 시간 윈도우 + Voice 융합** 으로 결정.
이유는 [/* 후보 비교 *(채팅 정리)*/].

```
[Camera]
   → [DS PGIE: yolov8n-pose, NMS-baked]
   → [NvDCF tracker]
   → [pyds probe]
        ↓
   ┌────────────────────┐
   │ frame-level rule   │ ① shoulder–hip tilt ≥ 60°
   │   AND ≥ 2/3        │ ② head–hip y compression < 0.30 × bbox_h
   │                    │ ③ bbox aspect w/h > 1.4
   └────────┬───────────┘ (+) head y drop ≥ 25% / 0.3 s → 즉시 fallen
            ↓
   ┌────────────────────┐
   │ temporal confirm   │ "fall_event"     : 1 s 윈도우 fallen 비율 ≥ 50%
   │                    │ "fall_confirmed" : 추가 5 s 동안 IoU ≥ 0.85
   └────────┬───────────┘
            ↓
       /vision/fall_state @ 5 Hz
            ↓
   (다음 단계: emergency_decider — Voice 융합)
```

## 2. 모델 자산

| 항목 | 값 |
|---|---|
| 모델 | [`yolov8n-pose.pt`](https://docs.ultralytics.com/models/yolov8/) (ultralytics 공식) |
| 라이선스 | AGPL-3.0 |
| 입력 | `[1, 3, 640, 640]` RGB |
| 출력 | `[1, 300, 57]` NMS-baked: `[x1,y1,x2,y2,conf,cls,kpt0_x,kpt0_y,kpt0_v, …, kpt16_v]` |
| Engine | FP16 9.6 MB, GTX 1650 Ti 기준 ~1.5 ms/sample |

```
release/vision/models/pose_estimator/
├── yolov8n_pose.onnx       (13.6 MB)
├── yolov8n_pose.engine     (FP16, 9.6 MB)
└── libnvdsinfer_custom_impl_yolov8_pose.so   (16 KB, src/parser_yolov8_pose 에서 빌드)
```

## 3. 코드 구조

```
release/vision/mind_care_perception/
├── mind_care_perception/
│   ├── fall_rules.py             ← 룰 + FallStateMachine (numpy 만 의존)
│   ├── ds_pose_pipeline.py       ← DS GStreamer 파이프라인 (pose 단일 PGIE)
│   ├── ds_pose_metadata.py       ← pyds probe + PoseAggregator
│   └── fall_detection_node.py    ← ROS 2 노드, /vision/fall_state 발행
├── config/
│   ├── pgie_yolov8n_pose.txt     ← cluster-mode=4, output-tensor-meta=1
│   └── labels_pose.txt
├── src/parser_yolov8_pose/
│   ├── nvdsparsebbox_yolov8_pose.cpp
│   └── Makefile
├── launch/
│   └── fall_detection.launch.py
└── scripts/
    ├── inspect_pose_onnx.py      ← ONNX 출력 디버그
    └── run_fall_test.sh          ← test 모드 검증 스크립트
```

## 4. /vision/fall_state 토픽 스키마

`std_msgs/String` JSON @ 5 Hz:

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

`fall_detected=true` 시점에서 dialogue 노드가 "괜찮으세요?" 발화를 시작하고,
`fall_confirmed=true` 가 되면 emergency_decider 가 알림을 발송 (Phase 5).

## 5. 빌드 & 실행

전제: Phase 2 환경 (DS 8.0 + TRT 10 + venv-ros + ros2_ws). PHASE2.md 참조.

```bash
# 0. venv + ROS source
source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash

# 1. ONNX 다운로드 + engine 빌드 (pose 만)
cd ~/마음돌봄/release/vision/mind_care_perception/scripts
python prepare_models.py --pose             # ONNX + .engine

# 2. 커스텀 파서 .so
cd ~/마음돌봄/release/vision/mind_care_perception/src/parser_yolov8_pose
make

# 3. ROS 2 빌드 (Phase 2 와 같은 워크스페이스)
cd ~/ros2_ws
colcon build --symlink-install --packages-select mind_care_perception
source install/setup.bash
```

## 6. 검증

### 6.1 test 모드 (videotestsrc)

```bash
bash ~/마음돌봄/release/vision/mind_care_perception/scripts/run_fall_test.sh
```

기대:
```
alive
=== /vision/fall_state echo (1 msg) ===
data: '{"ts":..., "presence": false, "track_count": 0,
        "fall_detected": false, "fall_confirmed": false}'
=== /vision/fall_state hz over 4s ===
average rate: 5.001
```

### 6.2 v4l2 모드 (USB 웹캠)

```bash
ros2 launch mind_care_perception fall_detection.launch.py source_mode:=v4l2
```

자세 변화 시:
```bash
ros2 topic echo /vision/fall_state | head -n 30
```
- 서있을 때: `presence=true, fall_detected=false`
- 빠르게 누우면: `fall_detected=true` (그 후 부동 5s 시 `fall_confirmed=true`)

### 6.3 비디오 회귀 — Le2i Fall Dataset

```bash
ros2 launch mind_care_perception fall_detection.launch.py \
    source_mode:=file file_uri:=file:///path/to/le2i/Coffee_room_01/video1.avi
```

평가 절차 (Recall/Precision 측정) 는 `scripts/eval_le2i.py` (예정) 가 자동화.

## 7. 룰 임계 튜닝

`fall_detection.launch.py` 인자로 노출:

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `tilt_deg_thr` | 60.0 | 어깨–엉덩이 기울기 임계 |
| `compression_thr` | 0.30 | 머리-엉덩이 y 거리 / bbox 높이 비율 |
| `aspect_thr` | 1.4 | bbox 가로/세로 |
| `window_s` | 1.0 | frame-level fallen 비율 측정 윈도우 |
| `ratio_thr` | 0.5 | window 내 fallen 비율 임계 |
| `confirm_idle_s` | 5.0 | 부동 확인 시간 |

## 8. 다음 단계

- [ ] **Le2i / UR Fall 평가** — Recall/Precision 측정 → 임계 튜닝
- [ ] **자체 시뮬 영상** 5–10 개 (매트 + 체조복 안전 낙상) 추가
- [ ] **Phase 5 — Emergency Decider** : `fall_confirmed` + 음성 무응답 30 s → 보호자 알림 (FCM/Twilio)
- [ ] **Vision×Voice fusion** : 응급어 ("도와줘", "아파") 와 AND 결합 → false negative 보완

## 9. 알려진 제약

- `output-tensor-meta=1` 로 raw 텐서를 ROS 노드에서 직접 디코딩하므로, **DS pyds 빌드에 따라 `pyds.NvDsInferTensorMeta` 접근 방식이 달라질 수 있음** (`ds_pose_metadata.py::_extract_tensor_rows`). 본 코드는 ctypes 로 직접 메모리 읽기 — 빌드 차이를 회피하지만, 대신 메모리 레이아웃이 PGIE config 의 `infer-dims` 와 일치한다고 가정.
- 1650 Ti 4 GB 에서 **face PGIE + emotion SGIE + pose PGIE** 모두 동시 추론 시 ~1.2 GB GPU 사용. llama-server (~2.5 GB) 와 같이 띄우면 OOM 위험 — 시연 시 NGL 낮추기 또는 학습/시연 분리.
- 단일 어르신 가정. 두 명 이상 동시 트래킹 시 룰은 가장 큰 bbox 만 사용 (PoseAggregator 가 가장 큰 박스 = 주 대상으로 선택).
