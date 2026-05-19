#!/usr/bin/env bash
# xavier_bootstrap.sh — Jetson AGX Xavier (JP5.1.x / R35.6.4) 환경 셋업 자동화.
#
# 가정: JetPack 5.1.x 플래시 완료 (Ubuntu 20.04 + CUDA 11.4 + TRT 8.5 + DS 6.2),
#       인터넷 연결됨, ~/마음돌봄 압축 해제됨.
# 원본(JP6 가정)은 xavier_bootstrap.sh.jp6.bak 에 백업됨.
#
# 단계:
#   1. apt 시스템 패키지 (python3.8 base)
#   2. ROS 2 Foxy + key (Humble 대체 — Ubuntu 20.04 native)
#   3. Python 3.8 venv 생성
#   4. NVIDIA torch wheel JP5 (2.1.0a0+41361538.nv23.06 cp38)
#   5. requirements.xavier.txt 설치
#   6. numba coverage_support 패치
#   7. ros2_ws 심볼릭 링크 + colcon build
#   8. healthcheck
#
# 사용:
#   bash mind_care_vision/scripts/xavier_bootstrap.sh
#   SKIP_APT=1 bash ...           # apt 부분 건너뜀

set -e
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GRN}✔${NC} $1"; }
warn() { echo -e "${YLW}⚠${NC} $1"; }
err()  { echo -e "${RED}✖${NC} $1"; exit 1; }

REPO_ROOT="$HOME/마음돌봄"
VENV="$REPO_ROOT/.venv-ros"
WS="$HOME/ros2_ws"

[ -d "$REPO_ROOT" ] || err "Repo 없음: $REPO_ROOT (먼저 git clone)"

# ============================================================
# 1. JetPack 확인
# ============================================================
echo "════════════════════════════════════════════════════"
echo "  Xavier Bootstrap — $(date '+%H:%M:%S')"
echo "════════════════════════════════════════════════════"

if [ -f /etc/nv_tegra_release ]; then
    rel=$(head -1 /etc/nv_tegra_release)
    ok "JetPack: $rel"
    echo "$rel" | grep -q "R35" || warn "  R35 (JetPack 5.1.x) 가 아님 — 본 스크립트는 JP5 가정"
else
    warn "Jetson 환경 아닌 듯 (/etc/nv_tegra_release 없음)"
fi

# ============================================================
# 2. apt 시스템 패키지
# ============================================================
if [ "${SKIP_APT:-0}" != "1" ]; then
    echo ""
    echo "[1/8] apt 시스템 패키지 설치 …"
    sudo apt update
    sudo apt install -y \
        python3.8 python3.8-venv python3.8-dev python3-pip \
        portaudio19-dev libasound2-plugins alsa-utils \
        ffmpeg sqlite3 \
        build-essential cmake git pkg-config \
        libsndfile1 libsndfile1-dev \
        curl wget \
        libopenblas-dev libopenmpi-dev \
        v4l-utils
    ok "apt 패키지 OK"
fi

# ============================================================
# 3. ROS 2 Foxy (없으면) — Ubuntu 20.04 (focal) native
# ============================================================
ROS_DISTRO=foxy
if ! [ -f /opt/ros/$ROS_DISTRO/setup.bash ]; then
    echo ""
    echo "[2/8] ROS 2 $ROS_DISTRO 설치 …"
    sudo apt install -y software-properties-common curl
    sudo add-apt-repository universe -y
    sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        -o /usr/share/keyrings/ros-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
        http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
        | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null
    sudo apt update
    sudo apt install -y \
        ros-$ROS_DISTRO-desktop \
        ros-$ROS_DISTRO-rmw-fastrtps-cpp \
        ros-$ROS_DISTRO-cv-bridge \
        ros-$ROS_DISTRO-image-transport \
        python3-colcon-common-extensions \
        python3-rosdep
    ok "ROS $ROS_DISTRO 설치"
else
    ok "ROS $ROS_DISTRO 이미 설치됨"
fi

# ============================================================
# 4. Python venv
# ============================================================
echo ""
echo "[3/8] Python 3.8 venv …"
if ! [ -d "$VENV" ]; then
    python3.8 -m venv "$VENV" --system-site-packages
    ok "venv 생성: $VENV"
else
    ok "venv 이미 있음"
fi
source "$VENV/bin/activate"
pip install --upgrade "pip<24.1" wheel setuptools >/dev/null   # pip 24.1+ 는 PEP 668 더 엄격, JP5 에선 23.x 안전

# ============================================================
# 5. NVIDIA torch wheel
# ============================================================
echo ""
echo "[4/8] NVIDIA torch wheel (JP5.1.x, CUDA 11.4, cp38) …"
TORCH_WHL="torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux_aarch64.whl"
TORCH_URL="https://developer.download.nvidia.com/compute/redist/jp/v512/pytorch/$TORCH_WHL"
if python -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
    cuda_avail=$(python -c "import torch; print(torch.__version__, torch.cuda.is_available())")
    ok "torch CUDA 동작: $cuda_avail"
else
    warn "  torch 미설치 또는 CPU only — NVIDIA JP5 wheel 자동 다운로드"
    cd /tmp
    if [ ! -f "$TORCH_WHL" ]; then
        wget --tries=3 --timeout=60 "$TORCH_URL" || err "torch wheel 다운로드 실패"
    fi
    pip install "/tmp/$TORCH_WHL" || err "torch wheel 설치 실패"
    python -c "import torch; print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())" \
        || err "torch import 실패"
    ok "torch JP5 wheel 설치 완료"
fi

# ============================================================
# 6. requirements.xavier.txt
# ============================================================
echo ""
echo "[5/8] requirements.xavier.txt 설치 …"
pip install -r "$REPO_ROOT/requirements.xavier.txt"
ok "Python 패키지 OK"

# ============================================================
# 7. numba 패치
# ============================================================
echo ""
echo "[6/8] numba coverage_support 패치 …"
NUMBA_PATCH="$VENV/lib/python3.8/site-packages/numba/misc/coverage_support.py"
if [ -f "$NUMBA_PATCH" ]; then
    cat > "$NUMBA_PATCH" <<EOF
# coverage_support stub — coverage 7.4.x ↔ numba 0.65.x 충돌 우회
def get_registered_loc_notify():
    return []
EOF
    ok "numba 패치 적용"
else
    warn "  numba 미설치 — resemblyzer pip install 후 재실행 필요"
fi

# ============================================================
# 8. ros2_ws + colcon build
# ============================================================
echo ""
echo "[7/8] ros2_ws 셋업 + colcon build …"
mkdir -p "$WS/src"
for pkg in mind_care_vision \
           release/emergency/mind_care_emergency \
           release/emergency/mind_care_api \
           release/vision/mind_care_perception ; do
    name=$(basename "$pkg")
    [ -L "$WS/src/$name" ] || ln -sf "$REPO_ROOT/$pkg" "$WS/src/$name"
done
source /opt/ros/foxy/setup.bash
cd "$WS"
colcon build --symlink-install
ok "colcon build 완료"

# ============================================================
# 9. healthcheck
# ============================================================
echo ""
echo "[8/8] healthcheck …"
source "$WS/install/setup.bash"
bash "$REPO_ROOT/mind_care_vision/scripts/healthcheck.sh"

echo ""
echo "════════════════════════════════════════════════════"
ok "Xavier bootstrap 완료"
echo ""
echo "다음 단계 (수동):"
echo ""
echo "  1. USB 카메라 + 마이크 연결 확인:"
echo "     v4l2-ctl --list-devices"
echo "     python -m sounddevice    # 입력 디바이스 인덱스 확인"
echo ""
echo "  2. ⭐ 화자 등록 (운영 마이크로 첫 enroll):"
echo "     source ~/마음돌봄/.venv-ros/bin/activate"
echo "     python ~/마음돌봄/mind_care_vision/tools/enroll_speaker.py"
echo "     # → 30초 자연스럽게 말하기 → ~/models/speaker.npy"
echo ""
echo "  3. TRT engine 재빌드 (XAVIER_INSTALL_GUIDE.md §8.1, TRT 8.5.2):"
echo "     /usr/src/tensorrt/bin/trtexec --onnx=... --saveEngine=...jetson.engine --fp16"
echo ""
echo "  4. NMS parser .so 재빌드 (§8.2):"
echo "     cd release/vision/.../parser_yolov8_face && make CUDA_VER=11.4"
echo ""
echo "  5. llama.cpp aarch64 빌드 + EXAONE-3.5-7.8B GGUF 다운로드 (§3.4):"
echo "     cd ~/llama.cpp && make clean && make GGML_CUDA=1 -j\$(nproc)"
echo "     mkdir -p ~/models && cd ~/models"
echo "     huggingface-cli download bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \\"
echo "         EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf --local-dir ."
echo "     # 띄울 때: bash scripts/start_llama_server.sh  (기본 exaone 프로필 = 7.8B)"
echo ""
echo "  6. RAG bge-m3 재빌드 (§4.2, 선택 — MiniLM 인덱스 그대로 써도 됨):"
echo "     grep -rn 'Xavier 이전 후' ~/마음돌봄/mind_care_vision/   # 5곳 BAAI/bge-m3 로 교체"
echo "     RESET=1 python tools/build_chroma_disease.py"
echo "     RESET=1 python tools/build_chroma_index.py"
echo ""
echo "  7. 부저 GPIO 구현 (mind_care_emergency/channels/buzzer_channel.py 현재 stub)"
echo ""
echo "  8. 최종 검증:"
echo "     bash ~/마음돌봄/mind_care_vision/scripts/smoke_e2e.sh"
echo "     # 본인 발화 vs 가족 음성 으로 SV 차단 동작 확인"
echo ""
echo "════════════════════════════════════════════════════"
