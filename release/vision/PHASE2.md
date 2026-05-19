# Phase 2 — 실 비전 파이프라인 (DeepStream 8.0)

`vision_emulator_node` (Phase 1) 와 동일한 `/vision/state` JSON 스키마를
**실제 카메라 → DeepStream → pyds probe** 경로로 채우는 단계.

```
[v4l2/file/test/ros image]
   → nvvideoconvert → nvstreammux
   → nvinfer  PGIE  : YOLOv8n-face (NMS-baked ONNX) + 커스텀 .so
   → nvtracker      : NvDCF
   → nvinfer  SGIE  : Mini-Xception emotion (1×48×48 GRAY → 7-class)
   → fakesink       (probe 가 메타데이터 추출 → VisionAggregator)
                    → ROS2 publisher /vision/state @ 0.5 Hz
```

## 1. 모델 출처와 라이선스

| 모델                  | 출처                                                                                               | 라이선스   | 비고                                          |
|---------------------|--------------------------------------------------------------------------------------------------|---------|---------------------------------------------|
| `yolov8n-face.onnx` | [akanametov/yolo-face 1.0.0](https://github.com/akanametov/yolo-face/releases/tag/1.0.0)        | AGPL-3.0 | NMS 가 ONNX 그래프에 내장됨. 출력 `[1, 300, 21]`        |
| `mini_xception.onnx` | 자체 정의 (`prepare_models.py` 안). FER2013 형 1×48×48 → 7-class.                                       | Apache-2.0 (코드) | **현재는 랜덤 가중치**. 실서비스용 가중치는 `.pth` 로 보강 필요   |

YOLOv8n-face 는 ultralytics 공식 weights 가 아니라 야생 fork 이므로,
`prepare_models.py` 가 GitHub release 의 **ONNX 를 직접** 받아온다.
(`.pt` + ultralytics export 폴백 경로도 같이 들어 있음.)

### 1.1 yolov8n-face ONNX 출력 21채널

```
[ 0..3 ]  x1, y1, x2, y2     (입력 좌표계 — 즉 0~640 픽셀)
[ 4   ]  face confidence
[ 5   ]  class id  (= 0)
[ 6..20 ]  5 keypoints × (x, y, visibility)   ← 본 단계에서는 무시
```

`[1, 300, 21]` 의 300 은 NMS 가 "최대 N개" 슬롯으로 패딩한 결과.
빈 슬롯은 모두 0 — 파서에서 `conf <= preThr` 로 걸러낸다.

## 2. 자산 위치

```
release/vision/
├── models/
│   ├── face_detector/
│   │   ├── yolov8n_face.onnx            (12.7 MB)
│   │   ├── yolov8n_face.engine          (FP16, ~9.6 MB)
│   │   └── libnvdsinfer_custom_impl_yolov8_face.so   ← 별도 빌드
│   └── emotion_classifier/
│       ├── mini_xception.onnx           (0.06 MB)
│       └── mini_xception.engine         (FP16, dynamic batch 1/8/16)
└── mind_care_perception/
    ├── config/                          ← nvinfer / tracker 설정
    ├── src/parser_yolov8_face/          ← .so 소스 + Makefile
    └── scripts/
        ├── prepare_models.py            ← ONNX 다운로드 + .engine 빌드
        ├── inspect_face_onnx.py         ← ONNX 출력 디버그용
        └── run_test_mode.sh             ← test 모드 파이프라인 검증
```

## 3. 한 번에 따라가는 빌드 절차

전제: DS 8.0 + TensorRT 10.x 설치, `~/마음돌봄/.venv-ros` venv,
ROS 2 Jazzy 워크스페이스 `~/ros2_ws/`.

```bash
# 0. venv + ROS source
source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash

# 1. ONNX 다운로드 + 엔진 빌드 (FP16)
cd ~/마음돌봄/release/vision/mind_care_perception/scripts
pip install ultralytics onnx onnxslim onnxruntime   # ONNX export 의존성 (한 번만)
python prepare_models.py             # ONNX + .engine 둘 다
# 부분 빌드:  python prepare_models.py --skip-trt   /  --trt-only  /  --face  /  --emotion

# 2. PGIE 커스텀 bbox 파서 .so 빌드
cd ~/마음돌봄/release/vision/mind_care_perception/src/parser_yolov8_face
make
# → release/vision/models/face_detector/libnvdsinfer_custom_impl_yolov8_face.so

# 3. ROS 2 워크스페이스에 등록 (심링크)
ln -sfn ~/마음돌봄/release/vision/mind_care_perception ~/ros2_ws/src/mind_care_perception
cd ~/ros2_ws
colcon build --symlink-install --packages-select mind_care_perception
source install/setup.bash
```

## 4. 동작 검증

### 4.1 test 모드 — videotestsrc

카메라/파일 없이 파이프라인이 살아있는지만 검증.

```bash
bash ~/마음돌봄/release/vision/mind_care_perception/scripts/run_test_mode.sh
```

기대 출력:

```
alive

=== /vision/state echo (1 msg) ===
data: '{"ts": ..., "presence": false, "face_id": null, ..., "emotion": "unknown", ...}'
```

`videotestsrc` 에는 얼굴이 없으므로 `presence=false` / `emotion="unknown"` 이 정상.

### 4.2 v4l2 모드 — USB 웹캠 직결

WSL 사용자는 `usbipd` 로 카메라를 WSL 에 attach 해야 한다 (별도 1회 작업).

```bash
ros2 launch mind_care_perception perception_camera.launch.py \
    source_mode:=v4l2 v4l2_device:=/dev/video0
```

확인:

```bash
ros2 topic echo /vision/state | head -n 3
ros2 topic hz   /vision/state          # ~2 Hz
```

### 4.3 file 모드 — mp4 회귀

```bash
ros2 launch mind_care_perception perception_camera.launch.py \
    source_mode:=file file_uri:=file:///path/to/sample.mp4
```

### 4.4 dialogue 노드와 결합 (Phase 1 패치 그대로 재사용)

```bash
# 터미널 1
ros2 launch mind_care_perception perception_camera.launch.py source_mode:=v4l2

# 터미널 2 — 기존 HRI 스택
~/마음돌봄/mind_care_vision/scripts/start_hri.sh
```

`mind_care_vision/config/hri_params.yaml` 에서 `vision_enabled: true` 만 켜면 된다.
토픽 스키마는 Phase 1 과 동일하므로 dialogue 측 코드 변경 없음.

## 5. 알려진 함정 (시간 잡아먹는 것들)

이번 단계에서 실제로 막혔다가 푼 항목들 — 새 PC 에서 또 마주칠 수 있음.

| 증상 | 원인 | 해결 |
|---|---|---|
| `vision_deepstream_node` 가 `rclpy.Node.__init__` 에서 SIGSEGV | venv 의 numpy 2.x 가 시스템 numpy 1.26 ABI(rclpy 빌드 기준) 와 충돌 | `pip uninstall numpy` (venv 안) — `--system-site-packages` 라 시스템 1.26 이 다시 노출됨 |
| 위와 같은 SIGSEGV 가 numpy 정리 후에도 남음 | `gi`/`pyds` module-level 초기화가 ROS DDS 시그널 마스크와 충돌 | `vision_deepstream_node.py` 가 GStreamer/pyds 를 `super().__init__()` 이후에 import 하도록 됨 (이미 반영) |
| `gst-error: Configuration file parsing failed ... key "net-scale-factor" ... cannot be interpreted` | GLib key-file 파서는 **인라인 `# 주석`** 을 못 읽음 | `pgie_yolov8n_face.txt` / `sgie_emotion.txt` 의 모든 `key=value  # comment` 를 별도 줄로 분리 |
| `trtexec: --workspace ... is no longer supported` | TRT 10.x | `--memPoolSize=workspace:N` 으로 (이미 `prepare_models.py` 에 반영) |
| `trtexec --explicitBatch` warning | TRT 10.x 에서 더 이상 필요 없음 | 그냥 빼면 됨 (이미 반영) |
| `ultralytics` import 시 `operator torchvision::nms does not exist` | `torch` 와 `torchvision` 버전 mismatch | `pip install --index-url https://download.pytorch.org/whl/cpu --force-reinstall torch==2.4.1 torchvision==0.19.1` 로 일관 페어 맞춤 |
| `yolov8n-face.pt` 다운로드 404 | 정확한 release tag 가 `1.0.0` (v 없음), repo 도 `yolo-face` (`yolov8-face` 아님) | `prepare_models.py` 의 URL 이미 갱신 |
| nvinfer config 안의 `../../models/...` 경로가 깨짐 | colcon `--symlink-install` 안 하면 share/ 로 deep-copy 되면서 상대경로 파괴 | colcon 호출 시 항상 `--symlink-install`. 혹은 launch 인자로 `pgie_config_file:=절대경로` 주입 |
| `vision_deepstream_node` 엔트리의 shebang 이 `/usr/bin/python3` 로 박힘 | venv 미활성 상태에서 colcon build | venv 활성화 후 `colcon build` 다시. 또는 `python -m mind_care_perception.vision_deepstream_node ...` 로 우회 |

## 6. 다음에 해야 할 일

- [ ] **mini_xception 가중치 학습/입수** — 현재는 랜덤. FER2013 + AffectNet 으로 fine-tune.
       `release/vision/models/emotion_classifier/fer_weights.pth` 에 두면 `prepare_models.py` 가 자동 로드.
- [ ] **face_id 매핑 (Phase 3)** — 현재는 `track_id != 0` 이면 무조건 `registered_name` 으로 매핑.
       SCRFD + ArcFace 임베딩으로 등록 DB 매칭 도입.
- [ ] **낙상 감지 (Phase 4)** — `fall_detected` 는 항상 `false` (placeholder).
       YOLOv8-pose SGIE 추가 또는 별도 브랜치 + 자세 각도/속도 룰.
- [ ] **부동 감지** — `nvdsanalytics` ROI + 체류시간으로 6시간 부동 감지 추가.
- [ ] **저조도 IR 보정** — FMEA 200 점, 야간 시나리오 대응.
