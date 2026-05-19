# 마음돌봄 Vision — 실행 가이드 (Phase 1 스켈레톤)

## 구성

```
Microphone ─► audio_bridge_node ─► /audio/transcripts (JSON)
                                        │
                                        ▼
                                   llm_dialogue_node ──HTTP──► llama.cpp server (:8080)
                                        │
                                        ▼
                                   /llm/responses (JSON)
                                        │
                                        ▼
                                     tts_node ─► Speaker
                                        │
                                        ▼
                                   /tts/status (JSON)
```

## 사전 준비

### 1) llama.cpp CUDA 빌드
```bash
cd ~/llama.cpp
export CUDACXX=/usr/local/cuda/bin/nvcc
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc
cmake --build build --config Release -j$(nproc)
```

### 2) GGUF 모델 다운로드
```bash
mkdir -p ~/models && cd ~/models
wget https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
```

### 3) TTS 설치 (택1)

**옵션 A: espeak-ng (가벼움, 저품질 한국어)**
```bash
sudo apt install -y espeak-ng
# 설정: config/hri_params.yaml → tts_backend: "espeak"
```

**옵션 B: Coqui TTS (무거움, 고품질 한국어)**
```bash
source ~/마음돌봄/.venv-ros/bin/activate
pip install torch TTS
# 설정: config/hri_params.yaml → tts_backend: "coqui"
# 최초 실행 시 모델 자동 다운로드
```

### 4) ROS 2 패키지 빌드
```bash
source /opt/ros/jazzy/setup.bash
cd ~/ros2_ws
colcon build --symlink-install --packages-select mind_care_vision
```

---

## ⚠️ 안전 실행 (Safe Start) — 4GB VRAM 랩탑 필독

**2026-04-23 사고 기록**: NGL=20 / ctx=4096 으로 llama-server를 띄운 상태에서
MeloTTS(`torch`, `transformers`) `pip install` 을 동시 진행 → 시스템이 강제 셧다운.
VRAM 3GB+RAM 스파이크+디스크 I/O가 겹쳐 랩탑 전원/열 보호 동작 추정.

### 규칙
1. **llama-server 실행 중에는 무거운 `pip install` / `cmake --build` / `docker build` 금지**
2. ROS 런치 전에 항상 **`bash scripts/healthcheck.sh`** 실행
3. VRAM 3GB 미만 여유 시 **`SAFE_MODE=1`** (CPU-only) 또는 **`NGL=10`**
4. llama-server 기동 직후 10초간은 모델 로딩이므로 ROS 런치 대기
5. 무거운 설치는 HRI 종료 후 전담하여 별도 진행

### 권장 프리셋 (4GB VRAM 랩탑)
```bash
# 기본: 안전 프로필
bash scripts/start_llama_server.sh         # NGL=14, ctx=2048

# 극도 안전: CPU-only
SAFE_MODE=1 bash scripts/start_llama_server.sh

# 다른 GPU 작업 병행 시
NGL=10 CTX=1536 bash scripts/start_llama_server.sh
```

### 프리플라이트 체크
```bash
bash scripts/healthcheck.sh
# ✔ VRAM 여유 충분  ✔ RAM 여유 충분  ✔ GGUF 존재
# 위 3개가 모두 OK일 때만 런치
```

### 원커맨드 단계 기동 (권장)
```bash
bash scripts/start_hri.sh
# = healthcheck → llama-server 백그라운드 + /health 대기 → ros2 launch
# 이미 llama 떠 있으면: SKIP_LLAMA=1 bash scripts/start_hri.sh
# CPU-only:            SAFE_MODE=1 bash scripts/start_hri.sh
```

---

## 실행

**Terminal A — llama.cpp 서버**
```bash
bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh
# CPU-only: SAFE_MODE=1 bash ...
# 가볍게:   NGL=10 bash ...
```

**Terminal B — HRI 런치**
```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
~/마음돌봄/.venv-ros/bin/python -m launch \
    ~/ros2_ws/install/mind_care_vision/share/mind_care_vision/launch/hri_system.launch.py
```

또는 개별 노드로:
```bash
ros2 run mind_care_vision audio_bridge_node --ros-args --params-file \
    ~/ros2_ws/install/mind_care_vision/share/mind_care_vision/config/hri_params.yaml
ros2 run mind_care_vision llm_dialogue_node --ros-args --params-file ...
ros2 run mind_care_vision tts_node --ros-args --params-file ...
```

**Terminal C — 모니터링**
```bash
ros2 topic echo /audio/transcripts
ros2 topic echo /llm/responses
ros2 topic echo /tts/status
```

---

## Phase 2 옵션 활성화 (향후)

`config/hri_params.yaml`에서:
```yaml
llm_dialogue_node:
  ros__parameters:
    sv_enabled: true          # 화자 검증 (pyannote/embedding)
    rag_enabled: true         # 의료 도메인 RAG
    guardrails_enabled: true  # NeMo Guardrails
```

각 기능은 별도 플러그인 노드 또는 `llm_dialogue_node` 내부 통합으로 확장 예정.

---

## Xavier 배포 (Phase 4)

`config/hri_params.xavier.yaml`을 만들고 런치 시 override:
- `device: "cuda"`, `compute_type: "float16"` (audio_bridge)
- `device: "cuda"` (tts)
- `NGL=33` (llama-server, 전체 GPU 오프로드)

모델·venv·llama.cpp는 aarch64 JetPack 6.x에서 재빌드 필요.
