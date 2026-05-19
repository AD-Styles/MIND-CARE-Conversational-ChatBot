# Jetson AGX Xavier 32GB — 인수 → 운영 가이드

> 처음 인수받은 Jetson AGX Xavier 에서 마음돌봄 Vision 을 처음부터 끝까지
> 셋업해서 시연 가능한 상태로 만드는 step-by-step 매뉴얼.
>
> **전제**: 이전 코드/데이터 없음 — 빈 Jetson AGX Xavier 32 GB 보드 하나.
> **목표 환경**: JetPack 6.x (Ubuntu 22.04 + CUDA 12.2 + TensorRT 10.x + DeepStream 7.x)
> **총 소요 시간**: ~6-7 시간 (JetPack 플래시 2-3h 별도)

---

## 📋 목차

0. [사전 준비물](#0-사전-준비물)
1. [JetPack 6 플래시 (Host PC)](#1-jetpack-6-플래시-host-pc)
2. [Xavier 첫 부팅 + 기본 설정](#2-xavier-첫-부팅--기본-설정)
3. [시스템 패키지 설치](#3-시스템-패키지-설치)
4. [ROS 2 Humble 설치](#4-ros-2-humble-설치)
5. [Python 환경 + NVIDIA torch wheel](#5-python-환경--nvidia-torch-wheel)
6. [프로젝트 clone + bootstrap](#6-프로젝트-clone--bootstrap)
7. [모델 다운로드](#7-모델-다운로드)
8. [네이티브 바이너리 빌드](#8-네이티브-바이너리-빌드)
9. [RAG 인덱스 구축](#9-rag-인덱스-구축)
10. [화자 등록 (enroll)](#10-화자-등록-enroll)
11. [부저 GPIO 셋업](#11-부저-gpio-셋업)
12. [통합 검증](#12-통합-검증)
13. [systemd 자동 시작](#13-systemd-자동-시작)
14. [트러블슈팅](#14-트러블슈팅)
15. [완료 체크리스트](#15-완료-체크리스트)

---

## 0. 사전 준비물

### 0.1 하드웨어
- [ ] **Jetson AGX Xavier 32 GB Developer Kit** (전원 어댑터 + USB-C 케이블 동봉)
- [ ] **Host PC** — Ubuntu 20.04 / 22.04 (SDK Manager 실행용, **macOS/Windows 직접 안 됨**)
- [ ] **USB-C ↔ USB-A 케이블** (Xavier 와 Host PC 연결, 플래시용)
- [ ] **NVMe SSD 1 TB** (선택 — 모델 + 데이터 저장, 권장)
- [ ] **USB 카메라** (UVC 호환, 720p 30fps 이상)
- [ ] **USB 마이크** (가능하면 ReSpeaker 4-Mic array)
- [ ] **3.5 mm 스피커 또는 USB 스피커**
- [ ] **GPIO 부저** (5V active buzzer, 점퍼선)
- [ ] **HDMI 케이블 + 모니터 + USB 키보드/마우스** (첫 부팅 GUI 설정용)
- [ ] **인터넷 (이더넷 권장)** — 모델 다운로드 30+ GB

### 0.2 계정·정보
- [ ] **NVIDIA Developer 계정** (SDK Manager 로그인용)
- [ ] **HuggingFace 토큰** (선택 — rate limit 회피)
- [ ] **마음돌봄 git repo URL** (인수받을 코드)

### 0.3 PC 측 도구
```bash
# Host Ubuntu PC 에서
sudo apt install -y openssh-client rsync
```

---

## 1. JetPack 6 플래시 (Host PC)

**소요 시간**: 2-3 시간 (네트워크 속도에 따라)

### 1.1 NVIDIA SDK Manager 설치 (Host PC)
1. https://developer.nvidia.com/sdk-manager 에서 SDK Manager `.deb` 다운로드
2. ```bash
   sudo apt install ./sdkmanager_*.deb
   ```

### 1.2 Xavier 를 Recovery Mode 로 진입
1. Xavier 전원 OFF
2. 전원 어댑터 + USB-C 케이블 (Host PC 와) 연결
3. **REC (Recovery) 버튼** 누른 채 **POWER 버튼** 짧게 누름
4. REC 버튼 1초 더 유지 후 떼기
5. Host PC 에서 `lsusb` 로 `0955:7019 NVidia Corp.` 확인

### 1.3 SDK Manager 실행 + JetPack 6.x 선택
```bash
sdkmanager &
```
- NVIDIA Developer 계정 로그인
- **Target Hardware**: Jetson AGX Xavier 32GB
- **JetPack Version**: 6.0 또는 6.1 (CUDA 12.2 + TensorRT 10.x)
- **Components**:
  - [x] Jetson Linux (L4T R36)
  - [x] CUDA Toolkit
  - [x] cuDNN
  - [x] TensorRT
  - [x] DeepStream SDK 7.x
  - [x] VPI (optional)
  - [ ] Computer Vision (필요 시)

### 1.4 플래시 진행
- "Continue" → SSH 비밀번호 입력 (Xavier 에 설정될 사용자명/암호)
  - 권장: 사용자명 `eslee03` (WSL 과 통일)
- 약 1-2시간 대기 — 진행률 90%에서 SSH 응답 대기 시 패닉 X

### 1.5 완료 확인
- Xavier 가 자동 재부팅
- 모니터에 Ubuntu 22.04 GNOME 데스크탑 표시되면 성공

---

## 2. Xavier 첫 부팅 + 기본 설정

**소요 시간**: 15분

### 2.1 첫 GUI 부팅
모니터 화면 따라:
- 언어: English (or Korean — 후자는 가끔 패키지 누락)
- 키보드: US 또는 KR
- 사용자: `eslee03` (1.4 에서 설정한 것과 동일)
- Wi-Fi 연결 (또는 이더넷 자동 인식)

### 2.2 SSH 활성화 (필수 — 이후 GUI 없이 SSH로 작업)
```bash
sudo apt update
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
ip addr show | grep inet         # IP 주소 확인
```

Host PC 에서:
```bash
ssh-copy-id eslee03@<xavier-ip>
ssh eslee03@<xavier-ip>          # 비밀번호 없이 접속 확인
```

### 2.3 시스템 시간 동기화
```bash
sudo timedatectl set-timezone Asia/Seoul
sudo systemctl restart systemd-timesyncd
date
```

### 2.4 NVMe SSD 마운트 (선택)
```bash
sudo fdisk -l                    # SSD 디바이스 확인 (예: /dev/nvme0n1)
sudo mkfs.ext4 /dev/nvme0n1p1    # 파티션 포맷 (주의: 데이터 날아감)
sudo mkdir /mnt/data
sudo mount /dev/nvme0n1p1 /mnt/data
echo '/dev/nvme0n1p1 /mnt/data ext4 defaults 0 2' | sudo tee -a /etc/fstab
sudo chown -R eslee03:eslee03 /mnt/data
```

→ 이후 `~/마음돌봄` 을 `/mnt/data/마음돌봄` 으로 symlink 권장.

### 2.5 nvpmodel — 최대 성능 모드
```bash
sudo nvpmodel -m 0               # MAXN — 30W, 모든 코어 풀 클럭
sudo jetson_clocks               # 클럭 고정
nvpmodel -q                      # 확인
```

---

## 3. 시스템 패키지 설치

**소요 시간**: 20분

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    python3.10 python3.10-venv python3.10-dev python3-pip \
    portaudio19-dev libasound2-plugins alsa-utils \
    ffmpeg sqlite3 \
    build-essential cmake git pkg-config \
    libsndfile1 libsndfile1-dev \
    curl wget htop tree v4l-utils \
    software-properties-common
```

**검증**:
```bash
python3.10 --version             # Python 3.10.12
gcc --version                    # gcc 11.x
nvcc --version                   # CUDA 12.2
ls /usr/src/tensorrt/bin/        # trtexec 있어야 함
```

---

## 4. ROS 2 Humble 설치

**소요 시간**: 30분

```bash
# 1. ROS 2 GPG 키 + apt source
sudo add-apt-repository universe -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu jammy main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list

# 2. 설치
sudo apt update
sudo apt install -y \
    ros-humble-desktop \
    ros-humble-rmw-fastrtps-cpp \
    ros-humble-cv-bridge \
    ros-humble-image-transport \
    python3-colcon-common-extensions \
    python3-rosdep

# 3. rosdep 초기화
sudo rosdep init
rosdep update

# 4. 자동 source (편의)
echo 'source /opt/ros/humble/setup.bash' >> ~/.bashrc
source ~/.bashrc
```

**검증**:
```bash
ros2 --version                   # 0.32.x 등
ros2 doctor                      # 모든 ✔ 떠야 함
ros2 topic list                  # /parameter_events, /rosout 만 나오면 OK
```

---

## 5. Python 환경 + NVIDIA torch wheel

**소요 시간**: 30분
**⚠️ 가장 위험한 단계** — torch 를 일반 pip 로 깔면 모든 ML 의존성이 깨집니다.

### 5.1 venv 생성

```bash
mkdir -p ~/마음돌봄
python3.10 -m venv ~/마음돌봄/.venv-ros --system-site-packages
source ~/마음돌봄/.venv-ros/bin/activate
pip install --upgrade pip wheel
```

### 5.2 NVIDIA torch wheel 다운로드 (CRITICAL)

**❌ 절대 하지 말 것**:
```bash
pip install torch                # 이렇게 하면 CPU only 깔림
pip install torch --index-url https://download.pytorch.org/whl/cu121   # 이것도 X (x86_64)
```

**✅ 올바른 방법** — JetPack 6.0 (CUDA 12.2):
```bash
cd /tmp
wget https://developer.download.nvidia.com/compute/redist/jp/v60/pytorch/torch-2.3.0-cp310-cp310-linux_aarch64.whl
pip install ./torch-2.3.0-cp310-cp310-linux_aarch64.whl
```

**✅ JetPack 6.1+ (CUDA 12.6)** 면:
```bash
pip install --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu126/+simple/ torch torchvision torchaudio
```

### 5.3 torchvision / torchaudio (source 또는 jetson-ai-lab)

```bash
pip install --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu122/+simple/ \
    torchvision torchaudio
```

### 5.4 검증 (절대 건너뛰지 말 것)

```bash
python -c "
import torch
print(f'torch          : {torch.__version__}')
print(f'cuda available : {torch.cuda.is_available()}')
print(f'device count   : {torch.cuda.device_count()}')
print(f'device name    : {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')
"
```

**기대 출력**:
```
torch          : 2.3.0
cuda available : True
device count   : 1
device name    : Xavier
```

> `cuda available : False` 가 뜨면 즉시 멈추고 §14 트러블슈팅 §14.1 참조.

---

## 6. 프로젝트 clone + bootstrap

**소요 시간**: 10분

### 6.1 repo clone

```bash
cd ~
git clone <REPO_URL> 마음돌봄    # 또는 rsync 로 WSL 에서 복사
cd 마음돌봄
git log --oneline | head         # v0.1-wsl-stable 태그 확인
```

### 6.2 numba 패치 (resemblyzer 의존성 충돌 우회)

```bash
mkdir -p ~/마음돌봄/.venv-ros/lib/python3.10/site-packages/numba/misc/
# (numba 자체는 아직 안 깔렸지만 디렉터리만 미리)
```

### 6.3 자동 셋업 스크립트 실행

```bash
bash mind_care_vision/scripts/xavier_bootstrap.sh
```

스크립트가 수행:
- apt 시스템 패키지 (이미 §3 에서 깔았으면 skip)
- ROS Humble 확인
- venv 검증
- `pip install -r requirements.xavier.txt`
- numba `coverage_support.py` stub 패치
- ros2_ws 심볼릭 링크 + colcon build
- healthcheck.sh 실행

**완료 후 안내되는 8 단계** 중 1, 4(부저), 5b(7.8B 다운로드) 는 우리가 별도로 처리.

### 6.4 검증

```bash
python -c "
import resemblyzer, scipy, sounddevice, webrtcvad, faster_whisper
import chromadb, langchain_chroma, sentence_transformers
import fastapi, pydantic, sqlalchemy
print('[OK] all core imports succeeded')
"
```

---

## 7. 모델 다운로드

**소요 시간**: 60분 (LLM 4.7 GB + bge-m3 2.3 GB + 기타)

### 7.1 EXAONE-3.5-7.8B GGUF (LLM, 4.7 GB)

```bash
source ~/마음돌봄/.venv-ros/bin/activate
mkdir -p ~/models && cd ~/models

# HuggingFace 토큰 있으면 환경변수 설정 (rate limit 회피)
# export HF_TOKEN=hf_xxx

huggingface-cli download \
    bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \
    EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf \
    --local-dir .

ls -lh EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf   # 4.7G 확인
```

### 7.2 bge-m3 임베딩 모델 (2.3 GB, 자동 캐시)

빌드 시점에 자동 다운로드되므로 미리 받지 않아도 됨. 미리 받고 싶으면:
```bash
python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='BAAI/bge-m3', repo_type='model')
"
```

### 7.3 WSL 에서 RAG 인덱스 + 모델 가중치 rsync (옵션)

이미 만들어진 산출물이 WSL 에 있다면 그대로 가져옵니다:

```bash
# Host PC 또는 다른 머신에서 (WSL 측이 source)
WSL_HOST=wsl-host-ip
rsync -avz --progress \
    /home/eslee03/마음돌봄/med_data/ \
    eslee03@xavier-ip:~/마음돌봄/med_data/

rsync -avz --progress \
    /home/eslee03/마음돌봄/release/vision/models/ \
    eslee03@xavier-ip:~/마음돌봄/release/vision/models/

rsync -avz --progress \
    /home/eslee03/.cache/huggingface/hub/models--BAAI--bge-m3/ \
    eslee03@xavier-ip:~/.cache/huggingface/hub/models--BAAI--bge-m3/
```

→ `release/vision/models/` 의 `.pt` (Mini-Xception) 와 `.onnx` (YOLO) 는 그대로 사용.
→ `.engine` 파일은 x86_64 빌드라 호환 안 됨 — **§8.1 에서 재빌드 필수**.

---

## 8. 네이티브 바이너리 빌드

**소요 시간**: 60분

### 8.1 TensorRT engine 재빌드 (YOLOv8n-face / pose)

```bash
cd ~/마음돌봄/release/vision/models/face_detector
/usr/src/tensorrt/bin/trtexec \
    --onnx=yolov8n_face.onnx \
    --saveEngine=yolov8n_face.engine \
    --fp16 \
    --memPoolSize=workspace:2048

cd ../pose_estimator
/usr/src/tensorrt/bin/trtexec \
    --onnx=yolov8n_pose.onnx \
    --saveEngine=yolov8n_pose.engine \
    --fp16 \
    --memPoolSize=workspace:2048

cd ../emotion_classifier
/usr/src/tensorrt/bin/trtexec \
    --onnx=mini_xception.onnx \
    --saveEngine=mini_xception.engine \
    --fp16

# 검증
ls -lh ../*/*.engine
```

각각 ~30 MB, 빌드 시간 ~5-10분.

### 8.2 NMS parser .so 재빌드 (C++)

```bash
cd ~/마음돌봄/release/vision/mind_care_perception/src/parser_yolov8_face
make clean
make CUDA_VER=12.2

cd ../parser_yolov8_pose
make clean
make CUDA_VER=12.2

# 결과 .so 를 model 디렉터리에 복사
cp ../parser_yolov8_face/libnvdsinfer_custom_impl_yolov8_face.so \
   ~/마음돌봄/release/vision/models/face_detector/
cp ../parser_yolov8_pose/libnvdsinfer_custom_impl_yolov8_pose.so \
   ~/마음돌봄/release/vision/models/pose_estimator/
```

### 8.3 llama.cpp aarch64 (CUDA)

```bash
cd ~
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
make clean
make GGML_CUDA=1 LLAMA_CUDA_F16=1 -j$(nproc)

# 검증
./build/bin/llama-server --help | head -5
```

빌드 ~30분, 출력 `build/bin/llama-server`.

### 8.4 DeepStream pyds 심볼릭 링크

```bash
# JetPack 6 에 자동 설치되어 있음. venv 에서 import 가능하도록 link
ln -sf /opt/nvidia/deepstream/deepstream/lib/pyds.so \
    ~/마음돌봄/.venv-ros/lib/python3.10/site-packages/pyds.so

# 검증
python -c "import pyds; print(pyds.__file__)"
```

### 8.5 ctranslate2 (faster-whisper 의존, CUDA 12 호환)

```bash
pip install "ctranslate2>=4.4"   # aarch64 wheel PyPI 에 있음
python -c "
from faster_whisper import WhisperModel
m = WhisperModel('small', device='cuda', compute_type='float16')
print('[OK] faster-whisper CUDA load')
"
```

---

## 9. RAG 인덱스 구축

**소요 시간**: 30분 (Xavier CUDA 기준)

### 9.1 옵션 A — WSL 의 MiniLM 인덱스 그대로 사용

7.1.3 에서 rsync 했다면 이미 `~/마음돌봄/med_data/chroma_db/` 에 있음.

```bash
ls ~/마음돌봄/med_data/chroma_db/        # _build_stats.json 등 있어야 함
cat ~/마음돌봄/med_data/chroma_db/_build_stats.json
# → "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
```

`hri_params.xavier.yaml` 의 `rag_embed_model` 도 MiniLM 그대로 → **별도 작업 0**.

### 9.2 옵션 B — bge-m3 1024d 로 재빌드 (권장)

```bash
# 1. 코드 5 곳 모델 ID 교체
grep -rn "Xavier 이전 후" ~/마음돌봄/mind_care_vision/
# 출력된 파일들에서 MiniLM 줄을 "BAAI/bge-m3" 로 교체
# 또는 sed 일괄:
find ~/마음돌봄/mind_care_vision -name "*.py" -o -name "*.yaml" | \
    xargs sed -i 's|sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2|BAAI/bge-m3|g'

# 2. 기존 chroma_db 백업 후 재빌드
mv ~/마음돌봄/med_data/chroma_db ~/마음돌봄/med_data/chroma_db.minilm.bak

source ~/마음돌봄/.venv-ros/bin/activate
RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_disease.py
RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_index.py
```

Xavier CUDA 에서 ~30분 (WSL CPU 1시간 대비 빠름).

### 9.3 검증

```bash
python << 'EOF'
import sys
sys.path.insert(0, '/home/eslee03/마음돌봄/mind_care_vision')
from mind_care_vision.rag import RagRetriever
r = RagRetriever(index_dir='/home/eslee03/마음돌봄/med_data/chroma_db')
hits = r.retrieve('무릎이 자주 아파요', k=3)
for i, h in enumerate(hits, 1):
    print(f"  [{i}] coll={h['collection']} score={h['score']:.3f} | {h['text'][:60]}")
EOF
```

bge-m3 면 score 가 0.5-0.7 대, 의료 hit 가 top-3 안에 들어오면 OK.

---

## 10. 화자 등록 (enroll)

**소요 시간**: 5분

### 10.1 마이크 인식 확인

```bash
python -m sounddevice            # 입력 디바이스 목록
arecord -l                       # ALSA 로 본 카드
```

USB 마이크가 보여야 함. 안 보이면 USB 재연결 + dmesg 확인.

### 10.2 enroll 실행

```bash
source ~/마음돌봄/.venv-ros/bin/activate
python ~/마음돌봄/mind_care_vision/tools/enroll_speaker.py
# → "30초간 말씀해 주세요" 뜨면 자연스럽게 책 한 단락 읽기
```

저장 위치: `~/models/speaker.npy` (256 bytes).

### 10.3 검증

```bash
python -c "
import numpy as np
e = np.load('/home/eslee03/models/speaker.npy')
print(f'shape: {e.shape}, dtype: {e.dtype}, norm: {np.linalg.norm(e):.3f}')
"
# → shape: (256,) dtype: float32 norm: 1.0 근처
```

---

## 11. 부저 GPIO 셋업

**소요 시간**: 15분

### 11.1 Jetson.GPIO 설치 + 권한

```bash
pip install Jetson.GPIO

# GPIO 접근 권한 (sudo 없이)
sudo groupadd -f gpio
sudo usermod -aG gpio $USER
sudo udevadm control --reload-rules && sudo udevadm trigger
# 로그아웃-재로그인 후 적용
```

### 11.2 5V active buzzer 배선

```
Jetson AGX Xavier 40-pin header (J30)
  Pin 1 (3.3V)  ──┐
  Pin 7 (GPIO9) ──┴── Buzzer + ── Buzzer - ── Pin 6 (GND)
```

⚠️ 능동(active) 부저: +/- 전압 인가하면 자체 발진. 수동(passive) 부저는 PWM 필요.

### 11.3 buzzer_channel.py 구현 (현재 stub 상태)

`~/마음돌봄/release/emergency/mind_care_emergency/mind_care_emergency/channels/buzzer_channel.py`:

```python
"""buzzer_channel.py — Jetson GPIO 부저 채널."""
import time
import threading
import Jetson.GPIO as GPIO

BUZZER_PIN = 7  # BOARD 모드 핀 7 (BCM 4)


class BuzzerChannel:
    def __init__(self):
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
        self._lock = threading.Lock()

    def alert(self, duration_s: float = 3.0, pattern: str = "siren") -> bool:
        """응급 부저 발생. pattern: siren(0.5초씩 5회) | beep(0.1초씩 10회) | solid"""
        with self._lock:
            try:
                if pattern == "siren":
                    for _ in range(int(duration_s / 1.0)):
                        GPIO.output(BUZZER_PIN, GPIO.HIGH); time.sleep(0.5)
                        GPIO.output(BUZZER_PIN, GPIO.LOW);  time.sleep(0.5)
                elif pattern == "beep":
                    for _ in range(int(duration_s / 0.2)):
                        GPIO.output(BUZZER_PIN, GPIO.HIGH); time.sleep(0.1)
                        GPIO.output(BUZZER_PIN, GPIO.LOW);  time.sleep(0.1)
                else:  # solid
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                    time.sleep(duration_s)
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                return True
            except Exception as exc:
                print(f"[buzzer] failed: {exc}")
                return False

    def close(self):
        GPIO.cleanup(BUZZER_PIN)
```

### 11.4 테스트

```bash
python -c "
from mind_care_emergency.channels.buzzer_channel import BuzzerChannel
b = BuzzerChannel()
b.alert(2.0, 'siren')
b.close()
"
```

부저가 0.5초씩 4회 울리면 성공.

---

## 12. 통합 검증

**소요 시간**: 10분

### 12.1 healthcheck

```bash
bash ~/마음돌봄/mind_care_vision/scripts/healthcheck.sh
```

기대: 13 항목 모두 ✔ (speaker.npy / chroma_db / EXAONE-7.8B / numba 패치 포함).

### 12.2 llama-server 기동

```bash
bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh &
sleep 10
curl http://127.0.0.1:8080/health   # {"status":"ok"}
curl http://127.0.0.1:8080/v1/models | python -m json.tool
```

### 12.3 smoke_e2e (4 시나리오)

```bash
# Xavier override config 로 실행
SKIP_LLAMA=1 bash ~/마음돌봄/mind_care_vision/scripts/smoke_e2e.sh
```

기대: 4/4 시나리오 ✔ — 특히 시나리오 2 (SV 차단) 이 inconclusive 가 아니라 실제 차단 로그가 떠야 함 (운영 마이크로 enroll 했기 때문).

### 12.4 (선택) 본인 vs 가족 음성 실시간 SV 검증

```bash
# 전체 HRI 가동
ros2 launch mind_care_vision hri_system.launch.py \
    config_file:=$HOME/마음돌봄/mind_care_vision/config/hri_params.xavier.yaml &

# 본인 발화 → LLM 응답 받기
# 가족이 발화 → "[SV] 미등록 화자 — 응답 생략" 로그 확인

# 임계값 조정이 필요하면:
#   본인도 거부 → sv_threshold: 0.65
#   외부인 통과 → sv_threshold: 0.80
```

---

## 13. systemd 자동 시작

**소요 시간**: 10분

### 13.1 mindcare-llama.service

```bash
sudo tee /etc/systemd/system/mindcare-llama.service > /dev/null <<EOF
[Unit]
Description=Mind Care - llama-server (EXAONE-3.5-7.8B)
After=network-online.target

[Service]
Type=simple
User=eslee03
WorkingDirectory=/home/eslee03/마음돌봄
ExecStart=/bin/bash /home/eslee03/마음돌봄/mind_care_vision/scripts/start_llama_server.sh
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/mindcare-llama.log
StandardError=append:/var/log/mindcare-llama.log

[Install]
WantedBy=multi-user.target
EOF
```

### 13.2 mindcare-hri.service

```bash
sudo tee /etc/systemd/system/mindcare-hri.service > /dev/null <<EOF
[Unit]
Description=Mind Care - ROS HRI nodes
After=mindcare-llama.service
Requires=mindcare-llama.service

[Service]
Type=simple
User=eslee03
WorkingDirectory=/home/eslee03/마음돌봄
ExecStartPre=/bin/sleep 30
ExecStart=/bin/bash -c "source /opt/ros/humble/setup.bash && source /home/eslee03/ros2_ws/install/setup.bash && bash /home/eslee03/마음돌봄/mind_care_vision/scripts/start_hri.sh"
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/mindcare-hri.log

[Install]
WantedBy=multi-user.target
EOF
```

### 13.3 활성화

```bash
sudo mkdir -p /var/log
sudo touch /var/log/mindcare-llama.log /var/log/mindcare-hri.log
sudo chown eslee03:eslee03 /var/log/mindcare-*.log

sudo systemctl daemon-reload
sudo systemctl enable mindcare-llama mindcare-hri
sudo systemctl start mindcare-llama
sleep 30
sudo systemctl start mindcare-hri

# 상태 확인
systemctl status mindcare-llama
systemctl status mindcare-hri
tail -f /var/log/mindcare-hri.log
```

### 13.4 로그 로테이션

```bash
sudo tee /etc/logrotate.d/mindcare > /dev/null <<EOF
/var/log/mindcare-*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
}
EOF
```

---

## 14. 트러블슈팅

### 14.1 `torch.cuda.is_available() == False`
**원인**: PyPI wheel 이 깔림 (CPU only)
**해결**:
```bash
pip uninstall -y torch torchvision torchaudio
# §5.2 NVIDIA wheel 재설치
```

### 14.2 `nvinfer config 파싱 실패` (DeepStream)
**원인**: `.txt` config 의 인라인 `#` 주석
**해결**: 인라인 주석을 모두 별도 줄로 옮기기

### 14.3 `rclpy SIGSEGV`
**원인**: venv 의 numpy 가 2.x
**해결**:
```bash
pip uninstall -y numpy
# system numpy 1.x 가 venv 에 노출 (--system-site-packages)
```

### 14.4 `resemblyzer import 시 numba AttributeError`
**원인**: coverage_support stub 미적용
**해결**:
```bash
cat > ~/마음돌봄/.venv-ros/lib/python3.10/site-packages/numba/misc/coverage_support.py <<EOF
def get_registered_loc_notify():
    return []
EOF
```

### 14.5 `huggingface-cli download` 가 너무 느림
**원인**: 무인증 rate limit
**해결**:
```bash
export HF_TOKEN=hf_xxx                   # HuggingFace 토큰 발급
# 또는 HF_ENDPOINT=https://hf-mirror.com 미러 사용
```

### 14.6 chroma_db 차원 불일치 에러
**원인**: 임베딩 모델은 384d, 인덱스는 1024d (또는 반대)
**해결**: yaml 의 `rag_embed_model` 과 `_build_stats.json` 의 `"model"` 일치 확인 → 안 맞으면 재빌드

### 14.7 GPIO Permission denied
**원인**: gpio 그룹 미가입
**해결**: `sudo usermod -aG gpio $USER && newgrp gpio` (또는 로그아웃-재로그인)

### 14.8 `.engine` 파일 사용 시 `serialization error`
**원인**: x86_64 엔진을 aarch64 에서 로드 시도
**해결**: §8.1 trtexec 로 Xavier 에서 재빌드

### 14.9 ros2 topic echo 가 메시지 못 받음
**원인**: DDS RMW 미일치 또는 reliability QoS
**해결**:
```bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ros2 topic info /llm/responses
```

### 14.10 systemd 서비스가 부팅 직후 죽음
**원인**: llama-server 보다 HRI 가 먼저 떠서 connection refused
**해결**: §13.2 의 `ExecStartPre=/bin/sleep 30` 늘리거나 healthcheck 기반 시작 조건

---

## 15. 완료 체크리스트

각 단계 끝나면 체크:

- [ ] §1 JetPack 6 플래시 — Xavier GUI 부팅
- [ ] §2 SSH 접속 + nvpmodel MAXN
- [ ] §3 apt + python3.10 + trtexec 존재
- [ ] §4 ROS 2 Humble `ros2 doctor` 통과
- [ ] §5 `torch.cuda.is_available() == True`
- [ ] §6 `bash xavier_bootstrap.sh` 완료, healthcheck 의 ROS/venv ✔
- [ ] §7 EXAONE-7.8B GGUF 4.7 GB 다운로드 완료
- [ ] §8 TRT engine 3개 + .so 2개 + llama-server + pyds + ctranslate2 모두 OK
- [ ] §9 chroma_db 존재 + smoke retrieve 성공
- [ ] §10 `~/models/speaker.npy` 생성
- [ ] §11 부저 GPIO 테스트 → 실제 울림
- [ ] §12 healthcheck 13/13 ✔, smoke_e2e 4/4 ✔
- [ ] §13 systemd 자동 시작 동작, 재부팅 후 자동 기동 확인

---

## 16. 시연 시 명령 모음

### 가장 빠른 한 줄 (권장)

```bash
bash ~/마음돌봄/mind_care_vision/scripts/start_demo.sh
```

llama + HRI + API + Firefox `/demo/` 까지 한 번에. 이미 떠있는 컴포넌트는 자동 스킵.

```bash
bash ~/마음돌봄/mind_care_vision/scripts/start_demo.sh --no-browser   # 헤드리스 (노트북에서 LAN 접속)
bash ~/마음돌봄/mind_care_vision/scripts/start_demo.sh status         # 상태만
bash ~/마음돌봄/mind_care_vision/scripts/start_demo.sh stop           # API + HRI 정리
```

### 수동 기동 (start_demo.sh 안 쓸 때)

```bash
# llama → HRI → API 순서
bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh &
sleep 30
ros2 launch mind_care_vision hri_system.launch.py \
    config_file:=$HOME/마음돌봄/mind_care_vision/config/hri_params.xavier.yaml &
cd ~/마음돌봄/release/emergency/mind_care_api
MIND_CARE_DEV_OPEN=1 uvicorn mind_care_api.app:create_app \
    --factory --host 0.0.0.0 --port 8000
# 노트북 브라우저: http://<xavier-LAN-IP>:8000/demo/
```

### 모니터링

```bash
tail -f /var/log/mindcare-hri.log
tail -f /tmp/mindcare_api.log
ros2 topic echo /audio/transcripts
ros2 topic echo /llm/responses
ros2 topic echo /emergency/alert
ros2 topic echo /vision/state
```

### 시연 중 강제 트리거 (영상 없을 때 발표 백업)

```bash
# panic_word alert 강제 발행 (브라우저 /demo/ 에서 빨간 알람 + 사이렌 확인)
ros2 topic pub --once /emergency/alert std_msgs/msg/String \
  "{data: '{\"alert_id\":\"manual-1\",\"elder_id\":\"elder_01\",\"ts\":1700000000.0,\"type\":\"panic_word\",\"severity\":\"critical\",\"status\":\"raised\"}'}"

# fall 도 동일하게 type 만 바꿔서
```

### 전체 종료

```bash
bash ~/마음돌봄/mind_care_vision/scripts/start_demo.sh stop
sudo systemctl stop mindcare-hri mindcare-llama   # systemd 쓸 때
# 또는 강제
pkill -INT -f llama-server
pkill -INT -f "ros2 launch"
pkill -INT -f "uvicorn mind_care_api"
```

---

## 17. 백업 plan (시연 도중 Xavier 다운 시)

1. WSL 으로 즉시 전환 (사전에 LAN 동일 토픽 통신 가능하게 설정)
2. 또는 `MODEL_PROFILE=qwen` 으로 Qwen2.5-3B 폴백 (LLM 만 문제일 때)
3. 또는 `SAFE_MODE=1` 로 CPU 전용 (GPU 문제일 때)
4. 응급 알림은 부저로만 — 네트워크 끊겨도 작동

---

> **상태**: 이 가이드 끝까지 따르면 마음돌봄 Vision 이 Xavier 에서 24/7 운영 가능.
> **다음 일**: 시연 영상 5+5 + burn-in 24h + 발표 자료.
> **연락**: `결과보고서_초안.md` §8 부록 / `HANDOVER.md` 참조.

— 끝 —
