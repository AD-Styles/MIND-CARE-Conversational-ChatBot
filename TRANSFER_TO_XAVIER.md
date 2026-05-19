# WSL → Xavier 전송 가이드

> WSL 에서 압축한 마음돌봄 환경을 Xavier 로 옮기는 방법.

---

## 1. 압축 파일

WSL 의 `$HOME/` 에 생성됨:

| 파일 | 크기 | 내용 | 우선순위 |
|---|---|---|---|
| `마음돌봄_xavier_core_YYYYMMDD_HHMM.tar.zst` | ~78 MB | 코드 + RAG chroma_db + .git + docs + ONNX/.pt | **필수** |
| `마음돌봄_xavier_hfcache_YYYYMMDD_HHMM.tar.zst` | ~5-9 GB | bge-m3 + faster-whisper + MiniLM 모델 캐시 | 선택 (시간 절약) |

> **제외**: venv(3GB, x86_64 → Xavier 재생성), TRT engine/.so(x86_64), GGUF(현재 없음)

---

## 2. 전송 방법 — 3 옵션

### 옵션 A. 같은 LAN 에 있을 때 — **scp 가 가장 빠름** ⭐

```bash
# WSL 에서 실행 (또는 Windows 호스트에서)
XAVIER_IP=<예: 192.168.1.50>

# core 만 (필수)
scp ~/마음돌봄_xavier_core_*.tar.zst eslee03@${XAVIER_IP}:~/

# + HF cache (선택, ~5-9GB)
scp ~/마음돌봄_xavier_hfcache_*.tar.zst eslee03@${XAVIER_IP}:~/
```

ETA:
- 100 Mbps 유선: core 8초, hfcache 12분
- Wi-Fi 5: core 15초, hfcache 25분

### 옵션 B. USB 외장 디스크

```bash
# WSL 에서 — Windows 마운트된 USB 로
cp ~/마음돌봄_xavier_core_*.tar.zst /mnt/d/   # Windows D: 가 USB 면
cp ~/마음돌봄_xavier_hfcache_*.tar.zst /mnt/d/

# Xavier 에서 — USB 마운트
sudo mkdir -p /mnt/usb
sudo mount /dev/sda1 /mnt/usb        # 디바이스명은 dmesg | tail 로 확인
cp /mnt/usb/마음돌봄_xavier_*.tar.zst ~/
sudo umount /mnt/usb
```

### 옵션 C. Windows 측에서 전송

WSL 파일 시스템은 `\\wsl.localhost\Ubuntu\home\eslee03\` 에서 직접 접근됨.
Windows 탐색기로 복사 → USB → Xavier.

또는 Windows 에서 `scp` (PowerShell 또는 Git Bash):
```powershell
scp "\\wsl.localhost\Ubuntu\home\eslee03\마음돌봄_xavier_core_*.tar.zst" `
    eslee03@<xavier-ip>:~/
```

---

## 3. Xavier 측 압축 해제

### 3.1 zstd 설치 확인

```bash
which zstd && zstd --version
# 없으면: sudo apt install -y zstd
```

### 3.2 core 압축 해제 (필수)

```bash
cd ~
tar --use-compress-program=zstd -xf 마음돌봄_xavier_core_*.tar.zst
ls 마음돌봄/                    # mind_care_vision, release, med_data, *.md 등 확인
```

확인:
```bash
cd ~/마음돌봄
git log --oneline | head        # WSL 의 git 히스토리 그대로 보임 (v0.1-wsl-stable 등)
du -sh med_data/chroma_db/      # 96 MB
ls release/vision/models/*/     # .onnx, .pth 있음 (.engine, .so 는 없음 — Xavier 재빌드)
```

### 3.3 HF cache 압축 해제 (선택)

```bash
mkdir -p ~/.cache/huggingface/hub
cd ~/.cache/huggingface/hub
tar --use-compress-program=zstd -xf ~/마음돌봄_xavier_hfcache_*.tar.zst
ls
# models--BAAI--bge-m3, models--Systran--faster-whisper-small, models--...MiniLM... 확인
```

이렇게 하면 Xavier 가 RAG 빌드 + Whisper 모델 로드 시 **재다운로드 안 함**.

---

## 4. 검증

```bash
cd ~/마음돌봄
bash mind_care_vision/scripts/healthcheck.sh
```

기대:
- `[venv-ros]` — venv 없음 ✖ (다음 단계에서 만듦)
- `[모델 파일] GGUF` — EXAONE-3.5-7.8B 없음 ✖ (다음 단계에서 다운로드)
- `[RAG Chroma]` — ✔ (96 MB MiniLM 인덱스 그대로 작동)
- 나머지 ✔

---

## 5. 다음 단계

`XAVIER_INSTALL_GUIDE.md` §5 (Python 환경) 부터 시작:

```bash
cat ~/마음돌봄/XAVIER_INSTALL_GUIDE.md
# §5 → §6 → §7 → ... → §15 완료 체크리스트
```

이미 §6 의 git clone 은 안 해도 됨 (압축 해제로 대체됨).

---

## 6. 정리

전송 후 WSL 에서 압축 파일 삭제:
```bash
rm ~/마음돌봄_xavier_*.tar.zst
```

WSL 자체는 백업으로 그대로 보존 (Xavier 문제 시 fallback).
