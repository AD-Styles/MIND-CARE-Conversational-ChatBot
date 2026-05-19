"""eval_sv.py — 화자 검증 정확도 평가 도구.

사용 예:
    python tools/eval_sv.py --enrolled ~/models/speaker.npy \
        --positive samples/me_*.wav \
        --negative samples/family_*.wav samples/tv_*.wav \
        --threshold 0.75

출력:
    TPR (등록 화자 통과율), TNR (외부 차단율), 평균 score 분포.

WAV 파일은 16kHz mono 권장. 다른 SR 이면 자동 리샘플.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd

TARGET_SR = 16000


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    pcm, sr = sf.read(str(path), always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)  # mono mix
    pcm = pcm.astype(np.float32)
    return pcm, sr


def to_target_sr(pcm: np.ndarray, sr: int) -> np.ndarray:
    if sr == TARGET_SR:
        return pcm
    g = gcd(sr, TARGET_SR)
    return resample_poly(pcm, TARGET_SR // g, sr // g).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enrolled", required=True,
                    help="등록 화자 .npy (enroll_speaker.py 결과)")
    ap.add_argument("--positive", nargs="+", default=[],
                    help="등록 화자 발화 .wav 목록")
    ap.add_argument("--negative", nargs="+", default=[],
                    help="외부 화자/TV 발화 .wav 목록")
    ap.add_argument("--threshold", type=float, default=0.75)
    args = ap.parse_args()

    from resemblyzer import VoiceEncoder, preprocess_wav

    enrolled = np.load(args.enrolled)
    encoder = VoiceEncoder()
    th = args.threshold

    def score_one(wav_path: str) -> float:
        pcm, sr = load_wav(Path(wav_path))
        pcm16k = to_target_sr(pcm, sr)
        try:
            wav = preprocess_wav(pcm16k, source_sr=TARGET_SR)
            embed = encoder.embed_utterance(wav)
            return float(np.dot(embed, enrolled) /
                         (np.linalg.norm(embed) * np.linalg.norm(enrolled)))
        except Exception as exc:
            print(f"  [WARN] {wav_path}: {exc}")
            return 0.0

    pos_scores = [score_one(p) for p in args.positive]
    neg_scores = [score_one(p) for p in args.negative]

    tp = sum(1 for s in pos_scores if s >= th)
    fn = len(pos_scores) - tp
    tn = sum(1 for s in neg_scores if s < th)
    fp = len(neg_scores) - tn

    print()
    print(f"=== 화자 검증 평가 (threshold={th}) ===")
    print(f"  Positive (본인): n={len(pos_scores)}, TP={tp}, FN={fn}")
    if pos_scores:
        print(f"    score mean={np.mean(pos_scores):.3f}, "
              f"min={min(pos_scores):.3f}, max={max(pos_scores):.3f}")
    print(f"  Negative (외부): n={len(neg_scores)}, TN={tn}, FP={fp}")
    if neg_scores:
        print(f"    score mean={np.mean(neg_scores):.3f}, "
              f"min={min(neg_scores):.3f}, max={max(neg_scores):.3f}")

    if pos_scores:
        print(f"\n  TPR (Recall) = {tp / len(pos_scores):.3f}")
    if neg_scores:
        print(f"  TNR (Specificity) = {tn / len(neg_scores):.3f}")

    # 권장 임계값 — Equal Error Rate 근사
    if pos_scores and neg_scores:
        all_scores = sorted(set(pos_scores + neg_scores))
        best_th, best_acc = th, 0.0
        for cand in all_scores:
            tp_c = sum(1 for s in pos_scores if s >= cand)
            tn_c = sum(1 for s in neg_scores if s < cand)
            acc = (tp_c + tn_c) / (len(pos_scores) + len(neg_scores))
            if acc > best_acc:
                best_acc = acc
                best_th = cand
        print(f"\n  추천 임계값: {best_th:.3f} (acc={best_acc:.3f})")

    print()
    return 0 if (fn == 0 and fp == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
