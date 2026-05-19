"""enroll_speaker.py — librosa/numba 없이 scipy만으로 리샘플링

30초간 마이크에서 녹음 → resemblyzer VoiceEncoder 임베딩 → ~/models/speaker.npy 저장.
재실행 시 덮어쓴다.

사용:
    ~/마음돌봄/.venv-ros/bin/python ~/마음돌봄/mind_care_vision/tools/enroll_speaker.py
    --duration 30          : 녹음 시간(초)
    --output  PATH         : 임베딩 저장 경로 (기본 ~/models/speaker.npy)
    --sr      16000        : 마이크 샘플레이트
"""

import argparse
import numpy as np
import sounddevice as sd
from pathlib import Path
from scipy.signal import resample_poly
from math import gcd

TARGET_SR = 16000  # resemblyzer 내부 sampling rate


def resample(pcm: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return pcm
    g = gcd(orig_sr, target_sr)
    return resample_poly(pcm, target_sr // g, orig_sr // g).astype(np.float32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(Path.home() / "models/speaker.npy"))
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--sr", type=int, default=16000)
    parser.add_argument("--device", default=None,
                        help="sounddevice 입력 디바이스 인덱스 또는 이름 (예: 0)")
    args = parser.parse_args()

    device = args.device
    if device is not None:
        try:
            device = int(device)
        except ValueError:
            pass  # name string
        info = sd.query_devices(device, "input")
        print(f"[입력] device={device} → {info['name']} (native_sr={info['default_samplerate']:.0f})")

    print(f"[등록] {args.duration}초간 말씀해 주세요. 아무 말이나 자연스럽게 하시면 됩니다.")
    print("녹음 시작...")
    audio = sd.rec(int(args.duration * args.sr), samplerate=args.sr,
                   channels=1, dtype="float32", device=device)
    sd.wait()
    print("녹음 완료. 임베딩 추출 중...")

    pcm = audio.squeeze()
    wav = resample(pcm, args.sr, TARGET_SR)

    # resemblyzer VoiceEncoder만 사용 (librosa preprocess_wav 우회)
    from resemblyzer import VoiceEncoder
    encoder = VoiceEncoder()
    embed = encoder.embed_utterance(wav)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(out), embed)
    print(f"[완료] 화자 임베딩 저장: {out}")


if __name__ == "__main__":
    main()
