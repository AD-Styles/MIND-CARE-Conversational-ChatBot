"""speaker_verifier.py

resemblyzer 기반 화자 검증 모듈.
- enroll()  : 등록된 화자 임베딩 numpy 파일을 로드
- verify()  : 입력 PCM(float32, 16kHz)과 등록 임베딩의 코사인 유사도를 반환
"""
from __future__ import annotations  # JP5 Py3.8 PEP 585 호환


import numpy as np
from pathlib import Path
from resemblyzer import VoiceEncoder, preprocess_wav


class SpeakerVerifier:
    def __init__(self, embedding_path: str, threshold: float = 0.75):
        self.threshold = threshold
        self.encoder = VoiceEncoder()
        path = Path(embedding_path)
        if not path.exists():
            raise FileNotFoundError(
                f"화자 임베딩 파일 없음: {path}\n"
                f"먼저 tools/enroll_speaker.py 를 실행해 등록하세요."
            )
        self.enrolled_embed = np.load(str(path))

    def verify(self, pcm: np.ndarray, sample_rate: int = 16000) -> tuple[bool, float]:
        """
        pcm: float32 numpy array (16kHz)
        반환: (검증 통과 여부, 코사인 유사도 점수)
        """
        try:
            wav = preprocess_wav(pcm, source_sr=sample_rate)
            embed = self.encoder.embed_utterance(wav)
            score = float(np.dot(embed, self.enrolled_embed) /
                          (np.linalg.norm(embed) * np.linalg.norm(self.enrolled_embed)))
            return score >= self.threshold, round(score, 3)
        except Exception:
            return False, 0.0
