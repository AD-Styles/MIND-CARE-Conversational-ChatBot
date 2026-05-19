"""emotion_models.py — 표정 분류기 모델 정의 (학습/배포 공용).

train_emotion.py 와 prepare_models.py 가 동일 정의를 import 하기 위함.

`EmotionVGG`
  - 입력 : 1×48×48 GRAY (FER2013/FER+/CK+ 표준)
  - 출력 : 7-class logits  (angry, disgust, fear, happy, neutral, sad, surprise)
  - 파라미터 약 0.6 M, FP16 engine ~1 MB, GTX 1650 Ti 기준 추론 0.2 ms/sample

`MiniXceptionLegacy`
  - 14 K params 의 초기 모델 (호환용으로만 보관)
"""

from __future__ import annotations

import torch.nn as nn

NUM_CLASSES = 7
LABEL_NAMES = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]


# ----------------------------------------------------------------------
# EmotionVGG — 메인 모델
# ----------------------------------------------------------------------
def _cbr(ic: int, oc: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(ic, oc, 3, padding=1, bias=False),
        nn.BatchNorm2d(oc),
        nn.ReLU(inplace=True),
    )


class EmotionVGG(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES, dropout: float = 0.3):
        super().__init__()
        self.features = nn.Sequential(
            _cbr(1, 32),  _cbr(32, 32),   nn.MaxPool2d(2),    # 24×24
            _cbr(32, 64), _cbr(64, 64),   nn.MaxPool2d(2),    # 12×12
            _cbr(64, 128), _cbr(128, 128), nn.MaxPool2d(2),   # 6×6
            _cbr(128, 192), _cbr(192, 192), nn.MaxPool2d(2),  # 3×3
        )
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.dropout = nn.Dropout(dropout)
        self.head = nn.Linear(192, num_classes)

    def forward(self, x):
        x = self.features(x)
        x = self.gap(x).flatten(1)
        return self.head(self.dropout(x))


# ----------------------------------------------------------------------
# MiniXception (legacy 14 K — 첫 학습에 쓰던 것, 호환용)
# ----------------------------------------------------------------------
class _DSC(nn.Module):
    def __init__(self, ic, oc, stride=1):
        super().__init__()
        self.dw = nn.Conv2d(ic, ic, 3, stride, 1, groups=ic, bias=False)
        self.pw = nn.Conv2d(ic, oc, 1, bias=False)
        self.bn = nn.BatchNorm2d(oc)
        self.act = nn.ReLU(inplace=True)
    def forward(self, x):
        return self.act(self.bn(self.pw(self.dw(x))))


class MiniXceptionLegacy(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(1, 8, 3, padding=1, bias=False), nn.BatchNorm2d(8), nn.ReLU(True),
            nn.Conv2d(8, 8, 3, padding=1, bias=False), nn.BatchNorm2d(8), nn.ReLU(True),
        )
        self.blocks = nn.Sequential(
            _DSC(8, 16, 2), _DSC(16, 32, 2), _DSC(32, 64, 2), _DSC(64, 128, 2),
        )
        self.gap  = nn.AdaptiveAvgPool2d(1)
        self.head = nn.Linear(128, num_classes)
    def forward(self, x):
        x = self.stem(x)
        x = self.blocks(x)
        x = self.gap(x).flatten(1)
        return self.head(x)


# ----------------------------------------------------------------------
# 진입점 — train + prepare 공용
# ----------------------------------------------------------------------
def build_model(name: str = "vgg") -> nn.Module:
    if name == "vgg":
        return EmotionVGG()
    if name == "mini":
        return MiniXceptionLegacy()
    raise ValueError(f"unknown model: {name!r}")
