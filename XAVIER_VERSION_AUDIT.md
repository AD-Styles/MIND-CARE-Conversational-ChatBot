# Xavier 이전 — 버전 호환성 감사

> 작성: 2026-05-08, WSL 환경 기준
> 대상 환경: Jetson AGX Xavier 32 GB + JetPack 6.x

---

## 1. 핵심 매트릭스 (붉은색 = 큰 차이, 노란색 = 약한 차이)

| 컴포넌트 | WSL (현재) | Xavier (JetPack 6) | 위험도 | 액션 |
|---|---|---|---|---|
| **OS** | Ubuntu 24.04 | Ubuntu 22.04 | 🔴 **major** | 네이티브 바이너리 모두 재빌드 |
| **Python** | 3.12.3 | **3.10.x** | 🔴 major | venv 새로 생성, 모든 wheel 재설치 |
| **ROS 2** | **Jazzy** | **Humble** | 🟡 minor | 코드 변경 거의 없음 (rclpy API 동일) |
| **CUDA** | 13.x (driver 591.74) | 12.2 | 🔴 major | TRT engine, .so, llama.cpp 재빌드 |
| **TensorRT** | 10.6 | 10.x (JP6.1+) or 8.6 (JP6.0) | 🔴 major | engine 호환 안 됨 — `trtexec` 다시 |
| **cuDNN** | 9.x | 8.9 | 🟡 | torch wheel 의존, NVIDIA wheel 사용 |
| **DeepStream** | 8.0 | 7.0/7.1 | 🟡 minor | nvinfer config 형식 동일, pyds 재설치 |
| **gcc** | 13.3 | 11.x | 🟡 | C++ 표준 라이브러리 ABI — 재빌드 필요 |
| **numpy** | 1.26.4 | 1.26.x | ✅ ABI OK | `--system-site-packages` 로 system numpy 노출 |
| **torch** | 2.11.0+cpu | **2.3-2.5 (NVIDIA wheel)** | 🔴 | PyPI 절대 X — NVIDIA Jetson wheel 만 |
| **transformers** | 5.6.0 | 4.45+ 권장 | 🟡 | 5.x 도 동작하지만 4.45 가 안정 |
| **chromadb** | 1.5.8 | 1.5.x aarch64 wheel 있음 | ✅ | OK |
| **faster-whisper** | 1.2.1 | 1.x + ctranslate2 4.4+ | 🟡 | ctranslate2 aarch64 wheel 있음 |
| **resemblyzer** | 0.1.4 | 동일 | ✅ | numba/coverage 패치 재적용 필요 |
| **sounddevice** | 0.5.5 | 동일 | ✅ | portaudio19-dev 시스템 패키지만 필요 |

---

## 2. 가장 큰 위험 4가지

### 🔴 R1. CUDA 13 → 12 — **TensorRT engine, llama.cpp, .so 모두 재빌드**

**현재 WSL 산출물 (재사용 불가)**:
- `release/vision/.../engines/yolov8n-face-nms.engine`
- `release/vision/.../engines/yolov8n-pose-nms.engine`
- `release/vision/.../so/libnms_parser.so` (x86_64, sm_75)
- `~/llama.cpp/build/bin/llama-server`

**Xavier 측 재빌드**:
```bash
# TRT engine
/usr/src/tensorrt/bin/trtexec \
    --onnx=yolov8n-face-nms.onnx \
    --saveEngine=yolov8n-face-nms.jetson.engine \
    --fp16 --memPoolSize=workspace:2048

# NMS parser .so
cd release/vision/mind_care_perception/src/parser_yolov8_face/
make clean && make CUDA_VER=12.2

# llama.cpp
cd ~/llama.cpp && make clean && make GGML_CUDA=1 -j$(nproc)
```

### 🔴 R2. Python 3.12 → 3.10 — venv 전체 재생성

**문법 검증 (3.10 호환 OK)**:
- ✅ `tuple[bool, float]` 타입 힌트 (3.9+)
- ✅ `dict[str, ...]` (3.9+)
- ✅ match-case (3.10+)
- ✅ `from __future__ import annotations`
- ❌ **사용 안 함**: PEP 695 type params, `@override`, PEP 701 f-string parser

→ **코드 변경 없음** ✨, venv 만 새로 만들면 됨.

```bash
# Xavier 에서
sudo apt install python3.10-venv python3.10-dev
python3.10 -m venv ~/마음돌봄/.venv-ros --system-site-packages
source ~/마음돌봄/.venv-ros/bin/activate
pip install --upgrade pip wheel
# torch 는 NVIDIA wheel 별도 설치 (아래 R4 참조)
pip install -r requirements.xavier.txt
```

### 🔴 R3. ROS Jazzy → Humble — 우리가 쓰는 API 가 호환되는지

우리 코드의 `rclpy` import 사용 점검:

| import | Jazzy | Humble | 호환 |
|---|---|---|---|
| `rclpy.init / spin / shutdown` | ✅ | ✅ | OK |
| `rclpy.node.Node` | ✅ | ✅ | OK |
| `rclpy.executors.ExternalShutdownException` | ✅ | ✅ (Iron+) | OK |
| `rclpy.qos.QoSProfile` | ✅ | ✅ | OK |
| `std_msgs.msg.String` | ✅ | ✅ | OK |
| `from launch import LaunchDescription` | ✅ | ✅ | OK |
| `Node.declare_parameter / get_parameter` | ✅ | ✅ | OK |

→ **코드 변경 없음**. `package.xml` 만 `<buildtool_depend>` / `<exec_depend>` 가 humble 동일.

### 🔴 R4. PyTorch — NVIDIA Jetson wheel 만 사용

**절대 하지 말 것**:
```bash
pip install torch                    # ❌ 일반 wheel — CPU 만, CUDA 미지원
pip install torch --index-url ...cuda  # ❌ x86_64 CUDA wheel — Xavier 에서 import 시 실패
```

**올바른 방법**:
```bash
# JetPack 6.0 (CUDA 12.2)
wget https://developer.download.nvidia.com/compute/redist/jp/v60/pytorch/torch-2.3.0-cp310-cp310-linux_aarch64.whl
pip install ./torch-2.3.0-cp310-cp310-linux_aarch64.whl

# torchvision: source build (~30분)
git clone --branch v0.18.0 https://github.com/pytorch/vision torchvision
cd torchvision && python setup.py install --user

# 또는 jetson-ai-lab.io 미러
pip install torchvision --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu122/+simple/
```

---

## 3. 약한 위험 (검증만 필요)

### 🟡 Y1. transformers 5.x → 4.45 권장

5.x 는 cutting edge. Xavier 에서도 동작하지만 의존성 충돌 가능. 4.45 로 다운그레이드 권장:

```bash
pip install "transformers>=4.45,<4.50"
```

### 🟡 Y2. langchain 1.x → 0.3 권장

같은 이유. 0.3 가 가장 안정적이고 chromadb/huggingface 통합 검증됨.

### 🟡 Y3. ctranslate2 4.4+ — CUDA 12 호환

`faster-whisper` 가 의존. 4.4 미만은 CUDA 11 만 지원.

```bash
pip install "ctranslate2>=4.4"
```

### 🟡 Y4. sentence-transformers 5 → 3.x 권장

```bash
pip install "sentence-transformers>=3.0,<3.5"
```

---

## 4. 시스템 패키지 (apt)

```bash
# JetPack 6 base + 추가
sudo apt update
sudo apt install -y \
    python3.10-venv python3.10-dev \
    portaudio19-dev libasound2-plugins alsa-utils \
    ffmpeg sqlite3 \
    build-essential cmake git pkg-config \
    libsndfile1 libsndfile1-dev \
    ros-humble-desktop \
    ros-humble-rmw-fastrtps-cpp \
    ros-humble-cv-bridge \
    ros-humble-image-transport
```

---

## 5. 검증 체크리스트 (Xavier 첫 부팅 후)

```bash
# 1. JetPack 버전
cat /etc/nv_tegra_release         # R36 (JetPack 6) 확인
nvcc --version                    # release 12.2

# 2. ROS Humble
source /opt/ros/humble/setup.bash
ros2 doctor

# 3. Python venv
python3.10 -m venv ~/마음돌봄/.venv-ros --system-site-packages
source ~/마음돌봄/.venv-ros/bin/activate
python --version                  # 3.10.x

# 4. NVIDIA torch wheel
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# → "2.3.0 True Xavier" 같은 출력

# 5. 모든 패키지 + numba 패치
pip install -r ~/마음돌봄/requirements.xavier.txt
python -c "import resemblyzer, librosa"   # numba 충돌 확인
# 필요시:
cat > ~/마음돌봄/.venv-ros/lib/python3.10/site-packages/numba/misc/coverage_support.py << EOF
def get_registered_loc_notify(): return []
EOF

# 6. ROS rclpy 동작
python -c "import rclpy; rclpy.init(); rclpy.shutdown()"

# 7. 프로젝트 빌드
mkdir -p ~/ros2_ws/src
ln -s ~/마음돌봄/mind_care_vision ~/ros2_ws/src/
ln -s ~/마음돌봄/release/emergency/mind_care_emergency ~/ros2_ws/src/
ln -s ~/마음돌봄/release/emergency/mind_care_api ~/ros2_ws/src/
ln -s ~/마음돌봄/release/vision/mind_care_perception ~/ros2_ws/src/
cd ~/ros2_ws && colcon build --symlink-install

# 8. healthcheck + smoke
bash ~/마음돌봄/mind_care_vision/scripts/healthcheck.sh
NGL=33 bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh &
bash ~/마음돌봄/mind_care_vision/scripts/smoke_e2e.sh
```

---

## 6. 우선순위 시간 견적 (Xavier 인수 후)

| 단계 | 시간 |
|---|---|
| JetPack 플래시 | 2-3 h |
| ROS 2 Humble + 시스템 패키지 | 30 min |
| Python venv + NVIDIA torch wheel | 30 min |
| `pip install -r requirements.xavier.txt` | 30 min |
| TRT engine 재빌드 (face + pose) | 30 min |
| `.so` (NMS parser) 재빌드 | 10 min |
| llama.cpp aarch64 빌드 | 30 min |
| chromadb 재빌드 (bge-m3 1024d) | 15 min |
| **화자 enroll (운영 마이크)** | **2 min** |
| smoke + healthcheck 검증 | 30 min |
| **합계** | **~6-7 시간** |

---

## 7. 가장 위험한 한 가지

> **PyTorch 를 일반 pip install 로 깔면 모든 게 무너집니다.**
>
> torch 와 chromadb 의 sentence-transformers 를 거쳐 모두 의존하고,
> CUDA 미지원 wheel 이 깔리면 RAG 임베딩 추론이 CPU 로 폴백 → 매우 느림.
> 또한 다른 패키지가 torch 2.x 의존을 풀면서 PyPI wheel 을 끌어와 덮어씀.
>
> **방어**:
> 1. NVIDIA torch wheel 먼저 설치
> 2. `requirements.xavier.txt` 의 torch 줄이 주석 처리되어 있는지 확인
> 3. 매 pip install 후 `python -c "import torch; assert torch.cuda.is_available()"` 검증
