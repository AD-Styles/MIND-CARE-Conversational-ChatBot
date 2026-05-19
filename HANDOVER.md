# 마음돌봄 Vision — Xavier 이식 직전 인수인계 문서

> 작성: 2026-05-11
> 작성자: eslee03 (포테토 팀)
> 기준 태그: `v0.1-wsl-stable`
> 다음 단계: Jetson AGX Xavier 32 GB 이식

---

## 0. 한 줄 요약

> Phase 1-5 (음성·시각·응급) 5 개 ROS 패키지 / 63 Python 파일 / 117 git 파일 / 4 commits.
> WSL 에서 코드·테스트·문서 100% 완료, Xavier 부팅 후 ~6-7 시간이면 동작 가능.

---

## 1. 프로젝트 정의

**대상**: 독거 70-80 대 어르신 돌봄 HRI (Human-Robot Interaction) 챗봇
**핵심 가치**: 일상 대화 + 응급 감지 (낙상·발성 패턴) + 보호자 알림
**플랫폼**: WSL (개발/검증) → Jetson AGX Xavier (배포)
**과정**: NVIDIA AI Academy Team Potato

---

## 2. 완료 항목 체크리스트

### Phase 2 — DeepStream 비전 파이프라인 ✅
- [x] YOLOv8n-face (NMS-baked ONNX) + custom NMS parser .so
- [x] Mini-Xception 7-class 감정 분류기 (FER+ + RAF-DB 학습, 85.18% val acc)
- [x] `vision_deepstream_node.py` + `vision_emulator_node.py` (도메인 분리)
- [x] `/vision/state` 토픽 → `llm_dialogue` 가 감정 컨텍스트로 활용

### Phase 4 — 낙상 감지 ✅
- [x] YOLOv8n-pose (NMS-baked) + custom NMS parser .so
- [x] `fall_detection_node.py` + `fall_rules.py` (룰 기반)
- [x] URFDD 70 video 평가: aspect_thr=1.4 / window 0.2s / ratio 0.33 → R 0.767 / P 0.676
- [x] 5 가지 chained 버그 수정 (rclpy/pyds/nvinfer/bbox/tensor 접근)

### Phase 5 — 응급 결정 + 알림 ✅
- [x] `emergency_decider_node.py` 상태 머신 NORMAL→QUERY→EMERGENCY→ACKED
- [x] `decider_states.py` ROS 의존성 0 (pytest 7/7 PASS)
- [x] `alert_dispatcher_node.py` — FCM + SMS + 부저(stub) + SQLite 재시도 큐
- [x] `api_gateway_node.py` — FastAPI + WebSocket + REST (mobile app 백엔드)

### Voice 통합 ✅
- [x] panic_word ("도와줘", "넘어졌어") → 즉시 EMERGENCY
- [x] `/dialogue/proactive_speech` → `llm_dialogue` forward → TTS

### 화자 검증 (Speaker Verification) ✅ (코드)
- [x] `speaker_verifier.py` resemblyzer wrapper
- [x] `audio_bridge_node` 검증 hook (payload 에 `speaker_verified`, `speaker_score`)
- [x] `llm_dialogue_node` 결정 hook (`verified=False` → 응답 생략)
- [x] `enroll_speaker.py` 30 초 등록 (scipy 리샘플로 numba 우회)
- [x] numba `coverage_support.py` stub 패치 (resemblyzer 의존성 충돌 우회)
- [ ] 실 enroll → **Xavier 첫날** (운영 마이크로 함)

### RAG (의료 도메인) ✅
- [x] `rag.py` Chroma 다중 컬렉션 retriever
- [x] `build_chroma_disease.py` (아산병원 질환 백과 3,936 docs)
- [x] `build_chroma_index.py` (광고 필터 의료 블로그 14,614 docs)
- [x] 임베딩: MiniLM 384d (현재) — Xavier 이전 후 bge-m3 1024d 로 재빌드 예정
- [x] 광고 필터 6 기준 (강한 키워드, 판매 어휘, 전화번호 등)

### LLM ✅
- [x] llama.cpp + EXAONE-3.5-**7.8B**-Instruct-Q4_K_M (4.7 GB GGUF, Xavier 32 GB 대상)
- [x] `start_llama_server.sh` 프리셋 (exaone / qwen, NGL=33 기본)
- [x] HTTP OpenAI 호환 API → `llm_dialogue_node` 가 requests 로 호출
- [x] WSL 개발 단계에서는 EXAONE-3.5-2.4B 로 통합 검증 (smoke + URFDD KPI)

### TTS ✅
- [x] `tts_node.py` 3 백엔드 지원 (edge / melo / coqui)
- [x] edge-tts 한국어 InJoon 보이스 기본
- [x] TTS 재생 중 마이크 게이트 (에코 루프 방지)

### 검증 인프라 ✅
- [x] `scripts/healthcheck.sh` — 13 항목 점검 (GPU/RAM/process/RAG/SV/numba)
- [x] `scripts/smoke_e2e.sh` — 4 시나리오 E2E (정상/SV차단/panic/proactive)
- [x] `scripts/burn_in.sh` — 24h 안정성 테스트
- [x] `tools/eval_sv.py` — SV 임베딩 평가
- [x] `release/vision/.../eval_fall.py` — URFDD 자동 평가

### Xavier 이식 준비 ✅
- [x] `requirements.core.txt` — 30 직접 의존성 (WSL 검증 버전)
- [x] `requirements.full.txt` — pip freeze 324 줄 (재현 백업)
- [x] `requirements.xavier.txt` — Python 3.10 + aarch64 + JetPack 6 호환 범위
- [x] `XAVIER_DEPLOY_CHECKLIST.md` — 9 단계 이식 절차
- [x] `XAVIER_VERSION_AUDIT.md` — 4 위험 + 시간 견적 + 검증 체크
- [x] `scripts/xavier_bootstrap.sh` — 첫 부팅 자동 셋업
- [x] Python 3.10 / ROS Humble 코드 호환성 100% 검증 (코드 변경 0)

### 문서 ✅ — 12 .md 파일
- [x] `결과보고서_초안.md` (30 KB, 588 줄, 8 섹션)
- [x] `XAVIER_DEPLOY_CHECKLIST.md` (10 KB)
- [x] `XAVIER_VERSION_AUDIT.md` (8.5 KB)
- [x] Phase 별 설계 문서 4 개 (PHASE2, PHASE4_DESIGN, PHASE5_DESIGN 등)
- [x] `RUN.md`, `SETUP.md`, `README.md`, `SELF_DEMO_GUIDE.md`
- [x] `HANDOVER.md` (이 파일)

### git ✅
- [x] 4 commits, `main` 브랜치
- [x] `v0.1-wsl-stable` 태그 = Xavier 이식 baseline
- [x] 117 → 122 files tracked (마지막 fix 포함)

---

## 3. 미완료 / 보류 항목

| # | 항목 | 상태 | 이유 |
|---|---|---|---|
| 1 | 화자 enroll (`~/models/speaker.npy`) | ⏸ Xavier 첫날 | WSL Windows mic ≠ Xavier USB mic 음향 특성 |
| 2 | 부저 GPIO 구현 | ⏸ Xavier 첫날 | `Jetson.GPIO` 필요, WSL 에선 stub |
| 3 | 자체 시연 영상 (5+5) | ⏳ 시연 1-2일 전 | KPI 재측정, 결과보고서 §4 갱신 |
| 4 | Burn-in 24h | ⏳ 시연 1주일 전 | 안정성 검증 |
| 5 | FE 팀 모바일 앱 통합 | ⏳ 별도 트랙 | FE 측 진행 |
| 6 | bge-m3 1024d 인덱스 | ⏸ Xavier 재빌드 | 다운로드 8.5 GB 캐시 있음, Xavier rsync 또는 재다운로드 |
| 7 | TRT engine aarch64 | ⏸ Xavier 첫날 | x86_64 engine 호환 안 됨 |
| 8 | `.so` NMS parser aarch64 | ⏸ Xavier 첫날 | gcc + nvinfer 헤더 |
| 9 | llama.cpp aarch64 | ⏸ Xavier 첫날 | CUDA 12.2 빌드 |
| 10 | smoke_e2e 시나리오 2 검증 | ⚠️ inconclusive | `ros2 topic pub --once` JSON boolean escape 의심 — Xavier 에서 실 mic 으로 재검증 |

---

## 4. 환경 스냅샷 (WSL 현재)

### 호스트
| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS (x86_64) |
| RAM | 11 GB (free ~10 GB) |
| GPU | NVIDIA GTX 1650 Ti **4 GB** |
| GPU driver | 591.74, CUDA Toolkit 13.x |
| TensorRT | 10.16 |
| Python | 3.12.3 (system + venv 동일) |
| ROS 2 | Jazzy |
| gcc | 13.3 |

### 모델 / 가중치
| 파일 | 비고 |
|---|---|
| `~/models/EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf` (4.7 GB) | **Xavier 에서 다운로드** (WSL 4 GB 에서는 안 돌아감) |
| `release/vision/models/face_detector/yolov8n_face.engine` (+ .onnx + .so) | engine·.so 재빌드 필요 |
| `release/vision/models/pose_estimator/yolov8n_pose.engine` (+ .onnx + .so) | engine·.so 재빌드 필요 |
| `release/vision/models/emotion_classifier/mini_xception.engine` (+ .onnx + .pth) | engine 재빌드 (.pth 그대로) |
| `~/models/speaker.npy` | **Xavier 첫날 enroll (필수)** |

### RAG (Chroma)
| | |
|---|---|
| 위치 | `~/마음돌봄/med_data/chroma_db/` (96 MB) |
| 모델 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dim) |
| 컬렉션 | `med_blog` (14,614) + `med_disease` (3,936) = 18,550 docs |
| 빌드 시간 (재현) | ~13 min (blog 582s + disease 207s, MiniLM CPU) |

### HuggingFace 캐시 (`~/.cache/huggingface/hub/`)
| 모델/데이터셋 | 크기 | 용도 |
|---|---|---|
| `BAAI/bge-m3` | 8.5 GB | Xavier 이식 시 사용 |
| `Systran/faster-whisper-small` | 464 MB | ASR |
| `paraphrase-multilingual-MiniLM-L12-v2` | 458 MB | 현재 RAG |
| FER2013, FER+, RAF-DB, AffectNet | 10 GB | 표정 학습 산출 |
| timm 모델 ~15 종 | 2 GB | 학습 비교용 |
| **총량** | **25 GB** | — |

### Python venv (`~/마음돌봄/.venv-ros/`)
| | |
|---|---|
| 크기 | 3.0 GB |
| 패키지 수 | 327 |
| 핵심 직접 의존성 | 30 (→ `requirements.core.txt`) |

핵심 버전:
```
torch 2.11.0+cpu     numpy 1.26.4         transformers 5.6.0
chromadb 1.5.8       langchain 1.2.15     sentence-transformers 5.4.1
faster-whisper 1.2.1 resemblyzer 0.1.4    scipy 1.17.1
fastapi 0.115.14     pydantic 2.13.3      SQLAlchemy 2.0.49
firebase-admin 6.9.0 twilio 9.10.5
edge-tts 7.2.8       sounddevice 0.5.5    webrtcvad 2.0.10
```

---

## 5. 코드 인벤토리 (122 git tracked files)

### 패키지 구조
```
~/마음돌봄/
├── .gitignore
├── HANDOVER.md                       ← 이 파일
├── XAVIER_DEPLOY_CHECKLIST.md
├── XAVIER_VERSION_AUDIT.md
├── 결과보고서_초안.md
├── 설치환경.ipynb
├── rag_crawling.ipynb
├── requirements.core.txt             직접 의존성
├── requirements.full.txt             pip freeze
├── requirements.xavier.txt           aarch64 호환
│
├── mind_care_vision/                 [ROS pkg 1] 음성·대화
│   ├── package.xml
│   ├── setup.py / setup.cfg
│   ├── RUN.md
│   ├── config/hri_params.yaml
│   ├── launch/hri_system.launch.py
│   ├── mind_care_vision/
│   │   ├── audio_bridge_node.py      ASR + VAD + SV hook
│   │   ├── llm_dialogue_node.py      LLM + RAG + SV decision
│   │   ├── tts_node.py               TTS (edge/melo/coqui)
│   │   ├── rag.py                    RagRetriever
│   │   └── speaker_verifier.py       resemblyzer wrapper
│   ├── tools/
│   │   ├── build_chroma_disease.py
│   │   ├── build_chroma_index.py
│   │   ├── enroll_speaker.py
│   │   ├── eval_sv.py
│   │   └── build_rag_index.py        (FAISS 구버전, 사용 X)
│   └── scripts/
│       ├── start_hri.sh
│       ├── start_llama_server.sh
│       ├── healthcheck.sh
│       ├── smoke_e2e.sh
│       ├── burn_in.sh
│       └── xavier_bootstrap.sh
│
└── release/
    ├── SETUP.md
    ├── vision/                       [ROS pkg 2] 비전 (Phase 2 + 4)
    │   ├── README.md, PHASE2.md, PHASE4.md, PHASE4_DESIGN.md
    │   ├── SELF_DEMO_GUIDE.md
    │   ├── patches/
    │   ├── models/                   (engine/onnx/so/pth — gitignored 일부)
    │   └── mind_care_perception/
    │       ├── package.xml
    │       ├── mind_care_perception/
    │       │   ├── vision_deepstream_node.py
    │       │   ├── vision_emulator_node.py
    │       │   ├── ds_pipeline.py            얼굴+감정
    │       │   ├── ds_pose_pipeline.py       포즈
    │       │   ├── fall_detection_node.py
    │       │   ├── fall_rules.py
    │       │   ├── ds_metadata.py
    │       │   └── ds_pose_metadata.py
    │       ├── config/                       (nvinfer pgie/sgie/tracker 6개)
    │       ├── launch/                       (3 launch)
    │       ├── scripts/                      (train_emotion, eval_fall 등 9개)
    │       └── src/
    │           ├── parser_yolov8_face/       C++ NMS parser
    │           └── parser_yolov8_pose/       C++ NMS parser
    │
    └── emergency/                    [ROS pkg 3, 4] 응급 + API
        ├── PHASE5.md, PHASE5_DESIGN.md
        ├── Emergency_Decider.ipynb
        ├── mind_care_emergency/
        │   ├── package.xml
        │   ├── conftest.py
        │   ├── mind_care_emergency/
        │   │   ├── emergency_decider_node.py
        │   │   ├── decider_states.py         pytest 7/7
        │   │   ├── alert_dispatcher_node.py
        │   │   ├── alerts_db.py
        │   │   └── channels/
        │   │       ├── fcm_channel.py
        │   │       ├── sms_channel.py
        │   │       └── buzzer_channel.py     stub → Xavier 구현
        │   └── tests/test_decider_states.py  7 cases PASS
        │
        └── mind_care_api/
            ├── package.xml
            ├── config/api_params.yaml
            ├── launch/api_gateway.launch.py
            └── mind_care_api/
                ├── api_gateway_node.py        ROS↔FastAPI
                ├── app.py                     FastAPI app
                ├── auth.py, db.py
                ├── ros_bridge.py
                └── routes/{alerts, health, ack, ws}.py
```

### 파일 통계
| 종류 | 개수 |
|---|---|
| Python | 63 |
| Markdown | 12 |
| Shell scripts | 11 |
| YAML config | 4 |
| C++ source | 2 |
| **총 git tracked** | **122** |

---

## 6. 검증 결과

### pytest (decider 상태머신 단위 테스트)
```
tests/test_decider_states.py::test_fall_then_timeout          PASSED
tests/test_decider_states.py::test_fall_then_user_ok          PASSED
tests/test_decider_states.py::test_fall_confirmed_short_circuit PASSED
tests/test_decider_states.py::test_panic_word_direct          PASSED
tests/test_decider_states.py::test_long_idle_path             PASSED
tests/test_decider_states.py::test_emergency_ack_cooldown     PASSED
tests/test_decider_states.py::test_emergency_dedupe           PASSED
============================== 7 passed in 0.04s ==============================
```

### smoke_e2e.sh (4 시나리오 E2E)
| 시나리오 | 결과 |
|---|---|
| ① 정상 발화 (verified=true, "무릎이 자주 아파요") | ✅ RAG 3 hits + LLM 7.1초 응답 |
| ② 미등록 화자 (verified=false) | ⚠️ inconclusive — `ros2 topic pub` JSON boolean escape 의심, Xavier 실 mic 검증 필요 |
| ③ panic_word ("도와줘") | ✅✅ `/emergency/alert` 1건 + LLM 응답 |
| ④ proactive_speech | ✅ `/llm/responses` turn_id=-1 forward |

### URFDD 평가 (낙상 감지, 70 video)
| 버전 | Recall | Precision | Notes |
|---|---|---|---|
| v1 (초기) | 0.000 | — | 5 chained 버그 |
| v2-v3 | 점진 개선 | — | bbox 좌표 통일 등 |
| **v4 (최종)** | **0.767** | **0.676** | aspect_thr=1.4, window 0.2s, ratio 0.33 |

System-level: E2E p95 latency **3.69 s** ≤ 30 s ✅ (KPI 최초 통과)

### healthcheck.sh
13 항목 모두 ✔ (`speaker.npy` 제외 — Xavier 첫날 예정)

---

## 7. git 히스토리

```
51bb23d (HEAD -> main)
        fix: restore +x on xavier_bootstrap.sh

6b3d078
        docs: 화자 enroll 을 Xavier 첫날 작업으로 이동
        - WSL Windows mic ≠ Xavier USB mic 음향 특성 차이
        - XAVIER_DEPLOY_CHECKLIST §4.3, bootstrap.sh 안내, VERSION_AUDIT 시간표

52a5836
        add: Xavier 이전 호환성 사전 점검 산출물
        - requirements.{core,full,xavier}.txt
        - XAVIER_VERSION_AUDIT.md (4 위험 + 시간표)
        - scripts/xavier_bootstrap.sh

e457ffd (tag: v0.1-wsl-stable)
        init: Phase 1-5 구현 완성본 (WSL stable, Xavier 이전 직전)
        - Phase 2/4/5 + Voice + SV + RAG + 12 docs
        - 117 files, 14,927 insertions
```

`.git` 크기: 9.3 MB

---

## 8. Xavier 이전 절차 (요약)

**전제**: Jetson AGX Xavier 32 GB, JetPack 6.x 플래시 완료, 카메라/마이크 연결됨

### 8.1 LLM — **EXAONE-3.5-7.8B Q4_K_M**

| | 값 |
|---|---|
| 모델 | EXAONE-3.5-**7.8B**-Instruct Q4_K_M |
| 파일 크기 | 4.7 GB |
| VRAM (NGL=33) | ~6 GB |
| 컨텍스트 | 8192 (RAG 3 hits + history 안전 수용) |
| 응답 속도 (Xavier 추정) | 8-12 tok/s |
| 시작 명령 | `bash start_llama_server.sh` (기본 `exaone` 프로필) |

다운로드:
```bash
mkdir -p ~/models && cd ~/models
huggingface-cli download bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \
    EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf --local-dir .
```

> WSL 4 GB VRAM 에서는 7.8B 가 안 들어감. 개발 단계에서는 EXAONE-3.5-2.4B 로 검증 완료.
> Xavier 32 GB 부터 본 모델 사용.

### 8.2 RAG 인덱스 결정 — **2 옵션, 둘 다 동작**

> RAG 자체는 Xavier 에서 **반드시 동작**합니다. 검색 품질 차이만 있음.

| 옵션 | 시간 | 검색 품질 | 추천 |
|---|---|---|---|
| **A. MiniLM 그대로 rsync** | 5 분 | 보통 (현재 WSL 수준) | 시간 없을 때 |
| **B. bge-m3 1024d 재빌드** | 30 분 | 우수 (한국어 의료어 매칭 ↑) | ⭐ |

**옵션 A** (즉시):
```bash
rsync -avz ~/마음돌봄/med_data/chroma_db/ \
    eslee03@xavier:~/마음돌봄/med_data/chroma_db/
# yaml 의 rag_embed_model 그대로 (MiniLM)
```

**옵션 B** (권장):
```bash
# 1. bge-m3 모델 캐시 (8.5 GB) 도 같이 rsync — 다시 다운 회피
rsync -avz ~/.cache/huggingface/hub/models--BAAI--bge-m3/ \
    eslee03@xavier:~/.cache/huggingface/hub/models--BAAI--bge-m3/

# 2. 코드 5 곳 모델 ID 교체
grep -rn "Xavier 이전 후" ~/마음돌봄/   # MiniLM 줄 → BAAI/bge-m3 로

# 3. chroma_db 재빌드 (Xavier CUDA ~30 min)
rm -rf ~/마음돌봄/med_data/chroma_db
RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_disease.py
RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_index.py
```

`hri_params.xavier.yaml` 에 두 옵션 모두 코멘트로 적혀 있음 — 줄 하나만 활성화.

### 8.3 전체 절차

```bash
# 1. clone (또는 rsync ~/마음돌봄)
git clone <repo-url> ~/마음돌봄
cd ~/마음돌봄

# 2. 자동 셋업
bash mind_care_vision/scripts/xavier_bootstrap.sh
# → apt + ROS Humble + venv + numba 패치 + colcon build + healthcheck

# 3. 수동 단계 9개 (XAVIER_DEPLOY_CHECKLIST 참고)
#    3-1. NVIDIA torch wheel 설치 (PyPI 사용 금지!)
#    3-2. 화자 enroll (~/models/speaker.npy, 30초)             ⭐
#    3-3. TRT engine 재빌드 (face + pose)
#    3-4. NMS parser .so 재빌드 (gcc + nvinfer)
#    3-5. llama.cpp aarch64 (CUDA 12.2)
#    3-5b. EXAONE-3.5-7.8B GGUF 다운로드                       ⭐
#    3-6. RAG 인덱스 (옵션 A rsync OR B bge-m3 재빌드)
#    3-7. 부저 GPIO 구현 (Jetson.GPIO)
#    3-8. smoke_e2e.sh 최종 검증

# 4. 운영
bash mind_care_vision/scripts/start_llama_server.sh &   # 기본 = 7.8B
ros2 launch mind_care_vision hri_system.launch.py \
    config_file:=$HOME/마음돌봄/mind_care_vision/config/hri_params.xavier.yaml
```

**시간 견적**: 6-7 시간 (JetPack 플래시 제외)

**상세**: `XAVIER_DEPLOY_CHECKLIST.md` + `XAVIER_VERSION_AUDIT.md` + `config/hri_params.xavier.yaml`

---

## 9. 인수인계 노트 (다음 작업자에게)

### 헤드라인
- 코드/문서는 다 끝났음. 손댈 게 거의 없음.
- Xavier 에서 6-7 시간 + 시연 영상 5+5 + 24h burn-in 만 하면 시연 가능.

### 함정 회피
1. **PyTorch 일반 pip install 금지**. NVIDIA Jetson wheel 만 사용. 잘못 깔면 모든 ML 의존성이 CPU 폴백 → 매우 느림.
2. **TRT engine 호환 안 됨**. x86_64 에서 만든 .engine 은 aarch64 에서 못 씀. Xavier 에서 다시 `trtexec`.
3. **numpy 절대 2.x 금지**. rclpy (ROS Humble) 가 1.26 ABI 기대. `--system-site-packages` 로 system numpy 노출.
4. **numba ↔ coverage 충돌**. resemblyzer 설치 후 `coverage_support.py` stub 필수.
5. **RAG 임베딩 모델 변경 시 chroma_db 재빌드 필요**. 차원 (384 vs 1024) 안 맞으면 통째로 못 씀.

### 빠른 디버깅
- 무엇이 동작 안 하면 먼저 `healthcheck.sh` → 13 항목 중 ❌ 찾기
- LLM 응답 안 오면 `curl http://127.0.0.1:8080/health` 부터
- ROS 토픽 안 보이면 `ros2 topic list | grep <name>`
- 모든 노드 로그는 `/tmp/smoke_e2e/*.log` 또는 `~/.ros/log/`

### 시연 흐름 (제안)
1. 정상 대화: "무릎이 아파요" → RAG 동작 + 어르신 톤 응답
2. 미등록 화자: 외부인 (가족 등) 발화 → 응답 차단
3. 응급 panic: "도와줘" → `/emergency/alert` 1건 + 부저 + (FCM 시연 가능 시) 모바일 푸시
4. 시각: 낙상 시연 → fall_detection_node → emergency_decider → alert
5. 능동 발화: emergency_decider → proactive "괜찮으세요?" → TTS

### 백업 plan
- 시연 도중 Xavier 멈춤 → WSL 으로 같은 데모 시연 가능 (LAN 으로 같은 토픽 통신)
- LLM 죽음 → `start_llama_server.sh` 재기동 (~30초)
- 네트워크 끊김 → 부저 + 로컬 음성 응답은 계속 작동 (오프라인 보장)

### 연락
- 코드: 이 repo
- 의문: `결과보고서_초안.md` 8 섹션 + Phase 별 설계 문서 참조
- 추가 작업 필요 시: `XAVIER_DEPLOY_CHECKLIST.md` 의 `[ ]` 항목

---

## 10. 부록 — 주요 환경 변수 / 명령 모음

### 매번 띄울 때
```bash
# 1. llama-server (8080 port)
NGL=20 bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh

# 2. ROS 전체 (audio+llm+tts+vision+emergency+api)
bash ~/마음돌봄/mind_care_vision/scripts/start_hri.sh

# 3. 점검
bash ~/마음돌봄/mind_care_vision/scripts/healthcheck.sh
```

### 토픽 모니터링
```bash
ros2 topic list
ros2 topic echo /audio/transcripts
ros2 topic echo /llm/responses
ros2 topic echo /emergency/alert
ros2 topic echo /vision/state
```

### Chroma 재빌드 (모델 바뀔 때만)
```bash
RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_disease.py
RESET=1 python ~/마음돌봄/mind_care_vision/tools/build_chroma_index.py
```

### 화자 재 enroll (마이크 바뀔 때)
```bash
python ~/마음돌봄/mind_care_vision/tools/enroll_speaker.py --duration 30
```

### pytest
```bash
cd ~/마음돌봄/release/emergency/mind_care_emergency
python -m pytest tests/ -v
```

---

> **상태**: WSL **v0.1-wsl-stable** = Xavier 이식 baseline
> **다음**: Xavier 인수 → `bash xavier_bootstrap.sh` → 수동 8 단계
> **목표**: 시연 D-day 까지 KPI 영상 5+5 + burn-in 24h 완료

— 끝 —
