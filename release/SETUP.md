# 마음돌봄 Vision — 팀원 셋업 가이드

ROS 2 Jazzy + EXAONE-3.5 + RAG(Chroma) 기반 어르신 돌봄 HRI 시스템.
이 문서는 **새 컴퓨터에서 맨 처음부터** 동일 환경을 구성하기 위한 단계별 안내입니다.

---

## 0. 요구 사양

| 항목 | 최소 | 권장 |
|---|---|---|
| OS | Windows 11 + WSL2 Ubuntu 24.04 **또는** Ubuntu 24.04 네이티브 | 좌동 |
| GPU | NVIDIA GTX 1650 Ti 4 GB VRAM | RTX 3060 이상 |
| RAM | 16 GB | 32 GB |
| Disk | 25 GB 여유 | 50 GB |
| 드라이버 | NVIDIA 550 이상 (WSL 인식 가능) | 최신 |
| 오디오 | USB 마이크 + 스피커 | 좌동 |

본 안내의 기본 가정:
- **WSL2 Ubuntu 24.04** 위에서 돌립니다. 네이티브 Ubuntu 도 거의 동일합니다.
- 홈 디렉터리 아래 `~/마음돌봄/` 으로 통일합니다 (스크립트 경로에 박혀 있음).
- Python 은 **3.12** (ROS 2 Jazzy 동봉 버전) 를 사용합니다.

---

## 1. Windows 호스트 셋업 (WSL 사용자만)

### 1.1 NVIDIA 드라이버 (Windows 측)

WSL2 에서 CUDA 를 쓰려면 **Windows 호스트에** NVIDIA 드라이버만 설치하면 됩니다. WSL 안에서는 별도 설치 금지 (충돌 발생).

1. <https://www.nvidia.com/Download/index.aspx> 에서 본인 GPU 에 맞는 최신 Game Ready 또는 Studio 드라이버 다운로드·설치.
2. PowerShell 에서 확인:
   ```powershell
   nvidia-smi
   ```

### 1.2 WSL2 Ubuntu 24.04 설치

PowerShell (관리자 권한):
```powershell
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
wsl --update
```
최초 기동 후 사용자 계정/비번 설정.

### 1.3 WSL 에서 GPU 확인

WSL Ubuntu 쉘에서:
```bash
nvidia-smi
```
GPU 가 보이면 OK. 안 보이면 Windows 드라이버·WSL 업데이트 확인.

### 1.4 오디오 (WSLg)

최신 WSL 은 **WSLg** 로 마이크/스피커가 자동 연결됩니다. 확인:
```bash
pactl info | head -5      # PulseAudio 서버 있음 확인
```
안 나오면 `sudo apt install -y pulseaudio` 후 `wsl --shutdown` 으로 WSL 재시작.

### 1.5 (권장) Windows PATH 누수 차단

WSL 안에서 명령 실행 시 Windows 의 `PATH` 가 딸려 들어와 한글 경로가 깨지는 일이 있습니다. `/etc/wsl.conf` 에 다음을 추가:
```ini
[interop]
appendWindowsPath=false
```
저장 후 `wsl --shutdown` → 재진입.

---

## 2. Ubuntu 공통 셋업

아래부터는 **WSL/네이티브 모두 동일**.

### 2.1 시스템 패키지

```bash
sudo apt update
sudo apt install -y \
    build-essential cmake git curl wget pkg-config \
    python3-pip python3-venv python3-dev \
    libasound2-dev portaudio19-dev libsndfile1 ffmpeg \
    libasound2-plugins pulseaudio pulseaudio-utils \
    libssl-dev libffi-dev
```

### 2.2 ROS 2 Jazzy 설치

공식 지침(<https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html>) 요약:

```bash
# locale
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

# ROS 2 apt repo
sudo apt install -y software-properties-common
sudo add-apt-repository -y universe
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

sudo apt update
sudo apt install -y ros-jazzy-ros-base python3-colcon-common-extensions \
    python3-rosdep python3-vcstool

# rosdep
sudo rosdep init || true
rosdep update
```

셸에서 자동 로딩:
```bash
echo 'source /opt/ros/jazzy/setup.bash' >> ~/.bashrc
source ~/.bashrc
```

### 2.3 오디오 장치 확인

```bash
# 재생(스피커) 장치
aplay -l
# 녹음(마이크) 장치
arecord -l
# Python sounddevice 쪽 인덱스 확인
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```
원하는 마이크/스피커의 인덱스를 메모 (나중에 `config/hri_params.yaml` 의 `input_device`, `output_device` 에 반영).

---

## 3. CUDA (선택 — llama.cpp GPU 오프로드)

**WSL 사용자**: Windows 드라이버가 이미 CUDA 런타임을 제공합니다. WSL 안에 별도 CUDA toolkit 을 설치할 필요 없이 `nvidia-smi` 만 확인되면 llama.cpp 빌드 시 CUDA 가 자동 감지됩니다.

**네이티브 Ubuntu**:
```bash
# CUDA 12.8 (또는 호환 버전) 설치 — NVIDIA 공식 runfile/deb 참조
# https://developer.nvidia.com/cuda-downloads
```
다음을 `~/.bashrc` 에 추가:
```bash
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
```
`nvcc --version` 으로 확인.

---

## 4. Python 가상환경

ROS 2 Jazzy 의 Python 3.12 위에 venv 를 만듭니다. 스크립트는 `~/마음돌봄/.venv-ros` 경로를 가정하므로 **그대로** 따라주세요.

```bash
mkdir -p ~/마음돌봄
cd ~/마음돌봄
python3 -m venv --system-site-packages .venv-ros
# --system-site-packages : rclpy 등 apt 로 깔린 ROS 패키지가 보이도록
source .venv-ros/bin/activate
pip install --upgrade pip setuptools wheel
```

Python 의존성은 **7단계(tar 해제 후)** 에 설치합니다.

---

## 5. llama.cpp 빌드 (CUDA 활성화)

EXAONE GGUF 추론 서버.

```bash
cd ~
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF
cmake --build build --config Release -j$(nproc)

# 확인
./build/bin/llama-server --version
./build/bin/llama-server --help 2>&1 | head -5
```

빌드 에러:
- `nvcc: not found` → CUDA toolkit 설치 필요
- 메모리 부족 → `-j4` 로 병렬 수 줄이기
- CUDA 버전 불일치 → llama.cpp README 참조

---

## 6. 모델 다운로드

### 6.1 EXAONE-3.5-7.8B-Instruct GGUF (필수)

LG AI Research 의 한국어 네이티브 7.8B 모델. Q4_K_M 약 **4.7 GB**.
Xavier 32 GB 환경 대상. 4 GB GPU 환경에서는 `SAFE_MODE=1` (CPU-only) 사용.

```bash
source ~/마음돌봄/.venv-ros/bin/activate
pip install huggingface_hub
mkdir -p ~/models
huggingface-cli download \
    bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \
    EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf \
    --local-dir ~/models
```

> 개발 단계에서는 EXAONE-3.5-2.4B (1.6 GB) 로 검증했으나 운영 모델은 7.8B 로 통일.
> 2.4B 가 필요하면 `MODEL_PROFILE=custom MODEL=~/models/<2.4B path>` 로 직접 지정.

### 6.2 (선택) Qwen2.5-3B-Instruct GGUF — 폴백용

EXAONE 에 문제 있을 때 쓸 수 있는 대체 모델. 약 2 GB.
```bash
huggingface-cli download \
    Qwen/Qwen2.5-3B-Instruct-GGUF \
    qwen2.5-3b-instruct-q4_k_m.gguf \
    --local-dir ~/models --local-dir-use-symlinks False
```

### 6.3 RAG 임베딩 모델 (자동)

`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (약 120 MB) 는 `rag.py` / 빌더 최초 실행 시 HuggingFace 에서 자동 캐시됩니다. 네트워크가 막힌 환경이라면 다른 노드에서 `~/.cache/huggingface/` 를 복사해 배치하세요.

---

## 7. 프로젝트 tar 패키지 설치

팀장이 배포한 `mind_care_vision_v*.tar.gz` 를 받는다.

```bash
cd ~/마음돌봄
tar -xzvf /경로/mind_care_vision_v1.tar.gz
# → ~/마음돌봄/mind_care_vision/     (소스)
# → ~/마음돌봄/med_data/chroma_db/   (프리빌드 RAG 인덱스, 약 96 MB)
```

### 7.1 Python 의존성 설치

```bash
source ~/마음돌봄/.venv-ros/bin/activate
pip install -r ~/마음돌봄/mind_care_vision/requirements.txt
```

주요 패키지 (pinned): langchain 1.2.x / langchain-chroma 1.1 / chromadb 1.5 / sentence-transformers 5.4 / torch 2.11 (CPU) / transformers 5.6 / faster-whisper 1.2 / sounddevice / edge-tts / webrtcvad.

> **torch** 는 CPU 전용으로 고정돼 있습니다. GPU 추론은 llama.cpp 가 담당하므로 venv 에는 CPU 토치로 충분합니다. GPU torch 가 꼭 필요하면:
> ```bash
> pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu121
> ```

### 7.2 스크립트 실행 권한

tar 압축·해제 중 유닉스 실행 비트가 빠질 수 있습니다:

```bash
chmod +x ~/마음돌봄/mind_care_vision/scripts/*.sh
```

---

## 8. ROS 2 워크스페이스 빌드

ROS 패키지이므로 `colcon build` 로 빌드·인스톨해야 노드가 등록됩니다.

```bash
mkdir -p ~/ros2_jazzy_ws/src
ln -s ~/마음돌봄/mind_care_vision ~/ros2_jazzy_ws/src/mind_care_vision
cd ~/ros2_jazzy_ws

source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
echo 'source ~/ros2_jazzy_ws/install/setup.bash' >> ~/.bashrc
source ~/.bashrc
```

`ros2 pkg list | grep mind_care` 로 등록 확인.

---

## 9. 실행

### 9.1 최초 점검

```bash
~/마음돌봄/mind_care_vision/scripts/healthcheck.sh
```
다음을 확인해 줍니다: ROS 존재, Python venv, GGUF 파일, GPU, 포트 8080, chroma_db 존재.

### 9.2 전체 HRI 스택 기동

```bash
~/마음돌봄/mind_care_vision/scripts/start_hri.sh
```
1. llama-server (EXAONE) 가 `127.0.0.1:8080` 에 뜹니다.
2. `/health` 엔드포인트 대기.
3. `ros2 launch mind_care_vision hri_system.launch.py` 로 audio / dialogue / tts 노드 실행.

마이크에 대고 _"안녕하세요, 요즘 잠이 잘 안 와요"_ 같이 말해 보세요. 스피커에서 EXAONE 의 한국어 응답이 재생됩니다.

### 9.3 LLM 프로파일 전환

```bash
MODEL_PROFILE=qwen   ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh
MODEL_PROFILE=exaone ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh
```
기본은 `exaone`. NGL(GPU offload layers) 기본값은 프로파일별로 자동 설정됩니다.

### 9.4 파라미터 튜닝

`config/hri_params.yaml`:
- `audio_bridge_node.input_device` : -1(기본) 말고 특정 마이크 지정 가능
- `tts_node.edge_voice` : InJoon(남) / SunHi(여) / Hyunsu(남, 다국어)
- `llm_dialogue_node.rag_enabled` : RAG on/off
- `llm_dialogue_node.rag_top_k` / `rag_min_score` : 검색 품질 튜닝

수정 후 ROS 노드만 재기동하면 반영됩니다.

---

## 10. RAG 인덱스 재빌드 (선택)

tar 에 **이미 빌드된 chroma_db** 가 들어 있습니다(med_blog 14,614 + med_disease 3,936 docs, 약 96 MB). 데이터를 갱신하고 싶을 때만:

### 10.1 원본 데이터 구조

```
~/마음돌봄/med_data/
├── *.jsonl               # 블로그 15 파일 (angina_pectoris_..., brain_..., etc)
├── *.json                # 질환 백과 1,300여 파일 (한글 질환명.json)
└── chroma_db/            # 출력 (영속 저장)
```
**원본 데이터(*.jsonl / *.json)는 tar 에 포함되지 않습니다** — 용량이 너무 커서. 인덱스만 복구하고 싶으면 chroma_db 만 재사용하면 되고, 재빌드가 필요하면 데이터 담당에게 원본을 요청하세요.

### 10.2 빌드 명령

```bash
source ~/마음돌봄/.venv-ros/bin/activate
cd ~/마음돌봄/mind_care_vision

# 블로그 인덱스 (약 10분)
RESET=1 python tools/build_chroma_index.py

# 질환 백과 인덱스 (약 3-4분)
RESET=1 python tools/build_chroma_disease.py
```
`RESET=1` 은 기존 컬렉션 삭제 후 재빌드.

---

## 11. 트러블슈팅

| 증상 | 원인 / 대처 |
|---|---|
| `llama-server: command not found` | llama.cpp 빌드 재확인 (→ 5단계). 스크립트는 `~/llama.cpp/build/bin/llama-server` 를 가정. |
| llama-server `curl: command not found` | 빌드 시 `-DLLAMA_CURL=OFF` 꼭 넣을 것. |
| VRAM OOM | `scripts/start_llama_server.sh` 의 `NGL` 환경변수 낮추기. EXAONE 4GB GPU 기준 20 권장. |
| 음성 안 나옴 (Edge TTS) | 인터넷 필요. 오프라인이면 `tts_backend: "melo"` 로 전환 (추가 설치 필요). |
| 마이크 무반응 | `arecord -l` 인덱스 확인 → `input_device` 수정. WSL 사용자는 Windows 마이크 권한 확인. |
| faster-whisper `cudnn` 에러 | `device: "cpu"`, `compute_type: "int8"` 로 고정 (이미 기본값). GPU 쓰고 싶다면 호스트에 cuDNN 설치 필요. |
| ALSA `underrun` (TTS 끊김) | `tts_node.py` 의 `latency="high"` 가 이미 반영. 그래도 끊기면 blocksize 를 `0.06s` 로 올리기. |
| `sub-process PATH leak`(`/mnt/c/...`) | 1.5 단계 `wsl.conf` 에 `appendWindowsPath=false` 설정. |
| "먹었으셨군" 같은 어색 경어 | EXAONE 사용 중인지 확인 (`healthcheck.sh` 로그). Qwen 이면 자동으로 지침 프롬프트가 길어짐. |
| chroma 컬렉션 비어 있음 | tar 추출 위치 확인 (`~/마음돌봄/med_data/chroma_db/`). 아니면 10단계로 재빌드. |
| ROS 노드 `package not found` | `source ~/ros2_jazzy_ws/install/setup.bash` 매 셸에서 실행했는지 확인. |

---

## 12. 빠른 시작 요약 (설치 다 된 사람용)

```bash
# 1) venv 활성화 + ROS source
source ~/마음돌봄/.venv-ros/bin/activate
source ~/ros2_jazzy_ws/install/setup.bash

# 2) 헬스체크
~/마음돌봄/mind_care_vision/scripts/healthcheck.sh

# 3) 기동
~/마음돌봄/mind_care_vision/scripts/start_hri.sh

# 4) 종료
pkill -9 -f llama-server
# ROS 노드는 Ctrl-C
```

---

## 13. 연락

- 프로젝트: 엔비디아 AI Academy 포테토 팀 — 마음돌봄 Vision
- 모델: EXAONE-3.5-7.8B-Instruct (LG AI Research, Xavier 운영)
- 데이터: 아산병원 질환 백과 + 건강 블로그 (광고성 행 필터링 적용)
