# Jetson AGX Xavier 32 GB 이식 체크리스트

> 마지막 업데이트: 2026-05-06
> 대상: Jetson AGX Xavier 32 GB Developer Kit + JetPack 6.x (Ubuntu 22.04 LTS, CUDA 12.x, TensorRT 10.x)

---

## 0. 전제

WSL 측에서 다음이 끝났다고 가정한다:
- [x] Phase 2 (vision) / 4 (fall) / 5 (emergency) 코드 완성
- [x] Voice 통합 + 화자 검증 (resemblyzer)
- [x] RAG (bge-m3, 1024 dim) 인덱스
- [x] pytest 7/7 PASS
- [x] E2E smoke 시나리오 4 종 검증
- [ ] **자체 시연 영상 KPI 재측정** (Xavier 이전에 끝낼 것)
- [ ] **Burn-in 24 h 안정성 확인** (Xavier 이전에 끝낼 것)

---

## 1. 하드웨어 준비

| 항목 | 요구 | 비고 |
|---|---|---|
| Xavier AGX 32 GB | 1 대 | Developer Kit 권장 |
| MicroSD / NVMe | 1 TB SSD 권장 | JetPack + 모델 + 로그 + Chroma |
| USB 카메라 | UVC 호환 (or CSI 카메라) | 720p 이상, 30 fps |
| USB 마이크 | array mic 권장 (ReSpeaker 4-Mic, USB) | beam-forming 으로 노이즈 ↓ |
| 스피커 | 3.5 mm or USB | TTS 출력 |
| 부저 | GPIO active buzzer 5 V | 응급 알림 (오프라인 보장 채널) |
| 응급 버튼 (선택) | momentary push, GPIO 풀업 | 어르신 직접 트리거 |
| 네트워크 | Wi-Fi 또는 LTE USB 모뎀 | FCM/Twilio 도달 |
| 로봇 본체 (선택) | TurtleBot3 / 자체 RC | 거동 시 — Phase 6 |

---

## 2. JetPack + 시스템 환경

### 2.1 OS 플래시
```bash
# Host PC 에서 SDK Manager 로
# JetPack 6.0 / 6.1 (Ubuntu 22.04 + CUDA 12.x + TRT 10.x + DeepStream 7.x)
sudo apt update && sudo apt upgrade
```

### 2.2 ROS 2 설치
- [ ] **ROS 2 Humble** (Ubuntu 22.04 공식) — 권장
- [ ] 또는 **Jazzy from source** (시간 1~2 시간) — WSL 과 버전 맞추려면

```bash
sudo apt install ros-humble-desktop ros-humble-rmw-fastrtps-cpp \
    ros-humble-cv-bridge ros-humble-image-transport
```

### 2.3 Python venv (rclpy 호환)
```bash
python3 -m venv ~/마음돌봄/.venv-ros --system-site-packages
source ~/마음돌봄/.venv-ros/bin/activate
pip install --upgrade pip wheel
```

### 2.4 시스템 패키지
- [ ] portaudio19-dev (sounddevice)
- [ ] libasound2-plugins (PulseAudio bridge)
- [ ] alsa-utils (aplay, arecord)
- [ ] ffmpeg (영상 평가)
- [ ] sqlite3 (alerts.db)

---

## 3. 네이티브 바이너리 재빌드 (aarch64)

x86_64 에서 빌드한 바이너리는 **모두 동작 안 함**. 다음을 재빌드:

### 3.1 TensorRT engine
- [ ] YOLOv8n-face → `.onnx` → `trtexec --onnx=... --saveEngine=...`
- [ ] YOLOv8n-pose → 동일
- [ ] FP16 권장 (Xavier compute 7.2 → INT8 캘리브레이션 시간 대비 이득 작음)
- [ ] 결과: `~/마음돌봄/release/vision/.../engines/{face,pose}_jetson.engine`

```bash
/usr/src/tensorrt/bin/trtexec \
    --onnx=yolov8n-face-nms.onnx \
    --saveEngine=yolov8n-face-nms.jetson.engine \
    --fp16 \
    --memPoolSize=workspace:2048
```

### 3.2 NMS parser .so
- [ ] C++ 코드 그대로 + Jetson nvinfer 헤더 (DeepStream 7.x include) 로 재컴파일
- [ ] 결과: `release/vision/.../so/libnms_parser.aarch64.so`

### 3.3 pyds (DeepStream Python bindings)
- [ ] DeepStream 7.x 설치 시 `/opt/nvidia/deepstream/deepstream/lib/pyds.so` aarch64 자동 설치됨
- [ ] venv 에 심볼릭 링크: `ln -s /opt/.../pyds.so ~/.venv-ros/lib/python3.10/site-packages/pyds.so`

### 3.4 llama.cpp (CUDA aarch64)
```bash
cd ~/llama.cpp
make clean
make GGML_CUDA=1 LLAMA_CUDA_F16=1 -j$(nproc)
```
- [ ] `llama-server` 바이너리 재빌드
- [ ] Xavier compute capability 7.2 → 자동 검출 OK

### 3.5 faster-whisper / ctranslate2
ctranslate2 는 aarch64 wheel 이 PyPI 에 없을 수 있음. 옵션:
- [ ] 옵션 A: ctranslate2 source build (~30분)
- [ ] 옵션 B: **whisper.cpp 로 교체** (CUDA aarch64 빌드 + GGML 모델, 빌드 가벼움)
- [ ] 옵션 C: `--device cpu` 로 강제 (속도 느려도 동작)

### 3.6 webrtcvad
- [ ] PyPI 에 aarch64 wheel 있음 — `pip install webrtcvad-wheels`

---

## 4. 모델·데이터 마이그레이션

### 4.1 LLM — **EXAONE-3.5-7.8B Q4_K_M** 다운로드

| | 값 |
|---|---|
| 모델 | EXAONE-3.5-**7.8B**-Instruct Q4_K_M |
| 파일 크기 | 4.7 GB |
| VRAM (NGL=33) | ~6 GB |
| 컨텍스트 | 8192 |
| 응답 자연스러움 | 우수 (의료 추론 RAG 보강 시 충분) |

- [ ] **다운로드**
  ```bash
  mkdir -p ~/models && cd ~/models
  huggingface-cli download bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \
      EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf \
      --local-dir .
  ```
- [ ] **실행 — 별도 코드 수정 불필요. 기본 프로필이 7.8B**:
  ```bash
  bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh
  # → 자동으로 7.8B Q4_K_M, NGL=33, ctx=8192 적용
  ```
- [ ] **검증**: `curl http://127.0.0.1:8080/v1/models` 응답에 모델 이름 확인

### 4.2 RAG 인덱스 (MiniLM → bge-m3 업그레이드 + 재빌드)
**현재 WSL 상태**: MiniLM 384d 인덱스 (CPU 빌드 한계 회피용 fallback). bge-m3 코드는 준비됨.

- [ ] **bge-m3 모델 캐시 이전** (~6.4 GB, 다시 다운받지 않으려면)
  ```bash
  rsync -avz --progress ~/.cache/huggingface/hub/models--BAAI--bge-m3/ \
      jetson:~/.cache/huggingface/hub/models--BAAI--bge-m3/
  ```
- [ ] **5 파일에서 모델 ID 교체** — 코멘트 마커로 위치 검색
  ```bash
  grep -rn "Xavier 이전 후" ~/마음돌봄/mind_care_vision/
  # → rag.py / build_chroma_index.py / build_chroma_disease.py
  #    config/hri_params.yaml / llm_dialogue_node.py
  # 모두 "BAAI/bge-m3" 로 교체
  ```
- [ ] **Chroma 재빌드** (Jetson CUDA, ~10 분)
  ```bash
  rm -rf ~/마음돌봄/med_data/chroma_db
  RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_disease.py
  RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_index.py
  ```
- [ ] **검증**: `python /tmp/finish_rag_smoke.py` — 의료 질문 3 개로 retrieval 품질 확인.
  MiniLM 대비 상위 hit 의 의료 관련성 ↑, score 분포도 더 좋음.

> **참고**: bge-m3 가 `pytorch_model.bin` 만 제공해서 CVE-2025-32434 (torch.load 보안 이슈)
> 우회용으로 `.safetensors` 변환이 한 번 필요. WSL 에서 이미 변환 완료 (`~/.cache/.../snapshots/.../model.safetensors`).
> rsync 로 같이 따라가면 됨.

### 4.3 화자 임베딩 — **Xavier 첫날 신규 등록 (필수)**
WSL 에서 미리 안 만들어둠. 이유:
- WSL 의 PulseAudio 브리지를 통해 Windows 마이크로 임베딩을 만들어도,
  Xavier 의 USB 마이크 (다른 frequency response · 노이즈 floor) 와 매칭이 안 좋음.
- 어차피 운영용 임베딩은 실제 운영 마이크로 만들어야 정확.

- [ ] **Xavier 마이크 연결 후** `enroll_speaker.py` 실행
  ```bash
  source ~/마음돌봄/.venv-ros/bin/activate
  python ~/마음돌봄/mind_care_vision/tools/enroll_speaker.py
  # → ~/models/speaker.npy 생성 (~2 KB)
  ```
- [ ] 검증: 본인 발화 → ASR 통과, 가족·TV 음성 → "[SV] 미등록 화자 — 응답 생략" 로그
- [ ] 임계값 조정 (필요 시): `hri_params.xavier.yaml` 의 `sv_threshold`
  - 본인도 거부 → 0.65 로 낮춤
  - 다른 사람도 통과 → 0.80 으로 올림

### 4.4 Vision 가중치
- [ ] `mini_xception_best.pt` (FER+ + RAF-DB 학습된 .pt) — 그대로 복사 OK
- [ ] YOLOv8n-face/pose `.onnx` — 그대로 복사 후 §3.1 trtexec

---

## 5. 설정 파일 (xavier override)

### 5.1 `config/hri_params.xavier.yaml`
- [ ] `audio_bridge_node.device: "cuda"` (WSL CPU → Xavier CUDA)
- [ ] `audio_bridge_node.compute_type: "float16"`
- [ ] `vision_node.engine_path:` → jetson .engine 경로
- [ ] `tts_node.tts_backend:` → 인터넷 끊긴 환경이면 `melo` (오프라인)

### 5.2 launch override
```bash
ros2 launch mind_care_vision mind_care.launch.py \
    config_file:=$HOME/마음돌봄/mind_care_vision/config/hri_params.xavier.yaml
```

---

## 6. 하드웨어 통합

### 6.1 카메라
- [ ] `v4l2-ctl --list-devices` 로 디바이스 확인
- [ ] DeepStream pipeline source: `v4l2src device=/dev/video0`

### 6.2 마이크
- [ ] `python -m sounddevice` 로 device index 확인
- [ ] `hri_params.xavier.yaml` 의 `input_device` 갱신 (-1 이 안 먹으면 명시)

### 6.3 부저 (GPIO)
- [ ] Jetson.GPIO 라이브러리 설치 — `pip install Jetson.GPIO`
- [ ] `mind_care_emergency/channels/buzzer_channel.py` 구현 (현재 stub)
- [ ] 핀 매핑 — Pin 7 (BCM 4) 기본
```python
import Jetson.GPIO as GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(7, GPIO.OUT)
GPIO.output(7, GPIO.HIGH)  # 부저 ON
```

### 6.4 응급 버튼 (선택)
- [ ] GPIO 인터럽트 핸들러 → `/emergency/manual_trigger` 토픽 publish
- [ ] decider 가 panic_word 와 동일하게 처리

---

## 7. 운영화

### 7.1 systemd unit
- [ ] `/etc/systemd/system/mindcare.service`
```ini
[Unit]
Description=Mind Care Vision HRI
After=network-online.target

[Service]
Type=simple
User=eslee03
WorkingDirectory=/home/eslee03/마음돌봄
ExecStart=/bin/bash /home/eslee03/마음돌봄/mind_care_vision/scripts/start_hri.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```
- [ ] `sudo systemctl enable mindcare && sudo systemctl start mindcare`

### 7.2 로그 로테이션
- [ ] `/etc/logrotate.d/mindcare` — `/var/log/mindcare/*.log` 매주 회전, 4 주 보관

### 7.3 헬스체크 (외부)
- [ ] `api_gateway` 의 `/healthz` 엔드포인트 → 외부 cron 으로 5 분마다 ping
- [ ] 무응답 → 보호자 SMS

### 7.4 OTA 업데이트
- [ ] `git pull && colcon build --symlink-install && systemctl restart mindcare`
- [ ] 스크립트 `tools/ota_update.sh`

---

## 8. 검증 단계

### 8.1 기동 검증 (5 분)
- [ ] `bash scripts/healthcheck.sh` — 모든 ✔ 떠야 함
- [ ] `bash scripts/smoke_e2e.sh` — 4/4 시나리오 ✔

### 8.2 latency 재측정
- [ ] WSL 대비 비교 표 작성 (KPI §4.3 갱신)
  - ASR: WSL CPU 234 ms → Xavier CUDA ~50 ms 예상
  - LLM: WSL 2.4B 1500 ms (개발 검증치) → Xavier 7.8B 1800 ms 예상
  - Vision: WSL 1650 Ti FP16 30 ms → Xavier FP16 25 ms 예상
- [ ] 결과보고서 §4 갱신

### 8.3 24 h burn-in
- [ ] `nohup bash scripts/start_hri.sh > /var/log/mindcare/burnin.log 2>&1 &`
- [ ] htop / nvidia-smi 로 메모리·온도 추이 매시간 sample
- [ ] 결과: MTBF 행 갱신

---

## 9. 시연 준비

- [ ] 데모 시나리오 영상 5 종 (정상 대화 / 낙상 / 응급 / 미등록 차단 / 의료 RAG)
- [ ] 발표 슬라이드 (시스템 다이어그램 + KPI 표 + 영상 link)
- [ ] **백업 계획**: 시연 도중 Xavier 멈출 경우 WSL fallback (LAN 으로 같은 데모 가능하게 사전 테스트)

---

## 일정 (예시)

| 일자 | 작업 |
|---|---|
| D-7 | 자체 시연 영상 (WSL) + KPI 재측정 |
| D-6 | Burn-in 24 h |
| D-5 | git tag v0.1, repo 정리 |
| D-4 | Xavier 인수, JetPack 플래시 |
| D-3 | 네이티브 바이너리 재빌드 (§3) |
| D-2 | 모델 마이그레이션 + smoke_e2e 검증 |
| D-1 | latency 재측정 + 시연 리허설 |
| D-day | 발표 |
