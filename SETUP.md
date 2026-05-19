# 설치 가이드 (SETUP)

마음돌봄 시스템을 **NVIDIA Jetson AGX Xavier**에 설치·구동하기 위한 안내입니다.
플래시부터 자동시작까지의 상세 절차는 [`XAVIER_INSTALL_GUIDE.md`](XAVIER_INSTALL_GUIDE.md)를,
구 개발환경(WSL/x86)은 [`release/SETUP.md`](release/SETUP.md)를 참고하세요.

---

## 0. 요구 사양

| 항목 | 사양 |
|---|---|
| 메인 보드 | NVIDIA Jetson AGX Xavier 32 GB |
| OS / SDK | JetPack 5.x (Ubuntu 20.04, Python 3.8, CUDA 11.4) |
| ROS | ROS 2 Foxy |
| 저장공간 | 30 GB 이상 여유 (모델·인덱스 포함) |
| 주변장치 | USB 카메라, USB 마이크, 스피커, 5 V 액티브 부저 |
| 네트워크 | 이더넷 권장 (모델 다운로드 약 5 GB) |

> ⚠️ **주의 사항**
> - **PyTorch는 일반 `pip install` 금지** — Jetson 전용 wheel만 사용 (JetPack 버전에 맞춰).
> - **`numpy`는 2.x 금지** — rclpy ABI가 깨집니다. venv를 `--system-site-packages`로 만들어
>   시스템 numpy(1.x)를 노출시킵니다.
> - **TensorRT 엔진(`.engine`)은 x86에서 빌드한 것을 재사용 불가** — Jetson에서 `trtexec`로
>   직접 빌드해야 합니다.

---

## 1. 시스템 패키지

```bash
sudo apt update
sudo apt install -y \
    build-essential cmake git curl wget pkg-config \
    python3-pip python3-venv python3-dev \
    libasound2-dev portaudio19-dev libsndfile1 ffmpeg \
    libssl-dev libffi-dev
```

ROS 2 Foxy는 JetPack 5.x(Ubuntu 20.04)에 맞춰 설치합니다
(공식 문서: <https://docs.ros.org/en/foxy/Installation.html>).

```bash
source /opt/ros/foxy/setup.bash
```

---

## 2. 소스 배치

저장소를 홈 디렉터리 아래 `~/마음돌봄/`으로 배치합니다 (스크립트 경로가 이를 가정).

```bash
git clone <REPO_URL> ~/마음돌봄
cd ~/마음돌봄
```

---

## 3. Python 가상환경

ROS의 시스템 패키지(rclpy, numpy)가 보이도록 `--system-site-packages`로 생성합니다.

```bash
cd ~/마음돌봄
python3 -m venv --system-site-packages .venv-ros
source .venv-ros/bin/activate
pip install --upgrade pip setuptools wheel
```

### 3.1 PyTorch (Jetson 전용 wheel)

JetPack 버전에 맞는 NVIDIA Jetson PyTorch wheel을 받아 **수동 설치**합니다.
(NVIDIA Jetson Zoo / 포럼의 JetPack 버전별 wheel URL 참조 —
일반 PyPI `torch`는 CUDA가 맞지 않아 동작하지 않습니다.)

### 3.2 나머지 의존성

```bash
pip install -r requirements.xavier.txt
```

핵심 패키지: `faster-whisper`(STT), `chromadb`·`langchain-chroma`·`sentence-transformers`(RAG),
`edge-tts`(TTS), `fastapi`·`uvicorn`(API), `firebase-admin`·`twilio`(알림), `Jetson.GPIO`(부저).

---

## 4. llama.cpp 빌드 (CUDA)

EXAONE GGUF 모델을 추론할 llama.cpp 서버를 CUDA 활성화하여 빌드합니다.

```bash
cd ~
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF
cmake --build build --config Release -j$(nproc)
```

---

## 5. 모델 다운로드

### 5.1 언어모델 (필수)

EXAONE-3.5-7.8B-Instruct GGUF를 `~/models`에 받습니다.

```bash
pip install huggingface_hub
mkdir -p ~/models
huggingface-cli download bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \
    EXAONE-3.5-7.8B-Instruct-Q3_K_M.gguf --local-dir ~/models
```

- `Q3_K_M` : 속도 우선 (기본). `Q4_K_M` : 품질 우선.
- (선택) 폴백 모델 `Qwen2.5-3B-Instruct-GGUF`도 동일하게 받을 수 있습니다.

### 5.2 비전 모델 (필수)

`YOLOv8n-pose` / `YOLOv8n-face` / 표정 분류 모델의 ONNX 파일을 배치한 뒤,
**Jetson에서 직접** TensorRT FP16 엔진으로 변환합니다 (`trtexec`).
상세 절차는 `release/vision/PHASE2.md`, `PHASE4.md` 참조.

### 5.3 RAG 임베딩 모델 (자동)

`BAAI/bge-m3`는 최초 실행 시 HuggingFace에서 자동으로 캐시됩니다
(`~/.cache/huggingface/`). 오프라인 환경이면 캐시를 미리 복사해 두세요.

---

## 6. RAG 인덱스 구축

> ⚠️ 의료 코퍼스(`med_data/`)는 저작권 문제로 저장소에 포함되지 않습니다.
> 서울아산병원 질환백과 등 출처의 이용약관을 준수하여 직접 수집해야 합니다.

의료 코퍼스를 `med_data/` 아래에 배치한 뒤 ChromaDB 인덱스를 빌드합니다.
인덱스는 런타임 임베딩 모델(`BAAI/bge-m3`)과 **동일한 모델**로 빌드해야 합니다.

```bash
source ~/마음돌봄/.venv-ros/bin/activate
cd ~/마음돌봄/mind_care_vision
export EMBED_MODEL=BAAI/bge-m3
RESET=1 python tools/build_chroma_disease.py   # 질환백과 → med_disease 컬렉션
RESET=1 python tools/build_chroma_index.py     # 블로그 자료 → med_blog 컬렉션
```

생성된 인덱스는 `med_data/chroma_db/`에 저장되며 `med_disease`·`med_blog` 컬렉션을
포함합니다. 임베딩 모델을 바꾸면 벡터 차원이 달라지므로 **반드시 재빌드**하세요.

---

## 7. ROS 2 워크스페이스 빌드

```bash
mkdir -p ~/ros2_ws/src
ln -s ~/마음돌봄/mind_care_vision              ~/ros2_ws/src/mind_care_vision
ln -s ~/마음돌봄/release/vision/mind_care_perception   ~/ros2_ws/src/mind_care_perception
ln -s ~/마음돌봄/release/emergency/mind_care_emergency ~/ros2_ws/src/mind_care_emergency
ln -s ~/마음돌봄/release/emergency/mind_care_api       ~/ros2_ws/src/mind_care_api

cd ~/ros2_ws
source /opt/ros/foxy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

`ros2 pkg list | grep mind_care`로 4개 패키지 등록을 확인합니다.

> 자동화: `mind_care_vision/scripts/xavier_bootstrap.sh`가 위 절차 상당 부분을
> 한 번에 수행합니다.

---

## 8. 설정

ROS 파라미터는 `mind_care_vision/config/hri_params.xavier.yaml`에서 조정합니다.

| 파라미터 | 설명 |
|---|---|
| `audio_bridge_node.model_size` | STT 모델 크기 (`small` 기본) |
| `audio_bridge_node.input_device` | 마이크 장치 인덱스 (`arecord -l`로 확인) |
| `tts_node.tts_backend` | TTS 백엔드 (`edge` 기본, 인터넷 필요) |
| `llm_dialogue_node.rag_enabled` | RAG 사용 여부 |
| `llm_dialogue_node.rag_embed_model` | RAG 임베딩 모델 (`BAAI/bge-m3`) |
| `alert_dispatcher_node.buzzer_gpio_pin` | 부저 GPIO 핀 (BOARD 7) |

알림 채널(FCM/SMS)을 쓰려면 `firebase-credentials.json`, Twilio 자격증명을 별도로
배치합니다 (저장소에 포함되지 않음 — `.gitignore` 처리).

보호자 Flutter 앱(`urgent_alarm_app/`)을 빌드할 경우, 본인 Firebase 프로젝트의
`google-services.json`을 `urgent_alarm_app/android/app/`에 직접 배치해야 합니다
(보안상 저장소에 포함되지 않음).

---

## 9. 실행

```bash
source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/foxy/setup.bash
source ~/ros2_ws/install/setup.bash

# 점검
bash ~/마음돌봄/mind_care_vision/scripts/healthcheck.sh

# LLM 서버 기동
bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh

# HRI 시스템 기동 (음성·대화·응급·API)
ros2 launch mind_care_vision hri_system.launch.py
```

보호자 웹 대시보드: `http://<Xavier-IP>:8000/demo/`

---

## 10. 트러블슈팅

| 증상 | 대처 |
|---|---|
| `torch` import 실패 / CUDA 불일치 | Jetson 전용 wheel로 재설치 (PyPI torch 금지) |
| `rclpy` ABI 에러 | numpy가 2.x인지 확인 → 1.x로 다운그레이드, venv는 `--system-site-packages` |
| TensorRT 엔진 로드 실패 | x86에서 빌드한 `.engine` → Jetson에서 `trtexec`로 재빌드 |
| `llama-server` 미발견 | llama.cpp 빌드 경로 확인 (`~/llama.cpp/build/bin/`) |
| VRAM 부족 | `start_llama_server.sh`의 `NGL` 낮추기 또는 `SAFE_MODE=1` |
| TTS 음성 없음 | edge-tts는 인터넷 필요 — 네트워크 확인 |
| RAG 컬렉션 비어 있음 | `med_data/chroma_db` 경로·빌드 여부 확인 (6단계) |

추가 진단은 `mind_care_vision/scripts/healthcheck.sh`,
`mind_care_vision/scripts/smoke_e2e.sh`를 활용하세요.
